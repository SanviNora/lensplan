"""Planning-model tests. Solutions are re-checked against constraints independently of the solver."""

from __future__ import annotations

import math

import pytest

from app.opt.heuristic import FixedSetupEvaluator, anneal, greedy_initial_pattern
from app.opt.model import TOLERANCE, PlanningModel, solve_plan
from app.opt.scenario import DEFAULT_SIZE, ScenarioSize, build_scenario
from app.opt.solvers import available_solvers
from app.opt.types import ProblemData, SolveStatus, SolverName

# Small enough that HiGHS proves optimality without a Gurobi license.
SMALL = ScenarioSize(skus=5, plants=2, lines_per_plant=1, dcs=2, periods=4)

HAS_GUROBI = SolverName.GUROBI in available_solvers()
gurobi_only = pytest.mark.skipif(HAS_GUROBI is False, reason="gurobi licence not available")


@pytest.fixture(scope="module")
def small_data() -> ProblemData:
    return build_scenario(SMALL, seed=3)


@pytest.fixture(scope="module")
def small_solution(small_data):
    return solve_plan(small_data, SolverName.HIGHS, time_limit=60)


# --------------------------------------------------------------------- problem data


def test_scenario_is_deterministic():
    assert build_scenario(SMALL, seed=3).demand == build_scenario(SMALL, seed=3).demand


def test_default_scenario_fits_the_gurobi_trial_licence():
    model = PlanningModel(build_scenario(DEFAULT_SIZE)).model
    assert model.num_vars < 2000
    assert model.num_constraints < 2000


def test_validation_rejects_a_sku_no_line_can_build(small_data):
    broken = build_scenario(SMALL, seed=3)
    orphan = broken.skus[0].id
    kept = [c for c in broken.capabilities if c.sku_id != orphan]
    with pytest.raises(ValueError, match="cannot be produced"):
        ProblemData(
            periods=broken.periods,
            skus=broken.skus,
            plants=broken.plants,
            lines=broken.lines,
            dcs=broken.dcs,
            capabilities=kept,
            lanes=broken.lanes,
            demand=broken.demand,
            line_hours=broken.line_hours,
        )


def test_validation_rejects_an_unreachable_dc(small_data):
    base = build_scenario(SMALL, seed=3)
    stranded = base.dcs[-1].id
    with pytest.raises(ValueError, match="unreachable"):
        ProblemData(
            periods=base.periods,
            skus=base.skus,
            plants=base.plants,
            lines=base.lines,
            dcs=base.dcs,
            capabilities=base.capabilities,
            lanes=[lane for lane in base.lanes if lane.dc_id != stranded],
            demand=base.demand,
            line_hours=base.line_hours,
        )


# --------------------------------------------------------------------- solution validity


def test_solution_is_optimal(small_solution):
    assert small_solution.status is SolveStatus.OPTIMAL


def test_cost_breakdown_reconciles_with_the_objective(small_solution):
    """The single most important test in this file.

    If the reported production, changeover, holding, freight and stockout costs do not
    sum to the objective the solver minimised, then the plan shown to a planner is not
    the plan that was optimised.
    """
    assert small_solution.costs.total == pytest.approx(small_solution.objective, abs=1e-4)


def test_no_line_exceeds_its_available_hours(small_data, small_solution):
    used: dict[tuple[str, int], float] = {}
    for entry in small_solution.production:
        used[(entry.line_id, entry.period)] = (
            used.get((entry.line_id, entry.period), 0.0) + entry.hours
        )
    for (line_id, period), hours in used.items():
        assert hours <= small_data.hours_for(line_id, period) + 1e-6


def test_inventory_balance_holds_at_every_dc(small_data, small_solution):
    """Recompute the flow conservation the model claims to enforce.

    closing = opening + received - (demand - unmet)
    """
    received: dict[tuple[str, str, int], float] = {}
    for shipment in small_solution.shipments:
        key = (shipment.dc_id, shipment.sku_id, shipment.period)
        received[key] = received.get(key, 0.0) + shipment.units

    reported = {
        (e.dc_id, e.sku_id, e.period): e for e in small_solution.inventory
    }

    for dc in small_data.dcs:
        for sku in small_data.skus:
            carried = small_data.opening_inventory.get((dc.id, sku.id), 0.0)
            for period in small_data.periods:
                key = (dc.id, sku.id, period)
                entry = reported.get(key)
                closing = entry.closing_units if entry else 0.0
                unmet = entry.unmet_units if entry else 0.0
                expected = (
                    carried
                    + received.get(key, 0.0)
                    - small_data.demand_for(dc.id, sku.id, period)
                    + unmet
                )
                assert closing == pytest.approx(expected, abs=1e-3), f"balance broken at {key}"
                carried = closing


def test_plants_ship_exactly_what_they_produce(small_data, small_solution):
    produced: dict[tuple[str, str, int], float] = {}
    line_plant = {line.id: line.plant_id for line in small_data.lines}
    for entry in small_solution.production:
        key = (line_plant[entry.line_id], entry.sku_id, entry.period)
        produced[key] = produced.get(key, 0.0) + entry.units

    shipped: dict[tuple[str, str, int], float] = {}
    for shipment in small_solution.shipments:
        key = (shipment.plant_id, shipment.sku_id, shipment.period)
        shipped[key] = shipped.get(key, 0.0) + shipment.units

    for key in set(produced) | set(shipped):
        assert produced.get(key, 0.0) == pytest.approx(shipped.get(key, 0.0), abs=1e-3)


def test_minimum_run_length_is_respected(small_data, small_solution):
    for entry in small_solution.production:
        if entry.units <= TOLERANCE:
            continue
        capability = small_data.capability(entry.line_id, entry.sku_id)
        assert entry.units >= capability.min_run_units - 1e-6


def test_line_variety_limit_is_respected(small_data, small_solution):
    limit = small_data.max_skus_per_line_period
    assert limit is not None
    counts: dict[tuple[str, int], int] = {}
    for entry in small_solution.production:
        if entry.units <= TOLERANCE:
            continue
        key = (entry.line_id, entry.period)
        counts[key] = counts.get(key, 0) + 1
    assert counts, "expected at least one production run"
    assert max(counts.values()) <= limit


def test_unmet_demand_never_exceeds_demand(small_data, small_solution):
    for entry in small_solution.inventory:
        demanded = small_data.demand_for(entry.dc_id, entry.sku_id, entry.period)
        assert entry.unmet_units <= demanded + 1e-6


def test_service_level_is_a_fraction(small_solution):
    assert 0.0 <= small_solution.service_level <= 1.0


# --------------------------------------------------------------------- big-M regression


def test_tightened_big_m_does_not_cut_off_the_optimum(small_data):
    """Regression for a bound that silently removed the true optimum.

    The production upper bound nets opening inventory off remaining demand. Doing that in
    every period rather than only the first one double-counts stock that has already been
    consumed, which tightens the bound below what the plan legitimately needs and raises
    the objective. The failure is quiet: the solver still reports OPTIMAL, just for a
    smaller feasible region.

    Guard by solving the same scenario with a deliberately loose bound and requiring the
    objectives to agree.
    """
    tightened = solve_plan(small_data, SolverName.HIGHS, time_limit=60, with_sensitivity=False)

    class LooseModel(PlanningModel):
        def _useful_production_bound(self, sku_id: str, period: int) -> float:
            return float("inf")

    loose = LooseModel(small_data).solve(
        SolverName.HIGHS, time_limit=60, with_sensitivity=False
    )

    assert tightened.status is SolveStatus.OPTIMAL
    assert loose.status is SolveStatus.OPTIMAL
    assert tightened.objective == pytest.approx(loose.objective, rel=1e-6)


# --------------------------------------------------------------------- sensitivity


def test_shadow_prices_are_non_negative_and_ordered(small_solution):
    prices = small_solution.shadow_prices
    assert prices
    assert all(p.shadow_price >= 0 for p in prices)
    assert prices == sorted(prices, key=lambda p: p.shadow_price, reverse=True)


def test_a_priced_hour_belongs_to_a_line_saturated_in_the_relaxation(small_solution):
    """An hour only carries value on a line the relaxation has run out of.

    The comparison has to be against the relaxation, not the executed plan. A line can be
    at 87% in the integer solution and still be priced, because minimum run lengths and
    the variety limit strand hours the relaxation was free to use. Asserting against the
    MIP's utilisation here fails for a correct model, which is how the mismatch between
    the two numbers was found in the first place.
    """
    priced = [p for p in small_solution.shadow_prices if p.shadow_price > 1e-6]
    assert priced, "expected at least one binding capacity constraint"
    for entry in priced:
        assert entry.binding_in_relaxation, (
            f"{entry.line_id} p{entry.period} priced at {entry.shadow_price:.2f} but the "
            f"relaxation only used {entry.relaxed_utilization:.1%} of its hours"
        )


def test_an_unpriced_line_has_slack_in_the_relaxation(small_solution):
    for entry in small_solution.shadow_prices:
        if entry.shadow_price <= 1e-6:
            assert entry.relaxed_utilization <= 1.0 + 1e-6


# --------------------------------------------------------------------- heuristic


def test_annealing_seed_is_feasible(small_data):
    """Regression: the LP-rounded seed used to breach the line-variety limit.

    When that happened the seed cost was infinity, every candidate looked like an
    improvement, and simulated annealing degenerated into a random walk that still
    returned a plausible-looking answer roughly 9% off the optimum.
    """
    evaluator = FixedSetupEvaluator(small_data, SolverName.HIGHS)
    pattern = greedy_initial_pattern(small_data, evaluator)

    limit = small_data.max_skus_per_line_period
    counts: dict[tuple[str, int], int] = {}
    for (line_id, _sku, period), enabled in pattern.items():
        if enabled:
            counts[(line_id, period)] = counts.get((line_id, period), 0) + 1
    assert not counts or max(counts.values()) <= limit

    cost, _ = evaluator.evaluate(pattern)
    assert math.isfinite(cost)


def test_annealing_improves_on_its_starting_point(small_data):
    result = anneal(small_data, solver=SolverName.HIGHS, iterations=150, seed=5, time_budget=60)
    assert math.isfinite(result.cost_trace[0])
    assert result.best_cost <= result.cost_trace[0] + 1e-9
    assert result.iterations > 0


def test_annealing_lands_near_the_exact_optimum(small_data):
    exact = solve_plan(small_data, SolverName.HIGHS, time_limit=90, with_sensitivity=False)
    result = anneal(small_data, solver=SolverName.HIGHS, iterations=400, seed=11, time_budget=90)
    gap = (result.best_cost - exact.objective) / exact.objective
    assert gap >= -1e-6, "heuristic beat the proven optimum, so the exact model is wrong"
    assert gap < 0.05, f"heuristic gap {gap:.2%} is worse than the 5% tolerance"


def test_heuristic_plan_costs_reconcile(small_data):
    result = anneal(small_data, solver=SolverName.HIGHS, iterations=100, seed=2, time_budget=60)
    assert result.solution.costs.total == pytest.approx(result.best_cost, abs=1e-4)


# --------------------------------------------------------------------- solver parity


@gurobi_only
def test_both_solvers_agree_on_the_optimum(small_data):
    highs = solve_plan(small_data, SolverName.HIGHS, time_limit=90, with_sensitivity=False)
    gurobi = solve_plan(small_data, SolverName.GUROBI, time_limit=90, with_sensitivity=False)
    assert highs.objective == pytest.approx(gurobi.objective, rel=1e-4)


@gurobi_only
def test_both_solvers_agree_on_the_binding_line(small_data):
    highs = solve_plan(small_data, SolverName.HIGHS, time_limit=90)
    gurobi = solve_plan(small_data, SolverName.GUROBI, time_limit=90)
    assert highs.shadow_prices[0].line_id == gurobi.shadow_prices[0].line_id
    assert highs.shadow_prices[0].period == gurobi.shadow_prices[0].period


# --------------------------------------------------------------------- degenerate cases


def test_zero_capacity_leaves_all_demand_unmet():
    data = build_scenario(SMALL, seed=3, capacity_scale=0.0)
    solution = solve_plan(data, SolverName.HIGHS, time_limit=60, with_sensitivity=False)
    assert solution.status is SolveStatus.OPTIMAL
    assert not solution.production
    assert solution.service_level < 0.5


def test_a_demand_surge_costs_more_than_the_base_case(small_data):
    base = solve_plan(small_data, SolverName.HIGHS, time_limit=60, with_sensitivity=False)
    surged = solve_plan(
        build_scenario(SMALL, seed=3, demand_surge=1.6),
        SolverName.HIGHS,
        time_limit=60,
        with_sensitivity=False,
    )
    assert surged.objective > base.objective

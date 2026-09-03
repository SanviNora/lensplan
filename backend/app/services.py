"""Map persisted scenarios onto optimizer dataclasses."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .heuristic_types import RunMethod
from .models import (
    PlanRun,
    RunBottleneck,
    RunInventory,
    RunProduction,
    RunShipment,
    Scenario,
    ScenarioOpeningInventory,
)
from .opt.heuristic import anneal
from .opt.model import solve_plan
from .opt.types import (
    DistributionCenter,
    FreightLane,
    Line,
    LineCapability,
    Plant,
    ProblemData,
    Sku,
    Solution,
    SolverName,
)


class ScenarioNotFound(LookupError):
    pass


def load_problem(db: Session, scenario_id: int) -> tuple[Scenario, ProblemData]:
    scenario = db.scalar(
        select(Scenario)
        .where(Scenario.id == scenario_id)
        .options(
            selectinload(Scenario.skus),
            selectinload(Scenario.plants),
            selectinload(Scenario.lines),
            selectinload(Scenario.dcs),
            selectinload(Scenario.capabilities),
            selectinload(Scenario.lanes),
            selectinload(Scenario.demand_rows),
            selectinload(Scenario.capacity_rows),
        )
    )
    if scenario is None:
        raise ScenarioNotFound(f"scenario {scenario_id} does not exist")

    opening = db.scalars(
        select(ScenarioOpeningInventory).where(
            ScenarioOpeningInventory.scenario_id == scenario_id
        )
    ).all()

    data = ProblemData(
        periods=list(range(1, scenario.periods + 1)),
        skus=[
            Sku(
                id=s.code,
                name=s.name,
                family=s.family,
                holding_cost=s.holding_cost,
                stockout_penalty=s.stockout_penalty,
                shelf_life_periods=s.shelf_life_periods,
            )
            for s in scenario.skus
        ],
        plants=[Plant(id=p.code, name=p.name) for p in scenario.plants],
        lines=[
            Line(id=line.code, plant_id=line.plant_code, name=line.name)
            for line in scenario.lines
        ],
        dcs=[DistributionCenter(id=d.code, name=d.name) for d in scenario.dcs],
        capabilities=[
            LineCapability(
                line_id=c.line_code,
                sku_id=c.sku_code,
                units_per_hour=c.units_per_hour,
                unit_cost=c.unit_cost,
                changeover_cost=c.changeover_cost,
                min_run_units=c.min_run_units,
            )
            for c in scenario.capabilities
        ],
        lanes=[
            FreightLane(plant_id=lane.plant_code, dc_id=lane.dc_code, cost_per_unit=lane.cost_per_unit)
            for lane in scenario.lanes
        ],
        demand={
            (row.dc_code, row.sku_code, row.period): row.units
            for row in scenario.demand_rows
        },
        line_hours={
            (row.line_code, row.period): row.hours for row in scenario.capacity_rows
        },
        opening_inventory={(row.dc_code, row.sku_code): row.units for row in opening},
        max_skus_per_line_period=scenario.max_skus_per_line_period,
    )
    return scenario, data


def run_plan(
    db: Session,
    scenario_id: int,
    *,
    method: RunMethod,
    solver: SolverName,
    label: str,
    created_by: str,
    time_limit: float | None = None,
    mip_gap: float | None = None,
    iterations: int = 400,
    seed: int = 11,
) -> PlanRun:
    settings = get_settings()
    _scenario, data = load_problem(db, scenario_id)

    limit = min(time_limit or settings.default_solve_seconds, settings.max_solve_seconds)
    gap = mip_gap if mip_gap is not None else settings.default_mip_gap
    capped_iterations = min(iterations, settings.max_anneal_iterations)

    performed_iterations: int | None = None
    if method is RunMethod.EXACT:
        solution: Solution = solve_plan(
            data, solver, time_limit=limit, mip_gap=gap, with_sensitivity=True
        )
    else:
        result = anneal(
            data,
            solver=solver,
            iterations=capped_iterations,
            seed=seed,
            time_budget=limit,
        )
        solution = result.solution
        performed_iterations = result.iterations

    run = PlanRun(
        scenario_id=scenario_id,
        label=label,
        method=method.value,
        solver=solver.value,
        status=solution.status.value,
        objective=solution.objective,
        cost_production=solution.costs.production,
        cost_changeover=solution.costs.changeover,
        cost_holding=solution.costs.holding,
        cost_freight=solution.costs.freight,
        cost_stockout=solution.costs.stockout,
        service_level=solution.service_level,
        unmet_units=solution.unmet_units,
        total_demand_units=solution.total_demand_units,
        solve_seconds=solution.solve_seconds,
        mip_gap=solution.mip_gap,
        iterations=performed_iterations,
        created_by=created_by,
    )
    db.add(run)
    db.flush()

    db.add_all(
        RunProduction(
            run_id=run.id,
            line_code=e.line_id,
            sku_code=e.sku_id,
            period=e.period,
            units=e.units,
            hours=e.hours,
            changeover=e.changeover,
        )
        for e in solution.production
    )
    db.add_all(
        RunShipment(
            run_id=run.id,
            plant_code=e.plant_id,
            dc_code=e.dc_id,
            sku_code=e.sku_id,
            period=e.period,
            units=e.units,
        )
        for e in solution.shipments
    )
    db.add_all(
        RunInventory(
            run_id=run.id,
            dc_code=e.dc_id,
            sku_code=e.sku_id,
            period=e.period,
            closing_units=e.closing_units,
            unmet_units=e.unmet_units,
        )
        for e in solution.inventory
    )
    db.add_all(
        RunBottleneck(
            run_id=run.id,
            line_code=p.line_id,
            period=p.period,
            shadow_price=p.shadow_price,
            hours_available=p.hours_available,
            hours_used=p.hours_used,
            hours_used_relaxed=p.hours_used_relaxed,
        )
        # Skip unpriced line-periods.
        for p in solution.shadow_prices
        if p.shadow_price > 1e-6
    )

    db.commit()
    db.refresh(run)
    return run


def compare_runs(baseline: PlanRun, candidate: PlanRun) -> dict:
    """Difference between two runs, framed as what changed and what it is worth."""

    def delta(a: float, b: float) -> dict:
        change = b - a
        return {
            "baseline": round(a, 2),
            "candidate": round(b, 2),
            "delta": round(change, 2),
            "pct": round(change / a * 100, 3) if a else None,
        }

    return {
        "baseline_run_id": baseline.id,
        "candidate_run_id": candidate.id,
        "total_cost": delta(baseline.objective, candidate.objective),
        "production": delta(baseline.cost_production, candidate.cost_production),
        "changeover": delta(baseline.cost_changeover, candidate.cost_changeover),
        "holding": delta(baseline.cost_holding, candidate.cost_holding),
        "freight": delta(baseline.cost_freight, candidate.cost_freight),
        "stockout": delta(baseline.cost_stockout, candidate.cost_stockout),
        "service_level": delta(baseline.service_level, candidate.service_level),
        "solve_seconds": delta(baseline.solve_seconds, candidate.solve_seconds),
    }

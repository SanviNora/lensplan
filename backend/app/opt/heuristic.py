"""Simulated annealing over binary setups; each candidate is costed as an LP."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from .model import PlanningModel
from .solvers import LinExpr, Model
from .types import ProblemData, Solution, SolverName, SolveStatus

SetupPattern = dict[tuple[str, str, int], bool]


@dataclass
class AnnealingResult:
    solution: Solution
    best_cost: float
    iterations: int
    accepted: int
    improved: int
    seconds: int | float
    cost_trace: list[float]


class FixedSetupEvaluator:
    """Costs a setup pattern by solving the remaining linear program exactly.

    The MILP is rebuilt once and only the setup columns are re-bounded between
    evaluations, so each candidate costs one LP solve rather than a full model rebuild.
    """

    def __init__(self, data: ProblemData, solver: SolverName) -> None:
        self.data = data
        self.solver = solver
        self.planning = PlanningModel(data)
        self._evaluations = 0

    @property
    def evaluations(self) -> int:
        return self._evaluations

    def keys(self) -> list[tuple[str, str, int]]:
        return list(self.planning.index.setup.keys())

    def evaluate(self, pattern: SetupPattern, *, want_solution: bool = False):
        """Return (cost, solution or None) for a fixed setup pattern."""
        model = self.planning.model
        for key, column in self.planning.index.setup.items():
            value = 1.0 if pattern.get(key, False) else 0.0
            model._lower[column] = value
            model._upper[column] = value

        result = model.solve(self.solver, time_limit=15.0, relax_binaries=True)
        self._evaluations += 1

        if not result.has_solution:
            return math.inf, None
        if want_solution:
            solution = self.planning.to_solution(result, self.solver)
            solution.status = SolveStatus.FEASIBLE
            return result.objective, solution
        return result.objective, None


def greedy_initial_pattern(data: ProblemData, evaluator: FixedSetupEvaluator) -> SetupPattern:
    """Seed the search from the LP relaxation.

    Relaxing the binaries and rounding anything the relaxation switched meaningfully on
    starts the search near the region the exact model cares about, which is a far better
    origin than a random pattern.
    """
    model = evaluator.planning.model
    for column in evaluator.planning.index.setup.values():
        model._lower[column] = 0.0
        model._upper[column] = 1.0

    relaxed = model.solve(evaluator.solver, time_limit=20.0, relax_binaries=True)
    pattern: SetupPattern = {key: False for key in evaluator.planning.index.setup}
    if not relaxed.values:
        return pattern

    # Rounding every fractional setup up breaches the SKU-per-line limit.
    limit = data.max_skus_per_line_period
    strength: dict[tuple[str, int], list[tuple[float, tuple[str, str, int]]]] = {}
    for key, column in evaluator.planning.index.setup.items():
        value = relaxed.values[column]
        if value > 1e-4:
            strength.setdefault((key[0], key[2]), []).append((value, key))

    for group in strength.values():
        group.sort(reverse=True)
        keep = group if limit is None else group[:limit]
        for _, key in keep:
            pattern[key] = True
    return pattern


def _neighbour(
    pattern: SetupPattern,
    keys: list[tuple[str, str, int]],
    rng: random.Random,
    max_skus_per_line_period: int | None,
) -> SetupPattern:
    """Flip one setup, or move a SKU between two lines in the same period.

    The move set matters more than the cooling schedule here. A pure flip drifts toward
    patterns that violate the line-variety limit; the relocation move keeps the number of
    active setups roughly constant while still changing which line carries which SKU.
    """
    candidate = dict(pattern)
    if rng.random() < 0.65:
        key = rng.choice(keys)
        candidate[key] = not candidate.get(key, False)
    else:
        active = [k for k, on in candidate.items() if on]
        if not active:
            candidate[rng.choice(keys)] = True
            return candidate
        line_id, sku_id, period = rng.choice(active)
        alternatives = [
            k
            for k in keys
            if k[1] == sku_id and k[2] == period and k[0] != line_id
        ]
        if alternatives:
            candidate[(line_id, sku_id, period)] = False
            candidate[rng.choice(alternatives)] = True
        else:
            candidate[(line_id, sku_id, period)] = False

    if max_skus_per_line_period:
        # Drop extras to stay inside the variety limit.
        by_line_period: dict[tuple[str, int], list[tuple[str, str, int]]] = {}
        for key, on in candidate.items():
            if on:
                by_line_period.setdefault((key[0], key[2]), []).append(key)
        for group in by_line_period.values():
            excess = len(group) - max_skus_per_line_period
            if excess > 0:
                for key in rng.sample(group, excess):
                    candidate[key] = False
    return candidate


def anneal(
    data: ProblemData,
    *,
    solver: SolverName = SolverName.HIGHS,
    iterations: int = 250,
    initial_temperature: float | None = None,
    cooling: float = 0.97,
    seed: int = 11,
    time_budget: float = 90.0,
) -> AnnealingResult:
    rng = random.Random(seed)
    evaluator = FixedSetupEvaluator(data, solver)
    keys = evaluator.keys()

    current = greedy_initial_pattern(data, evaluator)
    current_cost, _ = evaluator.evaluate(current)
    if not math.isfinite(current_cost):
        # Empty pattern: all demand unmet, finite cost.
        current = {key: False for key in keys}
        current_cost, _ = evaluator.evaluate(current)
    if not math.isfinite(current_cost):
        raise RuntimeError("no costable starting pattern; scenario is malformed")

    best_pattern = dict(current)
    best_cost = current_cost

    # Temperature scaled to the objective so the schedule is network-size independent.
    temperature = (
        initial_temperature
        if initial_temperature is not None
        else max(1.0, abs(current_cost) * 0.02)
    )

    started = time.perf_counter()
    accepted = 0
    improved = 0
    trace = [current_cost]
    performed = 0

    for _ in range(iterations):
        if time.perf_counter() - started > time_budget:
            break
        performed += 1
        candidate = _neighbour(current, keys, rng, data.max_skus_per_line_period)
        candidate_cost, _ = evaluator.evaluate(candidate)
        delta = candidate_cost - current_cost

        if delta <= 0 or (
            math.isfinite(candidate_cost)
            and temperature > 0
            and rng.random() < math.exp(-delta / temperature)
        ):
            current, current_cost = candidate, candidate_cost
            accepted += 1
            if current_cost < best_cost - 1e-9:
                best_pattern, best_cost = dict(current), current_cost
                improved += 1

        trace.append(current_cost)
        temperature *= cooling

    _, solution = evaluator.evaluate(best_pattern, want_solution=True)
    elapsed = time.perf_counter() - started
    if solution is None:
        raise RuntimeError("annealing finished without a costable plan")
    solution.solve_seconds = elapsed

    return AnnealingResult(
        solution=solution,
        best_cost=best_cost,
        iterations=performed,
        accepted=accepted,
        improved=improved,
        seconds=elapsed,
        cost_trace=trace,
    )

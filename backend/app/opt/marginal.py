"""MIP re-solve of extra line-hours. Do not quote LP duals as cash."""

from __future__ import annotations

from dataclasses import dataclass

from .model import solve_plan
from .types import ProblemData, Solution, SolverName


@dataclass(frozen=True)
class MarginalValue:
    line_id: str
    period: int
    hours_added: float
    lp_shadow_price: float
    realised_value_per_hour: float
    total_saving: float
    solver_seconds: float

    @property
    def dual_overstatement(self) -> float:
        """How many times larger the LP dual is than the value actually realised."""
        if self.realised_value_per_hour <= 1e-9:
            return float("inf") if self.lp_shadow_price > 1e-9 else 1.0
        return self.lp_shadow_price / self.realised_value_per_hour

    @property
    def worth_buying_below(self) -> float:
        """The most an hour on this line is worth paying for."""
        return max(0.0, self.realised_value_per_hour)


def evaluate_marginal_capacity(
    data: ProblemData,
    baseline: Solution,
    *,
    solver: SolverName = SolverName.HIGHS,
    top_n: int = 3,
    hours_added: float = 1.0,
    time_limit: float = 60.0,
) -> list[MarginalValue]:
    """Re-solve with extra hours on the highest-priced lines and measure the saving.

    Only lines the relaxation already priced are tested. A line with a zero dual has slack
    in the relaxation, so extra hours there cannot help the integer plan either.
    """
    candidates = [p for p in baseline.shadow_prices if p.shadow_price > 1e-6][:top_n]
    results: list[MarginalValue] = []

    for candidate in candidates:
        probe = ProblemData(
            periods=list(data.periods),
            skus=list(data.skus),
            plants=list(data.plants),
            lines=list(data.lines),
            dcs=list(data.dcs),
            capabilities=list(data.capabilities),
            lanes=list(data.lanes),
            demand=dict(data.demand),
            line_hours=dict(data.line_hours),
            opening_inventory=dict(data.opening_inventory),
            max_skus_per_line_period=data.max_skus_per_line_period,
        )
        key = (candidate.line_id, candidate.period)
        probe.line_hours[key] = probe.line_hours.get(key, 0.0) + hours_added

        probed = solve_plan(
            probe, solver, time_limit=time_limit, with_sensitivity=False
        )
        saving = baseline.objective - probed.objective
        results.append(
            MarginalValue(
                line_id=candidate.line_id,
                period=candidate.period,
                hours_added=hours_added,
                lp_shadow_price=candidate.shadow_price,
                realised_value_per_hour=saving / hours_added if hours_added else 0.0,
                total_saving=saving,
                solver_seconds=probed.solve_seconds,
            )
        )

    results.sort(key=lambda r: r.realised_value_per_hour, reverse=True)
    return results

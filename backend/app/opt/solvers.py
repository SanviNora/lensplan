"""Shared modelling layer over Gurobi and HiGHS. HiGHS is the default in CI."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .types import SolveStatus, SolverName


@dataclass(frozen=True)
class Var:
    """Handle to a decision variable. `index` is the column position in the model."""

    index: int
    name: str
    is_binary: bool


class LinExpr:
    """Sparse linear expression: sum of coefficient * variable, plus a constant."""

    __slots__ = ("terms", "constant")

    def __init__(
        self,
        terms: dict[int, float] | None = None,
        constant: float = 0.0,
    ) -> None:
        self.terms: dict[int, float] = terms or {}
        self.constant = constant

    @staticmethod
    def of(var: Var, coefficient: float = 1.0) -> "LinExpr":
        return LinExpr({var.index: coefficient})

    def add(self, var: Var, coefficient: float = 1.0) -> "LinExpr":
        if coefficient:
            self.terms[var.index] = self.terms.get(var.index, 0.0) + coefficient
        return self

    def add_constant(self, value: float) -> "LinExpr":
        self.constant += value
        return self

    def merge(self, other: "LinExpr", scale: float = 1.0) -> "LinExpr":
        for index, coefficient in other.terms.items():
            self.terms[index] = self.terms.get(index, 0.0) + scale * coefficient
        self.constant += scale * other.constant
        return self

    def is_empty(self) -> bool:
        return not any(self.terms.values())


class SolverUnavailable(RuntimeError):
    pass


class Model:
    """Solver-agnostic MILP container.

    Variables and constraints are accumulated in plain Python structures and only handed
    to a solver on `solve`. Building the model is therefore free of solver imports, which
    is what allows the formulation to be unit tested without any solver installed.
    """

    def __init__(self, name: str = "model") -> None:
        self.name = name
        self._lower: list[float] = []
        self._upper: list[float] = []
        self._binary: list[bool] = []
        self._names: list[str] = []
        self._obj: dict[int, float] = {}
        # Each constraint is (expression terms, lower bound, upper bound, name).
        self._rows: list[tuple[dict[int, float], float, float, str]] = []

    @property
    def num_vars(self) -> int:
        return len(self._lower)

    @property
    def num_constraints(self) -> int:
        return len(self._rows)

    def add_var(
        self,
        name: str,
        lower: float = 0.0,
        upper: float = float("inf"),
        binary: bool = False,
    ) -> Var:
        index = len(self._lower)
        self._lower.append(0.0 if binary else lower)
        self._upper.append(1.0 if binary else upper)
        self._binary.append(binary)
        self._names.append(name)
        return Var(index=index, name=name, is_binary=binary)

    def set_objective_term(self, var: Var, coefficient: float) -> None:
        if coefficient:
            self._obj[var.index] = self._obj.get(var.index, 0.0) + coefficient

    def add_constraint(
        self,
        expr: LinExpr,
        *,
        lower: float | None = None,
        upper: float | None = None,
        name: str = "",
    ) -> None:
        """Add `lower <= expr <= upper`. The expression constant is folded into bounds."""
        if lower is None and upper is None:
            raise ValueError("constraint needs at least one bound")
        lo = -float("inf") if lower is None else lower - expr.constant
        hi = float("inf") if upper is None else upper - expr.constant
        if lo > hi:
            raise ValueError(f"constraint {name} has crossed bounds: {lo} > {hi}")
        self._rows.append((dict(expr.terms), lo, hi, name))

    def solve(
        self,
        solver: SolverName,
        *,
        time_limit: float = 60.0,
        mip_gap: float = 1e-4,
        relax_binaries: bool = False,
    ) -> "SolveResult":
        if solver is SolverName.GUROBI:
            return self._solve_gurobi(
                time_limit=time_limit, mip_gap=mip_gap, relax_binaries=relax_binaries
            )
        return self._solve_highs(
            time_limit=time_limit, mip_gap=mip_gap, relax_binaries=relax_binaries
        )

    def _solve_highs(
        self, *, time_limit: float, mip_gap: float, relax_binaries: bool
    ) -> "SolveResult":
        try:
            import highspy
        except ImportError as exc:  # pragma: no cover - environment guard
            raise SolverUnavailable("highspy is not installed") from exc

        started = time.perf_counter()
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("time_limit", time_limit)
        h.setOptionValue("mip_rel_gap", mip_gap)

        inf = highspy.kHighsInf
        num_vars = self.num_vars
        lower = [-inf if lo == -float("inf") else lo for lo in self._lower]
        upper = [inf if hi == float("inf") else hi for hi in self._upper]
        costs = [self._obj.get(i, 0.0) for i in range(num_vars)]
        h.addVars(num_vars, lower, upper)
        h.changeColsCost(num_vars, list(range(num_vars)), costs)

        for i, name in enumerate(self._names):
            h.passColName(i, name)

        if not relax_binaries:
            integral = [i for i, is_bin in enumerate(self._binary) if is_bin]
            if integral:
                h.changeColsIntegrality(
                    len(integral),
                    integral,
                    [highspy.HighsVarType.kInteger] * len(integral),
                )

        for terms, lo, hi, name in self._rows:
            indices = list(terms.keys())
            values = [terms[i] for i in indices]
            h.addRow(
                -inf if lo == -float("inf") else lo,
                inf if hi == float("inf") else hi,
                len(indices),
                indices,
                values,
            )

        h.run()
        elapsed = time.perf_counter() - started

        model_status = h.getModelStatus()
        status = _HIGHS_STATUS.get(str(h.modelStatusToString(model_status)).lower(), SolveStatus.ERROR)

        if status in (SolveStatus.INFEASIBLE, SolveStatus.UNBOUNDED, SolveStatus.ERROR):
            return SolveResult(status=status, objective=0.0, values=[], duals=[], seconds=elapsed)

        solution = h.getSolution()
        values = list(solution.col_value)
        duals: list[float] = []
        if relax_binaries:
            duals = list(solution.row_dual)

        info = h.getInfo()
        gap = getattr(info, "mip_gap", None)
        objective = h.getObjectiveValue()
        return SolveResult(
            status=status,
            objective=objective,
            values=values,
            duals=duals,
            seconds=elapsed,
            mip_gap=None if relax_binaries else gap,
        )

    def _solve_gurobi(
        self, *, time_limit: float, mip_gap: float, relax_binaries: bool
    ) -> "SolveResult":
        try:
            import gurobipy as gp
            from gurobipy import GRB
        except ImportError as exc:  # pragma: no cover - environment guard
            raise SolverUnavailable("gurobipy is not installed") from exc

        started = time.perf_counter()
        try:
            env = gp.Env(params={"OutputFlag": 0})
            model = gp.Model(self.name, env=env)
            model.Params.TimeLimit = time_limit
            model.Params.MIPGap = mip_gap

            variables = []
            for i in range(self.num_vars):
                vtype = (
                    GRB.BINARY if (self._binary[i] and not relax_binaries) else GRB.CONTINUOUS
                )
                variables.append(
                    model.addVar(
                        lb=self._lower[i],
                        ub=self._upper[i],
                        obj=self._obj.get(i, 0.0),
                        vtype=vtype,
                        name=self._names[i],
                    )
                )
            model.ModelSense = GRB.MINIMIZE

            # One Gurobi row per logical constraint so duals align with HiGHS.
            rows = []
            for terms, lo, hi, name in self._rows:
                expr = gp.quicksum(coef * variables[i] for i, coef in terms.items())
                has_lo = lo != -float("inf")
                has_hi = hi != float("inf")
                if lo == hi:
                    rows.append(model.addConstr(expr == lo, name=name))
                elif has_lo and has_hi:
                    rows.append(model.addRange(expr, lo, hi, name=name))
                elif has_lo:
                    rows.append(model.addConstr(expr >= lo, name=name))
                else:
                    rows.append(model.addConstr(expr <= hi, name=name))

            model.optimize()
            elapsed = time.perf_counter() - started

            status = _GUROBI_STATUS.get(model.Status, SolveStatus.ERROR)
            if model.SolCount == 0:
                return SolveResult(
                    status=status, objective=0.0, values=[], duals=[], seconds=elapsed
                )

            values = [v.X for v in variables]
            duals = [c.Pi for c in rows] if relax_binaries else []
            gap = None if relax_binaries else getattr(model, "MIPGap", None)
            return SolveResult(
                status=status,
                objective=model.ObjVal,
                values=values,
                duals=duals,
                seconds=elapsed,
                mip_gap=gap,
            )
        except gp.GurobiError as exc:
            # Size-limited license raises; caller falls back to HiGHS.
            raise SolverUnavailable(f"gurobi could not solve this model: {exc}") from exc


@dataclass
class SolveResult:
    status: SolveStatus
    objective: float
    values: list[float]
    duals: list[float]
    seconds: float
    mip_gap: float | None = None

    @property
    def has_solution(self) -> bool:
        return bool(self.values) and self.status in (
            SolveStatus.OPTIMAL,
            SolveStatus.FEASIBLE,
            SolveStatus.TIME_LIMIT,
        )


_HIGHS_STATUS = {
    "optimal": SolveStatus.OPTIMAL,
    "infeasible": SolveStatus.INFEASIBLE,
    "unbounded": SolveStatus.UNBOUNDED,
    "primal infeasible": SolveStatus.INFEASIBLE,
    "primal unbounded": SolveStatus.UNBOUNDED,
    "time limit reached": SolveStatus.TIME_LIMIT,
    "iteration limit reached": SolveStatus.TIME_LIMIT,
    "solution limit reached": SolveStatus.FEASIBLE,
}

_GUROBI_STATUS = {
    2: SolveStatus.OPTIMAL,
    3: SolveStatus.INFEASIBLE,
    4: SolveStatus.INFEASIBLE,
    5: SolveStatus.UNBOUNDED,
    9: SolveStatus.TIME_LIMIT,
    11: SolveStatus.FEASIBLE,
    13: SolveStatus.FEASIBLE,
}


def available_solvers() -> list[SolverName]:
    """Solvers that are importable and, for Gurobi, actually licensed."""
    found: list[SolverName] = []
    try:
        import gurobipy as gp

        env = gp.Env(params={"OutputFlag": 0})
        gp.Model("licence-probe", env=env)
        found.append(SolverName.GUROBI)
    except Exception:
        pass
    try:
        import highspy  # noqa: F401

        found.append(SolverName.HIGHS)
    except ImportError:
        pass
    return found

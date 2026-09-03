"""HTTP API. Solves run synchronously, bounded by max_solve_seconds."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from .auth import create_access_token, get_current_user, require_role, verify_password
from .db import get_db
from .heuristic_types import RunMethod
from .models import (
    PlanRun,
    Scenario,
    ScenarioCapacity,
    ScenarioDemand,
    User,
)
from .opt.heuristic import anneal
from .opt.marginal import evaluate_marginal_capacity
from .opt.model import solve_plan
from .opt.solvers import available_solvers
from .opt.types import SolverName
from .schemas import (
    BenchmarkResult,
    BottleneckOut,
    CapacityUpdate,
    CostBreakdownOut,
    DemandUpdate,
    InventoryOut,
    MarginalValueOut,
    MarginalValueReport,
    ProductionOut,
    RunAnalytics,
    RunDetail,
    RunRequest,
    RunSummary,
    ShipmentOut,
    ScenarioCreate,
    ScenarioDetail,
    ScenarioSummary,
    SolverInfo,
    Token,
    UserOut,
    UtilizationCell,
)
from .scenario_store import create_scenario_from_spec
from .services import ScenarioNotFound, compare_runs, load_problem, run_plan

router = APIRouter(prefix="/api")

CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[Session, Depends(get_db)]


# ------------------------------------------------------------------ auth


@router.post("/auth/token", response_model=Token, tags=["auth"])
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: Db) -> Token:
    user = db.scalar(select(User).where(User.email == form.username))
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(
        access_token=create_access_token(user.email, user.role),
        role=user.role,
        full_name=user.full_name,
    )


@router.get("/auth/me", response_model=UserOut, tags=["auth"])
def me(user: CurrentUser) -> User:
    return user


# ------------------------------------------------------------------ solvers


@router.get("/solvers", response_model=SolverInfo, tags=["meta"])
def solvers(_user: CurrentUser) -> SolverInfo:
    found = [s.value for s in available_solvers()]
    return SolverInfo(
        available=found,
        default=SolverName.HIGHS.value,
        notes=(
            "Gurobi is used when a licence is present. The pip-installed licence is "
            "capped at 2000 variables and 2000 constraints, so larger networks fall back "
            "to HiGHS, which is open source and unrestricted."
        ),
    )


# ------------------------------------------------------------------ scenarios


@router.get("/scenarios", response_model=list[ScenarioSummary], tags=["scenarios"])
def list_scenarios(db: Db, _user: CurrentUser) -> list[Scenario]:
    return list(db.scalars(select(Scenario).order_by(Scenario.created_at.desc())))


@router.post(
    "/scenarios",
    response_model=ScenarioSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["scenarios"],
)
def create_scenario(
    payload: ScenarioCreate,
    db: Db,
    user: Annotated[User, Depends(require_role("planner", "admin"))],
) -> Scenario:
    existing = db.scalar(select(Scenario).where(Scenario.name == payload.name))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a scenario named '{payload.name}' already exists",
        )
    return create_scenario_from_spec(db, payload)


@router.get("/scenarios/{scenario_id}", response_model=ScenarioDetail, tags=["scenarios"])
def get_scenario(scenario_id: int, db: Db, _user: CurrentUser) -> ScenarioDetail:
    scenario = db.scalar(
        select(Scenario)
        .where(Scenario.id == scenario_id)
        .options(
            selectinload(Scenario.skus),
            selectinload(Scenario.lines),
            selectinload(Scenario.dcs),
        )
    )
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")

    total_demand = db.scalar(
        select(func.coalesce(func.sum(ScenarioDemand.units), 0.0)).where(
            ScenarioDemand.scenario_id == scenario_id
        )
    )
    detail = ScenarioDetail.model_validate(scenario)
    detail.sku_count = len(scenario.skus)
    detail.line_count = len(scenario.lines)
    detail.dc_count = len(scenario.dcs)
    detail.total_demand_units = float(total_demand or 0.0)
    return detail


@router.delete(
    "/scenarios/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    tags=["scenarios"],
)
def delete_scenario(
    scenario_id: int,
    db: Db,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> Response:
    # Admin-only: deleting a scenario cascades into its runs.
    result = db.execute(delete(Scenario).where(Scenario.id == scenario_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="scenario not found")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/scenarios/{scenario_id}/capacity", tags=["scenarios"])
def update_capacity(
    scenario_id: int,
    updates: list[CapacityUpdate],
    db: Db,
    _user: Annotated[User, Depends(require_role("planner", "admin"))],
) -> dict:
    if not updates:
        raise HTTPException(status_code=400, detail="no updates supplied")
    changed = 0
    for update in updates:
        row = db.scalar(
            select(ScenarioCapacity).where(
                ScenarioCapacity.scenario_id == scenario_id,
                ScenarioCapacity.line_code == update.line_code,
                ScenarioCapacity.period == update.period,
            )
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"no capacity row for {update.line_code} period {update.period}",
            )
        row.hours = update.hours
        changed += 1
    db.commit()
    return {"updated": changed}


@router.patch("/scenarios/{scenario_id}/demand", tags=["scenarios"])
def update_demand(
    scenario_id: int,
    updates: list[DemandUpdate],
    db: Db,
    _user: Annotated[User, Depends(require_role("planner", "admin"))],
) -> dict:
    if not updates:
        raise HTTPException(status_code=400, detail="no updates supplied")
    changed = 0
    for update in updates:
        row = db.scalar(
            select(ScenarioDemand).where(
                ScenarioDemand.scenario_id == scenario_id,
                ScenarioDemand.dc_code == update.dc_code,
                ScenarioDemand.sku_code == update.sku_code,
                ScenarioDemand.period == update.period,
            )
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no demand row for {update.sku_code} at {update.dc_code} "
                    f"period {update.period}"
                ),
            )
        row.units = update.units
        changed += 1
    db.commit()
    return {"updated": changed}


# ------------------------------------------------------------------ runs


@router.post(
    "/scenarios/{scenario_id}/runs",
    response_model=RunSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["runs"],
)
def create_run(
    scenario_id: int,
    payload: RunRequest,
    db: Db,
    user: Annotated[User, Depends(require_role("planner", "analyst", "admin"))],
) -> PlanRun:
    requested = payload.solver
    if requested is SolverName.GUROBI and SolverName.GUROBI not in available_solvers():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="gurobi is not licensed in this environment; use highs",
        )
    try:
        return run_plan(
            db,
            scenario_id,
            method=payload.method,
            solver=requested,
            label=payload.label or f"{payload.method.value} via {requested.value}",
            created_by=user.email,
            time_limit=payload.time_limit,
            mip_gap=payload.mip_gap,
            iterations=payload.iterations,
            seed=payload.seed,
        )
    except ScenarioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/scenarios/{scenario_id}/runs", response_model=list[RunSummary], tags=["runs"])
def list_runs(scenario_id: int, db: Db, _user: CurrentUser) -> list[PlanRun]:
    return list(
        db.scalars(
            select(PlanRun)
            .where(PlanRun.scenario_id == scenario_id)
            .order_by(PlanRun.created_at.desc())
        )
    )


def _load_run(db: Session, run_id: int) -> PlanRun:
    run = db.scalar(
        select(PlanRun)
        .where(PlanRun.id == run_id)
        .options(
            selectinload(PlanRun.production),
            selectinload(PlanRun.shipments),
            selectinload(PlanRun.inventory),
            selectinload(PlanRun.bottlenecks),
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


def _costs(run: PlanRun) -> CostBreakdownOut:
    return CostBreakdownOut(
        production=run.cost_production,
        changeover=run.cost_changeover,
        holding=run.cost_holding,
        freight=run.cost_freight,
        stockout=run.cost_stockout,
        total=(
            run.cost_production
            + run.cost_changeover
            + run.cost_holding
            + run.cost_freight
            + run.cost_stockout
        ),
    )


@router.get("/runs/{run_id}", response_model=RunDetail, tags=["runs"])
def get_run(run_id: int, db: Db, _user: CurrentUser) -> RunDetail:
    run = _load_run(db, run_id)
    # `costs` is derived; assemble the detail model explicitly.
    return RunDetail(
        **RunSummary.model_validate(run).model_dump(),
        costs=_costs(run),
        production=[ProductionOut.model_validate(p) for p in run.production],
        shipments=[ShipmentOut.model_validate(s) for s in run.shipments],
        inventory=[InventoryOut.model_validate(i) for i in run.inventory],
        bottlenecks=[BottleneckOut.model_validate(b) for b in run.bottlenecks],
    )


@router.get("/runs/{run_id}/analytics", response_model=RunAnalytics, tags=["runs"])
def run_analytics(run_id: int, db: Db, _user: CurrentUser) -> RunAnalytics:
    run = _load_run(db, run_id)
    _scenario, data = load_problem(db, run.scenario_id)

    used: dict[tuple[str, int], float] = {}
    for entry in run.production:
        key = (entry.line_code, entry.period)
        used[key] = used.get(key, 0.0) + entry.hours

    utilization = []
    for line in data.lines:
        for period in data.periods:
            available = data.hours_for(line.id, period)
            hours = used.get((line.id, period), 0.0)
            utilization.append(
                UtilizationCell(
                    line_code=line.id,
                    period=period,
                    hours_used=round(hours, 3),
                    hours_available=round(available, 3),
                    utilization=round(hours / available, 4) if available else 0.0,
                )
            )

    delivered = max(run.total_demand_units - run.unmet_units, 1.0)
    top = sorted(run.bottlenecks, key=lambda b: b.shadow_price, reverse=True)[:5]

    if run.unmet_units > 0 and top:
        head = top[0]
        recommendation = (
            f"{run.unmet_units:,.0f} units of demand go unserved. {head.line_code} is the "
            f"binding constraint in period {head.period}, where an extra hour is worth "
            f"${head.shadow_price:,.2f}. Adding a shift there is the cheapest way to "
            f"recover service."
        )
    elif top:
        head = top[0]
        recommendation = (
            f"All demand is served. {head.line_code} is still the tightest line in period "
            f"{head.period} at ${head.shadow_price:,.2f} per hour, so it is where any "
            f"further volume will bind first."
        )
    else:
        recommendation = (
            "No capacity constraint is binding. The plan is limited by cost, not by "
            "hours, so buying capacity will not reduce spend."
        )

    return RunAnalytics(
        run_id=run.id,
        costs=_costs(run),
        utilization=utilization,
        cost_per_unit_delivered=round(run.objective / delivered, 5),
        changeover_count=sum(1 for e in run.production if e.changeover),
        top_bottlenecks=[BottleneckOut.model_validate(b) for b in top],
        recommendation=recommendation,
    )


@router.post(
    "/runs/{run_id}/marginal-value",
    response_model=MarginalValueReport,
    tags=["runs"],
)
def marginal_value(
    run_id: int,
    db: Db,
    _user: Annotated[User, Depends(require_role("planner", "analyst", "admin"))],
    top_n: Annotated[int, Query(ge=1, le=5)] = 3,
    hours: Annotated[float, Query(gt=0, le=40)] = 1.0,
) -> MarginalValueReport:
    """What an extra hour on the tightest lines is actually worth.

    The shadow prices on a run come from the LP relaxation and overstate the money,
    because the relaxation can pay part of a changeover and the executable plan cannot.
    This endpoint re-solves the integer program with extra hours and reports the saving
    that is really available, which is the number to size a shift purchase against.
    """
    run = _load_run(db, run_id)
    _scenario, data = load_problem(db, run.scenario_id)

    solver = (
        SolverName.GUROBI
        if SolverName.GUROBI in available_solvers()
        else SolverName.HIGHS
    )
    baseline = solve_plan(data, solver, time_limit=90, with_sensitivity=True)
    results = evaluate_marginal_capacity(
        data, baseline, solver=solver, top_n=top_n, hours_added=hours
    )

    if not results:
        guidance = "No capacity constraint is binding, so extra hours are worth nothing."
    else:
        best = results[0]
        guidance = (
            f"An hour on {best.line_id} in period {best.period} is worth "
            f"${best.realised_value_per_hour:,.2f}. The relaxation priced it at "
            f"${best.lp_shadow_price:,.2f}, which is {best.dual_overstatement:.1f}x too "
            f"high, so budget against the realised figure."
        )

    return MarginalValueReport(
        run_id=run.id,
        results=[
            MarginalValueOut(
                line_code=r.line_id,
                period=r.period,
                hours_added=r.hours_added,
                lp_shadow_price=round(r.lp_shadow_price, 4),
                realised_value_per_hour=round(r.realised_value_per_hour, 4),
                total_saving=round(r.total_saving, 2),
                dual_overstatement=round(r.dual_overstatement, 3),
            )
            for r in results
        ],
        guidance=guidance,
    )


@router.get("/runs/{run_id}/compare/{other_id}", tags=["runs"])
def compare(run_id: int, other_id: int, db: Db, _user: CurrentUser) -> dict:
    return compare_runs(_load_run(db, run_id), _load_run(db, other_id))


@router.post(
    "/scenarios/{scenario_id}/benchmark",
    response_model=BenchmarkResult,
    tags=["runs"],
)
def benchmark(
    scenario_id: int,
    db: Db,
    _user: Annotated[User, Depends(require_role("planner", "analyst", "admin"))],
    time_limit: Annotated[float, Query(gt=0, le=300)] = 60.0,
    iterations: Annotated[int, Query(ge=10, le=2000)] = 400,
) -> BenchmarkResult:
    """Solve the same scenario exactly and heuristically, and report the difference.

    This is the question a planner actually asks about optimisation software: what does
    the exact solver buy over something simpler, and is it worth the runtime.
    """
    try:
        _scenario, data = load_problem(db, scenario_id)
    except ScenarioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    solver = (
        SolverName.GUROBI
        if SolverName.GUROBI in available_solvers()
        else SolverName.HIGHS
    )
    exact = solve_plan(data, solver, time_limit=time_limit, with_sensitivity=False)
    heuristic = anneal(
        data, solver=SolverName.HIGHS, iterations=iterations, time_budget=time_limit
    )

    gap = (
        (heuristic.best_cost - exact.objective) / exact.objective * 100
        if exact.objective
        else 0.0
    )
    if gap < 1.0:
        verdict = (
            f"The heuristic is within {gap:.2f}% of the exact plan, so it is a safe "
            f"fallback for networks that exceed the solver licence."
        )
    elif gap < 5.0:
        verdict = (
            f"The exact solver is worth {gap:.2f}% of total cost here. Use it whenever the "
            f"model fits."
        )
    else:
        verdict = (
            f"The heuristic gives up {gap:.2f}%, which is too much to accept. This network "
            f"needs the exact solver."
        )

    return BenchmarkResult(
        exact_objective=round(exact.objective, 2),
        exact_seconds=round(exact.solve_seconds, 3),
        exact_status=exact.status.value,
        heuristic_objective=round(heuristic.best_cost, 2),
        heuristic_seconds=round(heuristic.seconds, 3),
        heuristic_iterations=heuristic.iterations,
        gap_pct=round(gap, 3),
        verdict=verdict,
    )

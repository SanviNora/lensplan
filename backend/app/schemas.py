from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .heuristic_types import RunMethod
from .opt.types import SolverName


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ auth


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str


class UserOut(ORMModel):
    id: int
    email: EmailStr
    full_name: str
    role: str


# ------------------------------------------------------------------ scenario


class SkuOut(ORMModel):
    code: str
    name: str
    family: str
    holding_cost: float
    stockout_penalty: float
    shelf_life_periods: int


class LineOut(ORMModel):
    code: str
    plant_code: str
    name: str


class ScenarioSummary(ORMModel):
    id: int
    name: str
    description: str
    periods: int
    max_skus_per_line_period: int | None
    created_at: datetime


class ScenarioDetail(ScenarioSummary):
    skus: list[SkuOut]
    lines: list[LineOut]
    sku_count: int = 0
    line_count: int = 0
    dc_count: int = 0
    total_demand_units: float = 0.0


class ScenarioCreate(BaseModel):
    """Create a scenario by generating a network.

    Editing an existing scenario's demand or capacity is a separate operation; this
    endpoint exists so a planner can spin up a what-if network without hand-entering
    thousands of rows.
    """

    name: str = Field(min_length=3, max_length=160)
    description: str = ""
    skus: int = Field(default=10, ge=2, le=60)
    plants: int = Field(default=2, ge=1, le=3)
    lines_per_plant: int = Field(default=2, ge=1, le=4)
    dcs: int = Field(default=3, ge=1, le=4)
    periods: int = Field(default=8, ge=2, le=16)
    seed: int = 7
    demand_surge: float = Field(default=1.0, gt=0, le=5.0)
    capacity_scale: float = Field(default=1.0, ge=0.0, le=3.0)
    max_skus_per_line_period: int | None = Field(default=4, ge=1, le=20)


class CapacityUpdate(BaseModel):
    line_code: str
    period: int = Field(ge=1)
    hours: float = Field(ge=0)


class DemandUpdate(BaseModel):
    dc_code: str
    sku_code: str
    period: int = Field(ge=1)
    units: float = Field(ge=0)


# ------------------------------------------------------------------ runs


class RunRequest(BaseModel):
    method: RunMethod = RunMethod.EXACT
    solver: SolverName = SolverName.HIGHS
    label: str = ""
    time_limit: float | None = Field(default=None, gt=0, le=600)
    mip_gap: float | None = Field(default=None, ge=0, le=0.5)
    iterations: int = Field(default=400, ge=10, le=2000)
    seed: int = 11

    @field_validator("label")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()[:160]


class CostBreakdownOut(BaseModel):
    production: float
    changeover: float
    holding: float
    freight: float
    stockout: float
    total: float


class RunSummary(ORMModel):
    id: int
    scenario_id: int
    label: str
    method: str
    solver: str
    status: str
    objective: float
    service_level: float
    unmet_units: float
    total_demand_units: float
    solve_seconds: float
    mip_gap: float | None
    iterations: int | None
    created_at: datetime
    created_by: str


class ProductionOut(ORMModel):
    line_code: str
    sku_code: str
    period: int
    units: float
    hours: float
    changeover: bool


class ShipmentOut(ORMModel):
    plant_code: str
    dc_code: str
    sku_code: str
    period: int
    units: float


class InventoryOut(ORMModel):
    dc_code: str
    sku_code: str
    period: int
    closing_units: float
    unmet_units: float


class BottleneckOut(ORMModel):
    line_code: str
    period: int
    shadow_price: float
    hours_available: float
    hours_used: float
    hours_used_relaxed: float


class RunDetail(RunSummary):
    costs: CostBreakdownOut
    production: list[ProductionOut]
    shipments: list[ShipmentOut]
    inventory: list[InventoryOut]
    bottlenecks: list[BottleneckOut]


class UtilizationCell(BaseModel):
    line_code: str
    period: int
    hours_used: float
    hours_available: float
    utilization: float


class RunAnalytics(BaseModel):
    """Everything the dashboard needs that is derived rather than stored."""

    run_id: int
    costs: CostBreakdownOut
    utilization: list[UtilizationCell]
    cost_per_unit_delivered: float
    changeover_count: int
    top_bottlenecks: list[BottleneckOut]
    recommendation: str


class MarginalValueOut(BaseModel):
    line_code: str
    period: int
    hours_added: float
    lp_shadow_price: float
    realised_value_per_hour: float
    total_saving: float
    dual_overstatement: float


class MarginalValueReport(BaseModel):
    run_id: int
    results: list[MarginalValueOut]
    guidance: str


class BenchmarkResult(BaseModel):
    exact_objective: float
    exact_seconds: float
    exact_status: str
    heuristic_objective: float
    heuristic_seconds: float
    heuristic_iterations: int
    gap_pct: float
    verdict: str


class SolverInfo(BaseModel):
    available: list[str]
    default: str
    notes: str

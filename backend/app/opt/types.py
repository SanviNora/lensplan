"""Problem and solution dataclasses. Independent of the database."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SolverName(str, Enum):
    GUROBI = "gurobi"
    HIGHS = "highs"


class SolveStatus(str, Enum):
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    TIME_LIMIT = "time_limit"
    ERROR = "error"


@dataclass(frozen=True)
class Sku:
    """A finished lens product.

    Lens catalogues explode combinatorially because every prescription is a distinct
    finished good: power, base curve, cylinder and axis each multiply the SKU count.
    `family` groups SKUs that share tooling so changeover costs can differ within and
    across families.
    """

    id: str
    name: str
    family: str
    holding_cost: float
    stockout_penalty: float
    shelf_life_periods: int


@dataclass(frozen=True)
class Line:
    """A production line. Lines belong to a plant and are individually capacitated."""

    id: str
    plant_id: str
    name: str


@dataclass(frozen=True)
class Plant:
    id: str
    name: str


@dataclass(frozen=True)
class DistributionCenter:
    id: str
    name: str


@dataclass(frozen=True)
class LineCapability:
    """What a line can make, how fast, and what it costs to switch to it.

    A line that cannot run a SKU simply has no LineCapability row, which keeps the
    variable count down: we never create production variables for infeasible pairings.
    """

    line_id: str
    sku_id: str
    units_per_hour: float
    unit_cost: float
    changeover_cost: float
    min_run_units: float


@dataclass(frozen=True)
class FreightLane:
    plant_id: str
    dc_id: str
    cost_per_unit: float


@dataclass
class ProblemData:
    periods: list[int]
    skus: list[Sku]
    plants: list[Plant]
    lines: list[Line]
    dcs: list[DistributionCenter]
    capabilities: list[LineCapability]
    lanes: list[FreightLane]
    demand: dict[tuple[str, str, int], float]
    line_hours: dict[tuple[str, int], float]
    opening_inventory: dict[tuple[str, str], float] = field(default_factory=dict)
    max_skus_per_line_period: int | None = None

    def capability(self, line_id: str, sku_id: str) -> LineCapability | None:
        return self._capability_index.get((line_id, sku_id))

    def lane_cost(self, plant_id: str, dc_id: str) -> float | None:
        return self._lane_index.get((plant_id, dc_id))

    def demand_for(self, dc_id: str, sku_id: str, period: int) -> float:
        return self.demand.get((dc_id, sku_id, period), 0.0)

    def hours_for(self, line_id: str, period: int) -> float:
        return self.line_hours.get((line_id, period), 0.0)

    def lines_at(self, plant_id: str) -> list[Line]:
        return [line for line in self.lines if line.plant_id == plant_id]

    def __post_init__(self) -> None:
        self._capability_index = {(c.line_id, c.sku_id): c for c in self.capabilities}
        self._lane_index = {(lane.plant_id, lane.dc_id): lane.cost_per_unit for lane in self.lanes}
        self._validate()

    def _validate(self) -> None:
        if not self.periods:
            raise ValueError("problem must define at least one period")
        if sorted(self.periods) != self.periods:
            raise ValueError("periods must be supplied in ascending order")
        if len(set(self.periods)) != len(self.periods):
            raise ValueError("periods must be unique")

        sku_ids = {s.id for s in self.skus}
        line_ids = {line.id for line in self.lines}
        plant_ids = {p.id for p in self.plants}
        dc_ids = {d.id for d in self.dcs}

        for cap in self.capabilities:
            if cap.line_id not in line_ids:
                raise ValueError(f"capability references unknown line {cap.line_id}")
            if cap.sku_id not in sku_ids:
                raise ValueError(f"capability references unknown sku {cap.sku_id}")
            if cap.units_per_hour <= 0:
                raise ValueError(f"units_per_hour must be positive for {cap.line_id}/{cap.sku_id}")

        for line in self.lines:
            if line.plant_id not in plant_ids:
                raise ValueError(f"line {line.id} references unknown plant {line.plant_id}")

        for (dc_id, sku_id, period) in self.demand:
            if dc_id not in dc_ids:
                raise ValueError(f"demand references unknown dc {dc_id}")
            if sku_id not in sku_ids:
                raise ValueError(f"demand references unknown sku {sku_id}")
            if period not in self.periods:
                raise ValueError(f"demand references unknown period {period}")

        # Catch unreachable demand/SKU pairs before solve.
        producible = {cap.sku_id for cap in self.capabilities}
        for sku in self.skus:
            if sku.id not in producible:
                raise ValueError(f"sku {sku.id} cannot be produced on any line")
        for dc in self.dcs:
            if not any(self.lane_cost(p.id, dc.id) is not None for p in self.plants):
                raise ValueError(f"dc {dc.id} is unreachable from every plant")


@dataclass(frozen=True)
class ProductionEntry:
    line_id: str
    sku_id: str
    period: int
    units: float
    hours: float
    changeover: bool


@dataclass(frozen=True)
class ShipmentEntry:
    plant_id: str
    dc_id: str
    sku_id: str
    period: int
    units: float


@dataclass(frozen=True)
class InventoryEntry:
    dc_id: str
    sku_id: str
    period: int
    closing_units: float
    unmet_units: float


@dataclass(frozen=True)
class CostBreakdown:
    production: float
    changeover: float
    holding: float
    freight: float
    stockout: float

    @property
    def total(self) -> float:
        return self.production + self.changeover + self.holding + self.freight + self.stockout

    def as_dict(self) -> dict[str, float]:
        return {
            "production": self.production,
            "changeover": self.changeover,
            "holding": self.holding,
            "freight": self.freight,
            "stockout": self.stockout,
            "total": self.total,
        }


@dataclass(frozen=True)
class CapacityShadowPrice:
    """Marginal value of one more hour on a line in a period.

    Taken from the dual of the capacity constraint in the LP relaxation. Duals are not
    defined for a MIP, so this is computed on the relaxation and reported as guidance
    rather than as an exact marginal cost of the integer solution.
    """

    line_id: str
    period: int
    shadow_price: float
    hours_available: float
    hours_used: float
    hours_used_relaxed: float

    @property
    def utilization(self) -> float:
        """Utilisation in the plan that will actually be executed (the MIP solution)."""
        return self.hours_used / self.hours_available if self.hours_available else 0.0

    @property
    def relaxed_utilization(self) -> float:
        """Utilisation in the relaxation the shadow price was taken from.

        This is the figure the price is consistent with. It can sit above the executed
        plan's utilisation, because minimum run lengths and the line-variety limit force
        the integer solution to leave hours on the table that the relaxation can use.
        """
        return (
            self.hours_used_relaxed / self.hours_available if self.hours_available else 0.0
        )

    @property
    def binding_in_relaxation(self) -> bool:
        return self.relaxed_utilization > 0.999


@dataclass
class Solution:
    status: SolveStatus
    solver: SolverName
    objective: float
    costs: CostBreakdown
    production: list[ProductionEntry]
    shipments: list[ShipmentEntry]
    inventory: list[InventoryEntry]
    solve_seconds: float
    total_demand_units: float
    mip_gap: float | None = None
    shadow_prices: list[CapacityShadowPrice] = field(default_factory=list)

    @property
    def unmet_units(self) -> float:
        return sum(entry.unmet_units for entry in self.inventory)

    @property
    def service_level(self) -> float:
        """Fraction of demanded units delivered in the period they were demanded."""
        if self.total_demand_units <= 0:
            return 1.0
        return 1.0 - self.unmet_units / self.total_demand_units

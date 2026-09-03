"""Seeded scenario generator. Default size stays under the Gurobi pip license cap."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .types import (
    DistributionCenter,
    FreightLane,
    Line,
    LineCapability,
    Plant,
    ProblemData,
    Sku,
)

FAMILIES = [
    ("DD", "Daily Disposable Sphere", 0.006, 0.42, 6),
    ("MO", "Monthly Sphere", 0.011, 0.55, 10),
    ("TO", "Toric Astigmatic", 0.019, 0.94, 10),
    ("MF", "Multifocal", 0.024, 1.20, 12),
    ("IOL", "Intraocular Lens", 0.140, 9.50, 24),
]

PLANT_NAMES = [("P-FTW", "Fort Worth"), ("P-SGP", "Singapore"), ("P-BAR", "Barcelona")]
DC_NAMES = [
    ("DC-ATL", "Atlanta"),
    ("DC-CHI", "Chicago"),
    ("DC-LAX", "Los Angeles"),
    ("DC-FRA", "Frankfurt"),
]


@dataclass(frozen=True)
class ScenarioSize:
    skus: int
    plants: int
    lines_per_plant: int
    dcs: int
    periods: int
    capability_ratio: float = 0.6

    @property
    def estimated_vars(self) -> int:
        caps = int(self.plants * self.lines_per_plant * self.skus * self.capability_ratio)
        lanes = self.plants * self.dcs
        return (
            2 * caps * self.periods
            + lanes * self.skus * self.periods
            + 2 * self.dcs * self.skus * self.periods
        )


# Stay under the 2000-variable Gurobi pip license cap.
DEFAULT_SIZE = ScenarioSize(skus=10, plants=2, lines_per_plant=2, dcs=3, periods=8)
LARGE_SIZE = ScenarioSize(skus=40, plants=3, lines_per_plant=3, dcs=4, periods=12)


def build_scenario(
    size: ScenarioSize = DEFAULT_SIZE,
    *,
    seed: int = 7,
    demand_surge: float = 1.0,
    capacity_scale: float = 1.0,
) -> ProblemData:
    """Generate a planning scenario.

    `demand_surge` and `capacity_scale` exist so the API can create what-if scenarios
    against the same base network: a surge above 1.0 raises demand everywhere, a capacity
    scale below 1.0 simulates losing shift hours.
    """
    rng = random.Random(seed)

    skus: list[Sku] = []
    for i in range(size.skus):
        code, label, holding, penalty, life = FAMILIES[i % len(FAMILIES)]
        power = -6.0 + 0.5 * (i // len(FAMILIES))
        skus.append(
            Sku(
                id=f"{code}-{i:03d}",
                name=f"{label} {power:+.2f}D",
                family=code,
                holding_cost=round(holding * rng.uniform(0.9, 1.1), 4),
                stockout_penalty=round(penalty * rng.uniform(0.9, 1.1), 3),
                shelf_life_periods=life,
            )
        )

    plants = [Plant(id=pid, name=name) for pid, name in PLANT_NAMES[: size.plants]]
    dcs = [DistributionCenter(id=did, name=name) for did, name in DC_NAMES[: size.dcs]]

    lines: list[Line] = []
    for plant in plants:
        for j in range(size.lines_per_plant):
            lines.append(
                Line(
                    id=f"{plant.id}-L{j + 1}",
                    plant_id=plant.id,
                    name=f"{plant.name} Line {j + 1}",
                )
            )

    capabilities: list[LineCapability] = []
    for line in lines:
        eligible = [s for s in skus if rng.random() < size.capability_ratio]
        # Keep at least one feasible SKU per line.
        if not eligible:
            eligible = rng.sample(skus, k=max(1, len(skus) // 3))
        for sku in eligible:
            base_rate = {
                "DD": 2200.0,
                "MO": 1400.0,
                "TO": 780.0,
                "MF": 560.0,
                "IOL": 95.0,
            }[sku.family]
            capabilities.append(
                LineCapability(
                    line_id=line.id,
                    sku_id=sku.id,
                    units_per_hour=round(base_rate * rng.uniform(0.85, 1.15), 1),
                    unit_cost=round(
                        {"DD": 0.09, "MO": 0.21, "TO": 0.38, "MF": 0.52, "IOL": 6.40}[
                            sku.family
                        ]
                        * rng.uniform(0.9, 1.1),
                        4,
                    ),
                    changeover_cost=round(rng.uniform(280.0, 1450.0), 2),
                    min_run_units=float(
                        {"DD": 5000, "MO": 3000, "TO": 1200, "MF": 900, "IOL": 120}[
                            sku.family
                        ]
                    ),
                )
            )

    covered = {cap.sku_id for cap in capabilities}
    for sku in skus:
        if sku.id in covered:
            continue
        line = rng.choice(lines)
        capabilities.append(
            LineCapability(
                line_id=line.id,
                sku_id=sku.id,
                units_per_hour=900.0,
                unit_cost=0.35,
                changeover_cost=700.0,
                min_run_units=1000.0,
            )
        )

    lanes = [
        FreightLane(
            plant_id=plant.id,
            dc_id=dc.id,
            cost_per_unit=round(rng.uniform(0.012, 0.085), 4),
        )
        for plant in plants
        for dc in dcs
    ]

    periods = list(range(1, size.periods + 1))

    demand: dict[tuple[str, str, int], float] = {}
    for dc in dcs:
        dc_scale = rng.uniform(0.7, 1.4)
        for sku in skus:
            base = {
                "DD": 42000.0,
                "MO": 16000.0,
                "TO": 7400.0,
                "MF": 4200.0,
                "IOL": 900.0,
            }[sku.family] * dc_scale * rng.uniform(0.6, 1.4)
            for t in periods:
                # Mild seasonality so the plan is not identical every period.
                season = 1.0 + 0.18 * ((t % 4) - 1.5) / 1.5
                qty = base * season * rng.uniform(0.88, 1.12) * demand_surge
                demand[(dc.id, sku.id, t)] = round(qty, 1)

    line_hours: dict[tuple[str, int], float] = {}
    for line in lines:
        for t in periods:
            hours = 120.0 * rng.uniform(0.92, 1.08) * capacity_scale
            line_hours[(line.id, t)] = round(hours, 2)

    opening = {
        (dc.id, sku.id): round(
            demand[(dc.id, sku.id, periods[0])] * rng.uniform(0.05, 0.35), 1
        )
        for dc in dcs
        for sku in skus
    }

    return ProblemData(
        periods=periods,
        skus=skus,
        plants=plants,
        lines=lines,
        dcs=dcs,
        capabilities=capabilities,
        lanes=lanes,
        demand=demand,
        line_hours=line_hours,
        opening_inventory=opening,
        max_skus_per_line_period=4,
    )

"""Persist a generated network as scenario rows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import (
    Scenario,
    ScenarioCapability,
    ScenarioCapacity,
    ScenarioDc,
    ScenarioDemand,
    ScenarioLane,
    ScenarioLine,
    ScenarioOpeningInventory,
    ScenarioPlant,
    ScenarioSku,
)
from .opt.scenario import ScenarioSize, build_scenario
from .schemas import ScenarioCreate


def create_scenario_from_spec(db: Session, spec: ScenarioCreate) -> Scenario:
    size = ScenarioSize(
        skus=spec.skus,
        plants=spec.plants,
        lines_per_plant=spec.lines_per_plant,
        dcs=spec.dcs,
        periods=spec.periods,
    )
    data = build_scenario(
        size,
        seed=spec.seed,
        demand_surge=spec.demand_surge,
        capacity_scale=spec.capacity_scale,
    )

    scenario = Scenario(
        name=spec.name,
        description=spec.description,
        periods=spec.periods,
        max_skus_per_line_period=spec.max_skus_per_line_period,
    )
    db.add(scenario)
    db.flush()

    db.add_all(
        ScenarioSku(
            scenario_id=scenario.id,
            code=s.id,
            name=s.name,
            family=s.family,
            holding_cost=s.holding_cost,
            stockout_penalty=s.stockout_penalty,
            shelf_life_periods=s.shelf_life_periods,
        )
        for s in data.skus
    )
    db.add_all(
        ScenarioPlant(scenario_id=scenario.id, code=p.id, name=p.name) for p in data.plants
    )
    db.add_all(
        ScenarioLine(
            scenario_id=scenario.id, code=line.id, plant_code=line.plant_id, name=line.name
        )
        for line in data.lines
    )
    db.add_all(
        ScenarioDc(scenario_id=scenario.id, code=d.id, name=d.name) for d in data.dcs
    )
    db.add_all(
        ScenarioCapability(
            scenario_id=scenario.id,
            line_code=c.line_id,
            sku_code=c.sku_id,
            units_per_hour=c.units_per_hour,
            unit_cost=c.unit_cost,
            changeover_cost=c.changeover_cost,
            min_run_units=c.min_run_units,
        )
        for c in data.capabilities
    )
    db.add_all(
        ScenarioLane(
            scenario_id=scenario.id,
            plant_code=lane.plant_id,
            dc_code=lane.dc_id,
            cost_per_unit=lane.cost_per_unit,
        )
        for lane in data.lanes
    )
    db.add_all(
        ScenarioDemand(
            scenario_id=scenario.id,
            dc_code=dc_id,
            sku_code=sku_id,
            period=period,
            units=units,
        )
        for (dc_id, sku_id, period), units in data.demand.items()
    )
    db.add_all(
        ScenarioCapacity(
            scenario_id=scenario.id, line_code=line_id, period=period, hours=hours
        )
        for (line_id, period), hours in data.line_hours.items()
    )
    db.add_all(
        ScenarioOpeningInventory(
            scenario_id=scenario.id, dc_code=dc_id, sku_code=sku_id, units=units
        )
        for (dc_id, sku_id), units in data.opening_inventory.items()
    )

    db.commit()
    db.refresh(scenario)
    return scenario

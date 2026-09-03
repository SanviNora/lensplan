"""SQLAlchemy models. Network is stored normalized; result rows are written per solve."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="planner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("role in ('planner','analyst','admin')", name="ck_user_role"),
    )


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(String(1000), default="")
    periods: Mapped[int] = mapped_column(Integer)
    max_skus_per_line_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    skus: Mapped[list["ScenarioSku"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    plants: Mapped[list["ScenarioPlant"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    lines: Mapped[list["ScenarioLine"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    dcs: Mapped[list["ScenarioDc"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    capabilities: Mapped[list["ScenarioCapability"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    lanes: Mapped[list["ScenarioLane"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    demand_rows: Mapped[list["ScenarioDemand"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    capacity_rows: Mapped[list["ScenarioCapacity"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )
    runs: Mapped[list["PlanRun"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )


class ScenarioSku(Base):
    __tablename__ = "scenario_skus"
    __table_args__ = (UniqueConstraint("scenario_id", "code", name="uq_scenario_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(160))
    family: Mapped[str] = mapped_column(String(32), index=True)
    holding_cost: Mapped[float] = mapped_column(Float)
    stockout_penalty: Mapped[float] = mapped_column(Float)
    shelf_life_periods: Mapped[int] = mapped_column(Integer)

    scenario: Mapped[Scenario] = relationship(back_populates="skus")


class ScenarioPlant(Base):
    __tablename__ = "scenario_plants"
    __table_args__ = (UniqueConstraint("scenario_id", "code", name="uq_scenario_plant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(160))

    scenario: Mapped[Scenario] = relationship(back_populates="plants")


class ScenarioLine(Base):
    __tablename__ = "scenario_lines"
    __table_args__ = (UniqueConstraint("scenario_id", "code", name="uq_scenario_line"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(48), index=True)
    plant_code: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(160))

    scenario: Mapped[Scenario] = relationship(back_populates="lines")


class ScenarioDc(Base):
    __tablename__ = "scenario_dcs"
    __table_args__ = (UniqueConstraint("scenario_id", "code", name="uq_scenario_dc"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(160))

    scenario: Mapped[Scenario] = relationship(back_populates="dcs")


class ScenarioCapability(Base):
    __tablename__ = "scenario_capabilities"
    __table_args__ = (
        UniqueConstraint("scenario_id", "line_code", "sku_code", name="uq_scenario_capability"),
        CheckConstraint("units_per_hour > 0", name="ck_capability_rate_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    line_code: Mapped[str] = mapped_column(String(48), index=True)
    sku_code: Mapped[str] = mapped_column(String(48), index=True)
    units_per_hour: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float)
    changeover_cost: Mapped[float] = mapped_column(Float)
    min_run_units: Mapped[float] = mapped_column(Float, default=0.0)

    scenario: Mapped[Scenario] = relationship(back_populates="capabilities")


class ScenarioLane(Base):
    __tablename__ = "scenario_lanes"
    __table_args__ = (
        UniqueConstraint("scenario_id", "plant_code", "dc_code", name="uq_scenario_lane"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    plant_code: Mapped[str] = mapped_column(String(48), index=True)
    dc_code: Mapped[str] = mapped_column(String(48), index=True)
    cost_per_unit: Mapped[float] = mapped_column(Float)

    scenario: Mapped[Scenario] = relationship(back_populates="lanes")


class ScenarioDemand(Base):
    __tablename__ = "scenario_demand"
    __table_args__ = (
        UniqueConstraint(
            "scenario_id", "dc_code", "sku_code", "period", name="uq_scenario_demand"
        ),
        CheckConstraint("units >= 0", name="ck_demand_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    dc_code: Mapped[str] = mapped_column(String(48), index=True)
    sku_code: Mapped[str] = mapped_column(String(48), index=True)
    period: Mapped[int] = mapped_column(Integer, index=True)
    units: Mapped[float] = mapped_column(Float)

    scenario: Mapped[Scenario] = relationship(back_populates="demand_rows")


class ScenarioCapacity(Base):
    __tablename__ = "scenario_capacity"
    __table_args__ = (
        UniqueConstraint("scenario_id", "line_code", "period", name="uq_scenario_capacity"),
        CheckConstraint("hours >= 0", name="ck_hours_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    line_code: Mapped[str] = mapped_column(String(48), index=True)
    period: Mapped[int] = mapped_column(Integer, index=True)
    hours: Mapped[float] = mapped_column(Float)

    scenario: Mapped[Scenario] = relationship(back_populates="capacity_rows")


class ScenarioOpeningInventory(Base):
    __tablename__ = "scenario_opening_inventory"
    __table_args__ = (
        UniqueConstraint("scenario_id", "dc_code", "sku_code", name="uq_scenario_opening"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    dc_code: Mapped[str] = mapped_column(String(48), index=True)
    sku_code: Mapped[str] = mapped_column(String(48), index=True)
    units: Mapped[float] = mapped_column(Float)


class PlanRun(Base):
    __tablename__ = "plan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(160), default="")
    method: Mapped[str] = mapped_column(String(32))
    solver: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)

    objective: Mapped[float] = mapped_column(Float, default=0.0)
    cost_production: Mapped[float] = mapped_column(Float, default=0.0)
    cost_changeover: Mapped[float] = mapped_column(Float, default=0.0)
    cost_holding: Mapped[float] = mapped_column(Float, default=0.0)
    cost_freight: Mapped[float] = mapped_column(Float, default=0.0)
    cost_stockout: Mapped[float] = mapped_column(Float, default=0.0)

    service_level: Mapped[float] = mapped_column(Float, default=0.0)
    unmet_units: Mapped[float] = mapped_column(Float, default=0.0)
    total_demand_units: Mapped[float] = mapped_column(Float, default=0.0)

    solve_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    mip_gap: Mapped[float | None] = mapped_column(Float, nullable=True)
    iterations: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(255), default="")

    __table_args__ = (
        CheckConstraint("method in ('exact','anneal')", name="ck_run_method"),
    )

    scenario: Mapped[Scenario] = relationship(back_populates="runs")
    production: Mapped[list["RunProduction"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    shipments: Mapped[list["RunShipment"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    inventory: Mapped[list["RunInventory"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    bottlenecks: Mapped[list["RunBottleneck"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunProduction(Base):
    __tablename__ = "run_production"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("plan_runs.id", ondelete="CASCADE"), index=True)
    line_code: Mapped[str] = mapped_column(String(48), index=True)
    sku_code: Mapped[str] = mapped_column(String(48), index=True)
    period: Mapped[int] = mapped_column(Integer, index=True)
    units: Mapped[float] = mapped_column(Float)
    hours: Mapped[float] = mapped_column(Float)
    changeover: Mapped[bool] = mapped_column(Boolean, default=False)

    run: Mapped[PlanRun] = relationship(back_populates="production")


class RunShipment(Base):
    __tablename__ = "run_shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("plan_runs.id", ondelete="CASCADE"), index=True)
    plant_code: Mapped[str] = mapped_column(String(48), index=True)
    dc_code: Mapped[str] = mapped_column(String(48), index=True)
    sku_code: Mapped[str] = mapped_column(String(48), index=True)
    period: Mapped[int] = mapped_column(Integer, index=True)
    units: Mapped[float] = mapped_column(Float)

    run: Mapped[PlanRun] = relationship(back_populates="shipments")


class RunInventory(Base):
    __tablename__ = "run_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("plan_runs.id", ondelete="CASCADE"), index=True)
    dc_code: Mapped[str] = mapped_column(String(48), index=True)
    sku_code: Mapped[str] = mapped_column(String(48), index=True)
    period: Mapped[int] = mapped_column(Integer, index=True)
    closing_units: Mapped[float] = mapped_column(Float)
    unmet_units: Mapped[float] = mapped_column(Float)

    run: Mapped[PlanRun] = relationship(back_populates="inventory")


class RunBottleneck(Base):
    """Capacity shadow prices, stored so a plan's recommendations survive the run."""

    __tablename__ = "run_bottlenecks"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("plan_runs.id", ondelete="CASCADE"), index=True)
    line_code: Mapped[str] = mapped_column(String(48), index=True)
    period: Mapped[int] = mapped_column(Integer, index=True)
    shadow_price: Mapped[float] = mapped_column(Float)
    hours_available: Mapped[float] = mapped_column(Float)
    hours_used: Mapped[float] = mapped_column(Float)
    hours_used_relaxed: Mapped[float] = mapped_column(Float)

    run: Mapped[PlanRun] = relationship(back_populates="bottlenecks")

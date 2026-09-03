"""Create the schema and load baseline, peak-season, and line-down demo scenarios."""

from __future__ import annotations

import sys

from sqlalchemy import delete, select

from app.auth import hash_password
from app.db import SessionLocal, engine
from app.models import Base, Scenario, User
from app.schemas import ScenarioCreate
from app.scenario_store import create_scenario_from_spec

USERS = [
    ("planner@lensplan.example.com", "Priya Raman", "planner", "planner-password"),
    ("analyst@lensplan.example.com", "Dan Mercer", "analyst", "analyst-password"),
    ("admin@lensplan.example.com", "Site Admin", "admin", "admin-password"),
]

SCENARIOS = [
    ScenarioCreate(
        name="Baseline FY27 Q1",
        description="Normal operating conditions across Fort Worth and Singapore.",
        skus=10,
        plants=2,
        lines_per_plant=2,
        dcs=3,
        periods=8,
        seed=7,
    ),
    ScenarioCreate(
        name="Peak season +35% demand",
        description="Seasonal surge with unchanged capacity.",
        skus=10,
        plants=2,
        lines_per_plant=2,
        dcs=3,
        periods=8,
        seed=7,
        demand_surge=1.35,
    ),
    ScenarioCreate(
        name="Line down: 70% capacity",
        description="Extended maintenance removes 30% of available line hours.",
        skus=10,
        plants=2,
        lines_per_plant=2,
        dcs=3,
        periods=8,
        seed=7,
        capacity_scale=0.7,
    ),
]


def main() -> int:
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        for email, name, role, password in USERS:
            if db.scalar(select(User).where(User.email == email)) is None:
                db.add(
                    User(
                        email=email,
                        full_name=name,
                        role=role,
                        hashed_password=hash_password(password),
                    )
                )
        db.commit()

        for spec in SCENARIOS:
            existing = db.scalar(select(Scenario).where(Scenario.name == spec.name))
            if existing is not None:
                db.execute(delete(Scenario).where(Scenario.id == existing.id))
                db.commit()
            scenario = create_scenario_from_spec(db, spec)
            print(
                f"  scenario {scenario.id:>2}  {scenario.name:<32} "
                f"{len(scenario.skus)} skus, {len(scenario.lines)} lines, "
                f"{scenario.periods} periods"
            )

    print("\nseeded users:")
    for email, _name, role, password in USERS:
        print(f"  {role:<8} {email:<40} {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

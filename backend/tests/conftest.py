"""API tests against in-memory SQLite."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.db import get_db
from app.main import app
from app.models import Base, User
from app.opt.scenario import ScenarioSize, build_scenario
from app.schemas import ScenarioCreate
from app.scenario_store import create_scenario_from_spec

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db():
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def client(db):
    def _override():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def users(db):
    records = {
        "planner": User(
            email="planner@lensplan.example.com",
            full_name="Priya Raman",
            role="planner",
            hashed_password=hash_password("planner-password"),
        ),
        "analyst": User(
            email="analyst@lensplan.example.com",
            full_name="Dan Mercer",
            role="analyst",
            hashed_password=hash_password("analyst-password"),
        ),
        "admin": User(
            email="admin@lensplan.example.com",
            full_name="Site Admin",
            role="admin",
            hashed_password=hash_password("admin-password"),
        ),
    }
    for user in records.values():
        db.add(user)
    db.commit()
    return records


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/token",
        data={"username": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def planner_headers(client, users):
    return login(client, "planner@lensplan.example.com", "planner-password")


@pytest.fixture
def analyst_headers(client, users):
    return login(client, "analyst@lensplan.example.com", "analyst-password")


@pytest.fixture
def admin_headers(client, users):
    return login(client, "admin@lensplan.example.com", "admin-password")


@pytest.fixture
def tiny_scenario(db):
    """Small enough that HiGHS proves optimality in well under a second."""
    spec = ScenarioCreate(
        name="Test network",
        description="fixture",
        skus=4,
        plants=1,
        lines_per_plant=1,
        dcs=2,
        periods=3,
        seed=3,
    )
    return create_scenario_from_spec(db, spec)


def test_generated_network_is_valid():
    data = build_scenario(ScenarioSize(skus=4, plants=1, lines_per_plant=1, dcs=2, periods=3), seed=3)
    assert data.periods == [1, 2, 3]
    assert len(data.skus) == 4

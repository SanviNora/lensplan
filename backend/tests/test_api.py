import pytest

from app.opt.marginal import evaluate_marginal_capacity
from app.opt.model import solve_plan
from app.opt.scenario import ScenarioSize, build_scenario
from app.opt.types import SolverName


def test_login_rejects_a_bad_password(client, users):
    response = client.post(
        "/api/auth/token",
        data={"username": "planner@lensplan.example.com", "password": "wrong"},
    )
    assert response.status_code == 401


def test_unauthenticated_requests_are_rejected(client):
    assert client.get("/api/scenarios").status_code == 401


def test_health_does_not_require_auth(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_analyst_cannot_create_a_scenario(client, analyst_headers):
    response = client.post(
        "/api/scenarios",
        headers=analyst_headers,
        json={
            "name": "blocked",
            "skus": 4,
            "plants": 1,
            "lines_per_plant": 1,
            "dcs": 2,
            "periods": 3,
        },
    )
    assert response.status_code == 403


def test_planner_creates_lists_and_reads_a_scenario(client, planner_headers):
    created = client.post(
        "/api/scenarios",
        headers=planner_headers,
        json={
            "name": "Peak what-if",
            "description": "demand up",
            "skus": 4,
            "plants": 1,
            "lines_per_plant": 1,
            "dcs": 2,
            "periods": 3,
            "seed": 3,
            "demand_surge": 1.2,
        },
    )
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]

    listed = client.get("/api/scenarios", headers=planner_headers)
    assert any(row["name"] == "Peak what-if" for row in listed.json())

    detail = client.get(f"/api/scenarios/{scenario_id}", headers=planner_headers)
    body = detail.json()
    assert body["sku_count"] == 4
    assert body["line_count"] == 1
    assert body["total_demand_units"] > 0


def test_duplicate_scenario_name_conflicts(client, planner_headers, tiny_scenario):
    response = client.post(
        "/api/scenarios",
        headers=planner_headers,
        json={
            "name": tiny_scenario.name,
            "skus": 4,
            "plants": 1,
            "lines_per_plant": 1,
            "dcs": 2,
            "periods": 3,
        },
    )
    assert response.status_code == 409


def test_exact_solve_persists_a_reconciled_plan(client, planner_headers, tiny_scenario):
    response = client.post(
        f"/api/scenarios/{tiny_scenario.id}/runs",
        headers=planner_headers,
        json={"method": "exact", "solver": "highs", "label": "baseline", "time_limit": 30},
    )
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["status"] in {"optimal", "feasible", "time_limit"}
    assert summary["total_demand_units"] > 0

    detail = client.get(f"/api/runs/{summary['id']}", headers=planner_headers).json()
    assert detail["costs"]["total"] == pytest.approx(summary["objective"], abs=1e-3)

    analytics = client.get(f"/api/runs/{summary['id']}/analytics", headers=planner_headers)
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["recommendation"]
    assert body["utilization"]


def test_planner_can_edit_capacity_then_re_solve(client, planner_headers, tiny_scenario):
    first = client.post(
        f"/api/scenarios/{tiny_scenario.id}/runs",
        headers=planner_headers,
        json={"method": "exact", "solver": "highs", "time_limit": 30},
    )
    assert first.status_code == 201
    line = tiny_scenario.lines[0].code
    patched = client.patch(
        f"/api/scenarios/{tiny_scenario.id}/capacity",
        headers=planner_headers,
        json=[{"line_code": line, "period": 1, "hours": 0.0}],
    )
    assert patched.status_code == 200
    second = client.post(
        f"/api/scenarios/{tiny_scenario.id}/runs",
        headers=planner_headers,
        json={"method": "exact", "solver": "highs", "label": "line down", "time_limit": 30},
    )
    assert second.status_code == 201
    assert second.json()["objective"] >= first.json()["objective"] - 1e-3


def test_only_admin_can_delete_a_scenario(client, planner_headers, admin_headers, tiny_scenario):
    denied = client.delete(f"/api/scenarios/{tiny_scenario.id}", headers=planner_headers)
    assert denied.status_code == 403
    allowed = client.delete(f"/api/scenarios/{tiny_scenario.id}", headers=admin_headers)
    assert allowed.status_code == 204
    missing = client.get(f"/api/scenarios/{tiny_scenario.id}", headers=admin_headers)
    assert missing.status_code == 404


def test_solvers_endpoint_lists_highs(client, planner_headers):
    body = client.get("/api/solvers", headers=planner_headers).json()
    assert "highs" in body["available"]


def test_benchmark_returns_a_gap(client, planner_headers, tiny_scenario):
    response = client.post(
        f"/api/scenarios/{tiny_scenario.id}/benchmark?time_limit=20&iterations=40",
        headers=planner_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exact_objective"] > 0
    assert body["heuristic_objective"] > 0
    assert "verdict" in body


def test_compare_two_runs(client, planner_headers, tiny_scenario):
    a = client.post(
        f"/api/scenarios/{tiny_scenario.id}/runs",
        headers=planner_headers,
        json={"method": "exact", "solver": "highs", "time_limit": 30},
    ).json()
    b = client.post(
        f"/api/scenarios/{tiny_scenario.id}/runs",
        headers=planner_headers,
        json={"method": "anneal", "solver": "highs", "iterations": 40, "time_limit": 20},
    ).json()
    compared = client.get(f"/api/runs/{a['id']}/compare/{b['id']}", headers=planner_headers)
    assert compared.status_code == 200
    assert "total_cost" in compared.json()


def test_unknown_run_is_404(client, planner_headers):
    assert client.get("/api/runs/99999", headers=planner_headers).status_code == 404


def test_marginal_capacity_cannot_make_the_plan_more_expensive():
    """Adding hours is a relaxation of the original feasible region.

    The integer objective after adding capacity must therefore be at most the original
    objective, up to solver gap. A rise would mean the probe mutated the wrong field.
    """
    data = build_scenario(
        ScenarioSize(skus=5, plants=1, lines_per_plant=2, dcs=2, periods=4), seed=3
    )
    baseline = solve_plan(data, SolverName.HIGHS, time_limit=45, with_sensitivity=True)
    results = evaluate_marginal_capacity(
        data, baseline, solver=SolverName.HIGHS, top_n=2, hours_added=2.0, time_limit=45
    )
    for row in results:
        assert row.total_saving >= -1.0

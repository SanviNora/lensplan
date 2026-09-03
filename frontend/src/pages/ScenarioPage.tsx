import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  BenchmarkResult,
  RunSummary,
  Scenario,
} from "../api";
import { money } from "./Scenarios";

export default function ScenarioPage() {
  const { id } = useParams();
  const scenarioId = Number(id);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [bench, setBench] = useState<BenchmarkResult | null>(null);

  async function refresh() {
    const [detail, list] = await Promise.all([api.scenario(scenarioId), api.runs(scenarioId)]);
    setScenario(detail);
    setRuns(list);
  }

  useEffect(() => {
    refresh().catch((err: Error) => setError(err.message));
  }, [scenarioId]);

  async function solve(method: "exact" | "anneal") {
    setError("");
    setBusy(method);
    try {
      await api.createRun(scenarioId, {
        method,
        solver: "highs",
        label: method === "exact" ? "Exact MIP" : "Simulated annealing",
        time_limit: 90,
        iterations: 80,
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "solve failed");
    } finally {
      setBusy("");
    }
  }

  async function runBenchmark() {
    setError("");
    setBusy("bench");
    try {
      setBench(await api.benchmark(scenarioId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "benchmark failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <div className="card">
        <h2>{scenario?.name ?? "Scenario"}</h2>
        <p className="muted">{scenario?.description}</p>
        <div className="stat-grid">
          <div>
            <div className="stat-label">SKUs</div>
            <div className="stat-value">{scenario?.sku_count ?? "—"}</div>
          </div>
          <div>
            <div className="stat-label">Lines</div>
            <div className="stat-value">{scenario?.line_count ?? "—"}</div>
          </div>
          <div>
            <div className="stat-label">Demand units</div>
            <div className="stat-value">{money(scenario?.total_demand_units ?? 0)}</div>
          </div>
        </div>
        <div className="actions-row" style={{ marginTop: "1rem" }}>
          <button disabled={!!busy} onClick={() => solve("exact")}>
            {busy === "exact" ? "Solving…" : "Solve exact MIP"}
          </button>
          <button className="secondary" disabled={!!busy} onClick={() => solve("anneal")}>
            {busy === "anneal" ? "Searching…" : "Run annealing"}
          </button>
          <button className="secondary" disabled={!!busy} onClick={runBenchmark}>
            {busy === "bench" ? "Benchmarking…" : "Exact vs heuristic"}
          </button>
        </div>
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          HiGHS is the default (90 s). Gurobi proves this network in about 1.5 s when a
          licence is present; HiGHS may return a feasible plan at the time limit rather
          than a proven optimum.
        </p>
        {error && <p className="error">{error}</p>}
      </div>

      {bench && (
        <div className="card">
          <h2>Exact solver vs simulated annealing</h2>
          <p>{bench.verdict}</p>
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Objective</th>
                <th>Seconds</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Exact MIP</td>
                <td>${money(bench.exact_objective)}</td>
                <td>{bench.exact_seconds.toFixed(2)}</td>
              </tr>
              <tr>
                <td>Annealing</td>
                <td>${money(bench.heuristic_objective)}</td>
                <td>{bench.heuristic_seconds.toFixed(2)}</td>
              </tr>
            </tbody>
          </table>
          <p className="muted">Gap {bench.gap_pct.toFixed(2)}%</p>
        </div>
      )}

      <div className="card">
        <h2>Runs</h2>
        <table>
          <thead>
            <tr>
              <th>Label</th>
              <th>Method</th>
              <th>Objective</th>
              <th>Service</th>
              <th>Seconds</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{run.label || run.method}</td>
                <td>
                  <span className="badge">{run.method}</span>
                </td>
                <td>${money(run.objective)}</td>
                <td>{(run.service_level * 100).toFixed(2)}%</td>
                <td>{run.solve_seconds.toFixed(2)}</td>
                <td>
                  <Link to={`/runs/${run.id}`}>Open</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

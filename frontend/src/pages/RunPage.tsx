import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, MarginalReport, RunAnalytics, RunDetail } from "../api";
import { money } from "./Scenarios";

function heatColor(util: number): string {
  const clamped = Math.max(0, Math.min(1, util));
  const r = Math.round(40 + 180 * clamped);
  const g = Math.round(180 - 120 * clamped);
  return `rgb(${r}, ${g}, 80)`;
}

function HeatRow({
  line,
  periods,
  cells,
}: {
  line: string;
  periods: number[];
  cells: { line_code: string; period: number; utilization: number }[];
}) {
  return (
    <>
      <div style={{ fontSize: "0.8rem" }}>{line}</div>
      {periods.map((period) => {
        const cell = cells.find((u) => u.line_code === line && u.period === period);
        const util = cell?.utilization ?? 0;
        return (
          <div
            key={`${line}-${period}`}
            className="heat-cell"
            style={{ background: heatColor(util) }}
            title={`${(util * 100).toFixed(0)}%`}
          >
            {(util * 100).toFixed(0)}
          </div>
        );
      })}
    </>
  );
}

export default function RunPage() {
  const { id } = useParams();
  const runId = Number(id);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [analytics, setAnalytics] = useState<RunAnalytics | null>(null);
  const [marginal, setMarginal] = useState<MarginalReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([api.run(runId), api.analytics(runId)])
      .then(([run, stats]) => {
        setDetail(run);
        setAnalytics(stats);
      })
      .catch((err: Error) => setError(err.message));
  }, [runId]);

  const maxCost = analytics
    ? Math.max(
        analytics.costs.production,
        analytics.costs.changeover,
        analytics.costs.holding,
        analytics.costs.freight,
        analytics.costs.stockout,
        1,
      )
    : 1;

  const lines = useMemo(() => {
    const set = new Set(analytics?.utilization.map((c) => c.line_code) ?? []);
    return Array.from(set);
  }, [analytics]);
  const periods = useMemo(() => {
    const set = new Set(analytics?.utilization.map((c) => c.period) ?? []);
    return Array.from(set).sort((a, b) => a - b);
  }, [analytics]);

  async function computeMarginal() {
    setBusy(true);
    setError("");
    try {
      setMarginal(await api.marginal(runId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  if (!detail || !analytics) {
    return <p className="muted">{error || "Loading…"}</p>;
  }

  const costs = [
    ["Production", analytics.costs.production],
    ["Changeover", analytics.costs.changeover],
    ["Holding", analytics.costs.holding],
    ["Freight", analytics.costs.freight],
    ["Stockout", analytics.costs.stockout],
  ] as const;

  return (
    <>
      <p>
        <Link to={`/scenarios/${detail.scenario_id}`}>← Scenario</Link>
      </p>
      <div className="card">
        <h2>{detail.label || `Run ${detail.id}`}</h2>
        <p className="muted">
          {detail.method} · {detail.solver} · {detail.status} · {detail.solve_seconds.toFixed(2)}s
        </p>
        <div className="stat-grid">
          <div>
            <div className="stat-label">Total cost</div>
            <div className="stat-value">${money(detail.objective)}</div>
          </div>
          <div>
            <div className="stat-label">Service level</div>
            <div className="stat-value">{(detail.service_level * 100).toFixed(2)}%</div>
          </div>
          <div>
            <div className="stat-label">Unmet units</div>
            <div className="stat-value">{money(detail.unmet_units)}</div>
          </div>
          <div>
            <div className="stat-label">Cost / delivered unit</div>
            <div className="stat-value">${analytics.cost_per_unit_delivered.toFixed(3)}</div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Recommendation</h2>
        <p>{analytics.recommendation}</p>
        <button className="secondary" disabled={busy} onClick={computeMarginal}>
          {busy ? "Re-solving…" : "Price an extra hour on the tightest lines"}
        </button>
        {marginal && (
          <>
            <p style={{ marginTop: "1rem" }}>{marginal.guidance}</p>
            <table>
              <thead>
                <tr>
                  <th>Line</th>
                  <th>Period</th>
                  <th>LP dual $/hr</th>
                  <th>Realised $/hr</th>
                  <th>Overstatement</th>
                </tr>
              </thead>
              <tbody>
                {marginal.results.map((row) => (
                  <tr key={`${row.line_code}-${row.period}`}>
                    <td>{row.line_code}</td>
                    <td>{row.period}</td>
                    <td>${row.lp_shadow_price.toFixed(2)}</td>
                    <td>${row.realised_value_per_hour.toFixed(2)}</td>
                    <td>{row.dual_overstatement.toFixed(2)}×</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      <div className="card">
        <h2>Cost breakdown</h2>
        {costs.map(([label, value]) => (
          <div key={label} style={{ display: "grid", gridTemplateColumns: "140px 1fr 90px", gap: "0.5rem", alignItems: "center", marginBottom: "0.4rem" }}>
            <span className="muted">{label}</span>
            <div className="bar" style={{ width: `${(value / maxCost) * 100}%` }} />
            <span>${money(value)}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <h2>Line utilisation</h2>
        <p className="muted">Executed plan hours / available hours. Green is slack, red is saturated.</p>
        <div className="heat" style={{ gridTemplateColumns: `120px repeat(${periods.length}, 1fr)` }}>
          <div />
          {periods.map((p) => (
            <div key={p} className="muted" style={{ textAlign: "center", fontSize: "0.75rem" }}>
              P{p}
            </div>
          ))}
          {lines.map((line) => (
            <HeatRow key={line} line={line} periods={periods} cells={analytics.utilization} />
          ))}
        </div>
      </div>

      <div className="card">
        <h2>Production runs</h2>
        <table>
          <thead>
            <tr>
              <th>Line</th>
              <th>SKU</th>
              <th>Period</th>
              <th>Units</th>
              <th>Hours</th>
            </tr>
          </thead>
          <tbody>
            {detail.production.slice(0, 40).map((row, i) => (
              <tr key={i}>
                <td>{row.line_code}</td>
                <td>{row.sku_code}</td>
                <td>{row.period}</td>
                <td>{money(row.units)}</td>
                <td>{row.hours.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {error && <p className="error">{error}</p>}
    </>
  );
}

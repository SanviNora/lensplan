const API = "/api";

function headers(): HeadersInit {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { ...headers(), ...(options.headers || {}) },
  });
  if (response.status === 401) {
    localStorage.removeItem("token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (response.status === 204) return undefined as T;
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

export async function login(email: string, password: string): Promise<string> {
  const body = new URLSearchParams({ username: email, password });
  const response = await fetch(`${API}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!response.ok) throw new Error("Invalid credentials");
  const data = await response.json();
  return data.access_token as string;
}

export interface Scenario {
  id: number;
  name: string;
  description: string;
  periods: number;
  sku_count?: number;
  line_count?: number;
  dc_count?: number;
  total_demand_units?: number;
}

export interface RunSummary {
  id: number;
  scenario_id: number;
  label: string;
  method: string;
  solver: string;
  status: string;
  objective: number;
  service_level: number;
  unmet_units: number;
  total_demand_units: number;
  solve_seconds: number;
  created_at: string;
}

export interface CostBreakdown {
  production: number;
  changeover: number;
  holding: number;
  freight: number;
  stockout: number;
  total: number;
}

export interface Bottleneck {
  line_code: string;
  period: number;
  shadow_price: number;
  hours_available: number;
  hours_used: number;
  hours_used_relaxed: number;
}

export interface UtilizationCell {
  line_code: string;
  period: number;
  hours_used: number;
  hours_available: number;
  utilization: number;
}

export interface RunDetail extends RunSummary {
  costs: CostBreakdown;
  production: { line_code: string; sku_code: string; period: number; units: number; hours: number; changeover: boolean }[];
  shipments: { plant_code: string; dc_code: string; sku_code: string; period: number; units: number }[];
  inventory: { dc_code: string; sku_code: string; period: number; closing_units: number; unmet_units: number }[];
  bottlenecks: Bottleneck[];
}

export interface RunAnalytics {
  run_id: number;
  costs: CostBreakdown;
  utilization: UtilizationCell[];
  cost_per_unit_delivered: number;
  changeover_count: number;
  top_bottlenecks: Bottleneck[];
  recommendation: string;
}

export interface BenchmarkResult {
  exact_objective: number;
  exact_seconds: number;
  exact_status: string;
  heuristic_objective: number;
  heuristic_seconds: number;
  heuristic_iterations: number;
  gap_pct: number;
  verdict: string;
}

export interface MarginalReport {
  run_id: number;
  guidance: string;
  results: {
    line_code: string;
    period: number;
    hours_added: number;
    lp_shadow_price: number;
    realised_value_per_hour: number;
    total_saving: number;
    dual_overstatement: number;
  }[];
}

export const api = {
  scenarios: () => request<Scenario[]>("/scenarios"),
  scenario: (id: number) => request<Scenario>(`/scenarios/${id}`),
  runs: (scenarioId: number) => request<RunSummary[]>(`/scenarios/${scenarioId}/runs`),
  createRun: (scenarioId: number, payload: Record<string, unknown>) =>
    request<RunSummary>(`/scenarios/${scenarioId}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  run: (id: number) => request<RunDetail>(`/runs/${id}`),
  analytics: (id: number) => request<RunAnalytics>(`/runs/${id}/analytics`),
  benchmark: (scenarioId: number) =>
    request<BenchmarkResult>(`/scenarios/${scenarioId}/benchmark?time_limit=90&iterations=80`, {
      method: "POST",
    }),
  marginal: (runId: number) =>
    request<MarginalReport>(`/runs/${runId}/marginal-value?top_n=3&hours=1`, { method: "POST" }),
  solvers: () => request<{ available: string[]; notes: string }>("/solvers"),
};

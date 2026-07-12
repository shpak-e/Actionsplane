import { getOperateToken } from "./lib/auth";
import type {
  Binding,
  Finding,
  Job,
  Metrics,
  Mode,
  PipelineGraph,
  Repo,
  Run,
  Scorecard,
} from "./types";

const BASE = "/api/v1";

/**
 * Attach the operate token (if the user set one) as `Authorization: Bearer`. Sent on reads too:
 * when the server has a token configured it gates reads as well, so the dashboard needs it to load;
 * in tokenless "open" mode no token exists here and the header is simply omitted (review 4, NEW-3).
 */
function authHeaders(): HeadersInit {
  const token = getOperateToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST", headers: authHeaders() });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  repos: () => get<Repo[]>("/repos"),
  runs: (params: { repo_id?: number; workflow_id?: number; branch?: string; status?: string }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v != null && q.set(k, String(v)));
    const qs = q.toString();
    return get<Run[]>(`/runs${qs ? `?${qs}` : ""}`);
  },
  jobs: (runId: number) => get<Job[]>(`/runs/${runId}/jobs`),
  metrics: (workflowId: number) => get<Metrics>(`/workflows/${workflowId}/metrics`),
  findings: (params: {
    repo_id?: number;
    severity?: string;
    finding_type?: string;
    limit?: number;
    offset?: number;
  }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v != null && q.set(k, String(v)));
    const qs = q.toString();
    // Paginated: { items, total } — total is the unpaginated count (see FindingsPage).
    return get<{ items: Finding[]; total: number }>(`/findings${qs ? `?${qs}` : ""}`);
  },
  scorecard: () => get<Scorecard>("/audit/scorecard"),
  drift: (repoId?: number) =>
    get<Binding[]>(`/drift${repoId != null ? `?repo_id=${repoId}` : ""}`),
  rerun: (runId: number) => post<{ status: string }>(`/runs/${runId}/rerun`),
  mode: () => get<Mode>("/mode"),
  offlineSync: () => post<Mode>("/offline/sync"),
  pipelines: () => get<PipelineGraph>("/pipelines"),
};

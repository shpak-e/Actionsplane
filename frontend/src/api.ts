import { getOperateToken } from "./lib/auth";
import type {
  Binding,
  Campaign,
  DriftDetail,
  Finding,
  Job,
  Metrics,
  Mode,
  PipelineGraph,
  Repo,
  Run,
  Scorecard,
  Workflow,
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

async function post<T>(path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { ...(authHeaders() as Record<string, string>) };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE", headers: authHeaders() });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  repos: () => get<Repo[]>("/repos"),
  allRepos: () => get<Repo[]>("/repos?watched_only=false"),
  addRepo: (body: { owner: string; name: string }) => post<Repo>("/repos", body),
  removeRepo: (id: number) => del<{ status: string; repo_id: number }>(`/repos/${id}`),
  workflows: (repoId: number) => get<Workflow[]>(`/repos/${repoId}/workflows`),
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
  driftDetail: (bindingId: number) => get<DriftDetail>(`/drift/${bindingId}/detail`),
  rerun: (runId: number) => post<{ status: string }>(`/runs/${runId}/rerun`),
  mode: () => get<Mode>("/mode"),
  offlineSync: () => post<Mode>("/offline/sync"),
  pipelines: () => get<PipelineGraph>("/pipelines"),
  createCampaign: (body: { name: string; operation: string; repo_ids: number[] }) =>
    post<Campaign>("/campaigns", body),
  applyCampaign: (id: number) => post<Campaign>(`/campaigns/${id}/apply`),
};

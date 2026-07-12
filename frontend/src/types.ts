export interface Repo {
  id: number;
  owner: string;
  name: string;
  default_branch: string;
  watched: boolean;
  archived: boolean;
}

export interface Run {
  id: number;
  repo_id: number;
  workflow_id: number | null;
  run_number: number;
  head_branch: string | null;
  head_sha: string | null;
  event: string | null;
  status: string | null;
  conclusion: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  actor: string | null;
}

export interface Step {
  name: string | null;
  status: string | null;
  conclusion: string | null;
  number: number | null;
}

export interface Job {
  id: number;
  run_id: number;
  name: string | null;
  status: string | null;
  conclusion: string | null;
  started_at: string | null;
  completed_at: string | null;
  runner_name: string | null;
  runner_group: string | null;
  steps: Step[];
}

export interface Metrics {
  runs: number;
  successes: number;
  failures: number;
  success_rate: number | null;
  p50_duration_s: number | null;
  p95_duration_s: number | null;
  p95_queue_s: number | null;
  flake_rate: number | null;
}

export interface LiveEnvelope {
  kind: "run" | "job";
  // Slim payload from the backend bus (build_envelope): just enough to scope a cache invalidation.
  data: {
    id?: number;
    repo_id?: number;
    run_id?: number;
    workflow_id?: number;
    status?: string;
    conclusion?: string;
    head_branch?: string;
  };
}

export interface Finding {
  id: number;
  repo_id: number;
  workflow_id: number | null;
  path: string | null;
  finding_type: string;
  severity: string;
  ref: string | null;
  message: string;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
}

export interface Scorecard {
  repos: number;
  open_findings: number;
  by_severity: Record<string, number>;
  by_type: Record<string, number>;
  score: number;
}

export interface Binding {
  id: number;
  repo_id: number;
  template_id: number;
  path: string;
  last_drift_check_at: string | null;
  drift_severity: string | null;
}

export interface Mode {
  offline: boolean;
  live: boolean;
  repos: string[];
  synced_at: string | null;
}

export interface PipelineNode {
  id: string;
  repo: string;
  path: string;
  name: string;
  external: boolean;
  badges: string[];
  status: string | null;
  conclusion: string | null;
  run_id: number | null;
  run_number: number | null;
  failed_job: string | null;
  failed_step: string | null;
}

export interface PipelineEdge {
  source: string;
  target: string;
  type: string; // triggers | calls | opens-pr | dispatch
  heuristic: boolean;
}

export interface PipelineGraph {
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  pipelines: string[][];
}

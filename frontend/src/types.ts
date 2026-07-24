export interface Repo {
  id: number;
  owner: string;
  name: string;
  default_branch: string;
  watched: boolean;
  archived: boolean;
}

export interface Workflow {
  id: number;
  repo_id: number;
  path: string;
  name: string | null;
  state: string;
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

export interface NotificationEvent {
  type: string;
  label: string;
  description: string;
}

export interface FleetCostRow {
  workflow_id: number;
  repo: string | null;
  name: string;
  runs: number;
  failures: number;
  total_minutes: number;
  avg_duration_s: number | null;
  p95_duration_s: number | null;
  p95_queue_s: number | null;
  last_run_at: string | null;
}

export interface FleetCost {
  window_days: number;
  total_minutes: number;
  rows: FleetCostRow[];
}

export interface OpenApiParameter {
  name: string;
  in: string;
  required?: boolean;
  schema?: { type?: string };
}

export interface OpenApiOperation {
  summary?: string;
  description?: string;
  tags?: string[];
  parameters?: OpenApiParameter[];
  requestBody?: { content?: Record<string, unknown> };
  responses?: Record<string, { description?: string }>;
}

export interface OpenApiDoc {
  info?: { title?: string; version?: string };
  paths: Record<string, Record<string, OpenApiOperation>>;
}

export interface NotificationChannel {
  id: number;
  kind: string; // slack | webhook | email
  name: string;
  events: string[];
  enabled: boolean;
  created_at: string | null;
  target_hint: string | null;
}

export interface NotificationTest {
  status: string; // "sent" | "error"
  detail: string | null;
}

export interface Template {
  id: number;
  name: string;
  version: number;
}

export interface Binding {
  id: number;
  repo_id: number;
  template_id: number;
  path: string;
  last_drift_check_at: string | null;
  drift_severity: string | null;
}

export interface DriftDetail {
  binding_id: number;
  repo: string;
  path: string;
  template: string;
  severity: string;
  changes: string[];
  canonical_yaml: string;
  candidate_yaml: string;
}

export interface Mode {
  offline: boolean;
  live: boolean;
  repos: string[];
  synced_at: string | null;
}

export interface RuleImpact {
  rule: string;
  workflows: number;
  repos: number;
  fix_operation: string | null;
  fixable_repo_ids: number[];
}

export interface SimulationReport {
  policy_rules: string[];
  workflows_evaluated: number;
  workflows_violating: number;
  repos_violating: number;
  by_rule: RuleImpact[];
  samples: Array<Record<string, unknown>>;
}

export interface RadarEntry {
  id: string;
  kind: string;
  target: string;
  replacement: string;
  deadline: string | null;
  days_until: number | null;
  status: string;
  reference: string;
  fix_operation: string | null;
  workflows: number;
  repos: number;
  fixable_repo_ids: number[];
  samples: Array<Record<string, unknown>>;
}

export interface RadarReport {
  as_of: string;
  workflows_scanned: number;
  entries: RadarEntry[];
}

export interface RequireCheckResult {
  repo_id: number;
  repo: string | null;
  status: string;
  detail: string | null;
  ruleset_id: number | null;
}

export interface RequireCheckOut {
  check: string;
  results: RequireCheckResult[];
}

export interface CampaignTarget {
  id: number;
  repo_id: number;
  status: string;
  pr_number: number | null;
  pr_url: string | null;
  diff_preview: string | null;
  error: string | null;
}

export interface Campaign {
  id: number;
  name: string;
  operation: string;
  status: string;
  created_at: string | null;
  targets: CampaignTarget[];
}

export interface CampaignSummary {
  id: number;
  name: string;
  operation: string;
  status: string;
  created_at: string | null;
  targets: number;
  opened: number;
  errored: number;
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

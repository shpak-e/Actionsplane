"""API response models — the read-model shapes the UI/CLI consume."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, field_validator

# A campaign operation name flows into a git ref (refs/heads/actionsplane/<op>-<id>), PR titles,
# and commit messages, so constrain it to a safe charset before it ever reaches those (review §4
# L-1). Registry-membership (is this an operation we actually implement?) is checked at the
# endpoint, where the OPERATIONS registry is already imported.
_OPERATION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class RepoOut(BaseModel):
    id: int
    owner: str
    name: str
    default_branch: str
    watched: bool
    archived: bool


class WorkflowOut(BaseModel):
    id: int
    repo_id: int
    path: str
    name: str | None
    state: str


class RunOut(BaseModel):
    id: int
    repo_id: int
    workflow_id: int | None
    run_number: int
    head_branch: str | None
    head_sha: str | None
    event: str | None
    status: str | None
    conclusion: str | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    actor: str | None


class MetricsOut(BaseModel):
    runs: int
    successes: int
    failures: int
    success_rate: float | None
    p50_duration_s: float | None
    p95_duration_s: float | None
    p95_queue_s: float | None
    flake_rate: float | None


class StepOut(BaseModel):
    """One step within a job (from GitHub's job ``steps`` array — the failed-step locus)."""

    name: str | None = None
    status: str | None = None
    conclusion: str | None = None
    number: int | None = None


class JobOut(BaseModel):
    id: int
    run_id: int
    name: str | None
    status: str | None
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None
    runner_name: str | None
    runner_group: str | None
    steps: list[StepOut] = []


class FindingOut(BaseModel):
    id: int
    repo_id: int
    workflow_id: int | None
    path: str | None
    finding_type: str
    severity: str
    ref: str | None
    message: str
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None


class FindingsPage(BaseModel):
    """A paginated slice of open findings plus the unpaginated total (review 3, P1.4)."""

    items: list[FindingOut]
    total: int


class ScorecardOut(BaseModel):
    repos: int
    open_findings: int
    by_severity: dict[str, int]
    by_type: dict[str, int]
    score: int


class PolicySimulateIn(BaseModel):
    """A proposed policy/ruleset to simulate against the fleet (W2). All rules default off."""

    require_sha_pinned: bool = False
    disallowed_triggers: list[str] = []
    require_permissions: bool = False


class RuleImpactOut(BaseModel):
    rule: str
    workflows: int
    repos: int
    fix_operation: str | None
    fixable_repo_ids: list[int]


class SimulationReportOut(BaseModel):
    policy_rules: list[str]
    workflows_evaluated: int
    workflows_violating: int
    repos_violating: int
    by_rule: list[RuleImpactOut]
    samples: list[dict]


class TemplateOut(BaseModel):
    id: int
    name: str
    version: int


class BindingOut(BaseModel):
    id: int
    repo_id: int
    template_id: int
    path: str
    last_drift_check_at: datetime | None
    drift_severity: str | None


class TemplateCreate(BaseModel):
    name: str
    canonical_yaml: str


class BindingCreate(BaseModel):
    template_id: int
    path: str


class CampaignCreate(BaseModel):
    name: str
    operation: str = "pin-shas"
    repo_ids: list[int]

    @field_validator("operation")
    @classmethod
    def _operation_charset(cls, v: str) -> str:
        if not _OPERATION_RE.match(v):
            raise ValueError("operation must match ^[A-Za-z0-9._-]+$")
        return v


class CampaignTargetOut(BaseModel):
    id: int
    repo_id: int
    status: str
    pr_number: int | None
    pr_url: str | None
    diff_preview: str | None
    error: str | None


class CampaignOut(BaseModel):
    id: int
    name: str
    operation: str
    status: str
    targets: list[CampaignTargetOut] = []


class AuditLogEntryOut(BaseModel):
    """One row of the append-only write-operation audit trail (plan §8, Phase 5.2)."""

    id: int
    occurred_at: datetime
    actor: str
    action: str
    target: str | None
    detail: dict | None


class ModeOut(BaseModel):
    """Whether the dashboard should show live updates (App mode) or a Sync button (offline)."""

    offline: bool
    live: bool
    repos: list[str] = []
    synced_at: datetime | None = None


class PipelineNode(BaseModel):
    id: str
    repo: str
    path: str
    name: str
    external: bool
    badges: list[str] = []
    # latest observed run for this workflow (None for external/unobserved nodes)
    status: str | None = None  # queued | in_progress | completed
    conclusion: str | None = None  # success | failure | cancelled | …
    run_id: int | None = None
    run_number: int | None = None
    failed_job: str | None = None  # when conclusion=failure: the job that failed
    failed_step: str | None = None  # …and the step within it


class PipelineEdge(BaseModel):
    source: str
    target: str
    type: str  # triggers | calls | opens-pr | dispatch
    heuristic: bool


class PipelineGraphOut(BaseModel):
    """Fleet-wide cross-workflow trigger/dependency graph + connected components."""

    nodes: list[PipelineNode]
    edges: list[PipelineEdge]
    pipelines: list[list[str]]  # node ids grouped into connected components, largest first

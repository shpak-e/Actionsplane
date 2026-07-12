"""Runtime configuration.

All settings are read from the environment (prefix ``ACTIONSPLANE_``) so the same
image runs in Docker Compose and Kubernetes without code changes. The GitHub App
private key is referenced by *path* — never inlined — so it can be mounted from a
KMS-backed secret in prod (see the Security Model in ``plan.md``).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACTIONSPLANE_",
        env_file=".env",
        extra="ignore",
    )

    # --- persistence ---
    database_url: str = "postgresql+asyncpg://actionsplane:actionsplane@localhost:5432/actionsplane"
    redis_url: str = "redis://localhost:6379/0"

    # --- GitHub App ---
    github_app_id: int | None = None
    github_app_private_key_path: str | None = None
    github_webhook_secret: str | None = Field(default=None, repr=False)
    github_api_url: str = "https://api.github.com"
    api_url: str = "http://localhost:8000"  # ActionsPlane API base (for the CLI)
    api_token: str | None = Field(default=None, repr=False)  # if set, /api/v1 requires it
    # Optional read-only token (minimal RBAC, plan §8 / Phase 5.2). When set, it grants access
    # to the read endpoints only; every mutating endpoint still demands the operate token
    # (``api_token``) and answers 403 to the read token. Fail-closed: configuring only the
    # read token leaves the write paths unreachable rather than open.
    api_read_token: str | None = Field(default=None, repr=False)

    # --- offline mode (no GitHub App; pull a fixed list of public repos on demand) ---
    # Comma-separated `owner/repo` or repo URLs. When set, the system runs read-only over the
    # public REST API (optionally authenticated with `github_token` for a higher rate limit),
    # fetching workflows/runs on startup and on a manual Sync — no webhooks, no live updates.
    offline_repos: str = ""
    github_token: str | None = Field(default=None, repr=False)  # plain PAT for offline reads

    # --- behaviour ---
    poll_interval_seconds: int = 300  # reconciliation safety net (see plan §4)
    # How far back the reconcile sweep asks GitHub for runs (server-side ``created>=`` filter,
    # review 3, 4b). A dropped webhook is redelivered within hours, so a day is ample; keeping it
    # tight means an idle repo's reconcile is a cheap 304 instead of paging deep history.
    reconcile_lookback_hours: int = 24
    fetch_concurrency: int = 8  # max repos processed in parallel by the sweeps
    # Per-installation rate-limit budget floor (Phase 5.5). When a sweep observes
    # X-RateLimit-Remaining below this, it stops starting new repos (gracefully — the rest are
    # picked up by the next sweep) instead of burning the budget the webhooks/API also need.
    # 0 disables the guard.
    rate_limit_floor: int = 250
    # Payload retention (Phase 5.6). ``raw_payload`` JSONB on runs/jobs is nulled after this many
    # days (normalized columns are kept, so history stays queryable); processed webhook delivery
    # ids are deleted after theirs. 0 disables that pruning dimension.
    raw_payload_retention_days: int = 90
    delivery_retention_days: int = 30
    bulk_edits_enabled: bool = False  # opt-in per install; gates contents:write
    # Opt-in (mirrors bulk_edits_enabled): only when true do we request `security_events: write`
    # and push findings to GitHub Code Scanning. Off by default so the scope isn't requested
    # unless the operator wants the Security-tab integration.
    security_events_enabled: bool = False

    # --- observability (OpenTelemetry tracing) ---
    # Off by default → all tracing hooks degrade to no-ops. When true, spans are exported via OTLP
    # and trace context is propagated across the arq queue (ingest → worker → audit → SARIF = one
    # trace). The exporter also honours the standard OTEL_EXPORTER_OTLP_* env vars.
    otel_enabled: bool = False
    otel_endpoint: str | None = None  # OTLP gRPC endpoint, e.g. http://otel-collector:4317

    @property
    def offline_repo_list(self) -> list[str]:
        """Parsed, de-whitespaced offline repo specs."""
        return [r.strip() for r in self.offline_repos.split(",") if r.strip()]

    @property
    def offline_mode(self) -> bool:
        """True when a list of offline repos is configured (no GitHub App needed)."""
        return bool(self.offline_repo_list)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (one read of the environment per process)."""
    return Settings()

"""The Provider protocol — what each CI/CD backend must implement.

Kept intentionally small: identity plus the pipeline parser. Run-ingest, file-fetch, and
PR/MR creation are provider-specific clients that satisfy the same shapes the GitHub layer
already uses; they're added per provider as the observe/edit pillars are ported.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """A CI/CD backend ActionsPlane can observe, audit, and edit."""

    name: str  # "github" | "gitlab" | ...

    def pipeline_glob(self) -> str:
        """Glob identifying this provider's pipeline files in a repo."""
        ...

"""Typed workflow AST.

We parse workflow YAML into these Pydantic models rather than reasoning over raw text
or regexes, because the audit/drift/edit engines need to reason structurally about
``jobs.*.steps[].uses``, ``permissions``, ``concurrency``, runner labels, etc. Round-trip
edits (preserving comments/formatting) are handled separately by ``ruamel.yaml`` in the
executor; these models are the *analysis* view.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Step(BaseModel):
    """A single step within a job."""

    id: str | None = None
    name: str | None = None
    uses: str | None = None  # e.g. "actions/checkout@v4" — None for a `run:` step
    run: str | None = None
    with_: dict[str, object] = Field(default_factory=dict, alias="with")
    env: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class Job(BaseModel):
    """A job within a workflow."""

    id: str  # the YAML key under `jobs:`
    name: str | None = None
    runs_on: str | list[str] | None = None
    permissions: dict[str, str] | str | None = None
    steps: list[Step] = Field(default_factory=list)
    uses: str | None = None  # reusable-workflow call at job level
    needs: list[str] = Field(default_factory=list)


class Workflow(BaseModel):
    """A parsed ``.github/workflows/*.yml`` file."""

    path: str
    name: str | None = None
    on: object = None  # trigger spec; kept loose, normalised by the audit engine
    permissions: dict[str, str] | str | None = None
    concurrency: dict[str, object] | str | None = None
    jobs: dict[str, Job] = Field(default_factory=dict)

    def all_uses(self) -> list[str]:
        """Every ``uses:`` reference in the workflow (step-level and job-level)."""
        refs: list[str] = []
        for job in self.jobs.values():
            if job.uses:
                refs.append(job.uses)
            refs.extend(s.uses for s in job.steps if s.uses)
        return refs

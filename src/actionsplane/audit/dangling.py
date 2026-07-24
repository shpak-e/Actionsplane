"""Dangling-reference diagnostics (UI-1 / F3).

A repo-level audit pass (needs the full set of a repo's workflows, so it can't run per-file): flag
workflows that point at something which doesn't exist in the repo, so a trigger or a job silently
never runs — an invisible failure mode no per-workflow linter catches:

* ``workflow_run:`` naming an upstream workflow no file in the repo defines → it never fires.
* ``uses: ./.github/workflows/x.yml`` (a local reusable workflow) whose file is absent → the job
  can't start.

Pure over the persisted relation descriptors (``name`` / ``workflow_run_upstreams`` / ``calls``), so
it needs no re-fetch and is trivially testable.
"""

from __future__ import annotations

from typing import Any

from actionsplane.audit.findings import Finding
from actionsplane.models.enums import FindingType, Severity


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def detect_dangling_references(items: list[dict[str, Any]]) -> list[tuple[str, Finding]]:
    """``items``: ``[{"path": str, "name": str|None, "descriptor": {...}}, ...]`` for one repo.

    Returns ``[(workflow_path, Finding), ...]`` — the path is where the dangling reference lives.
    """
    names = {it["name"] for it in items if it.get("name")}
    basenames = {_basename(it["path"]) for it in items}
    out: list[tuple[str, Finding]] = []

    for it in items:
        path = it["path"]
        desc = it.get("descriptor") or {}

        for upstream in desc.get("workflow_run_upstreams", []):
            if upstream and upstream not in names:
                out.append(
                    (
                        path,
                        Finding(
                            FindingType.DANGLING_WORKFLOW_RUN,
                            Severity.MEDIUM,
                            f"workflow_run triggers off '{upstream}', but no workflow in this repo "
                            "is named that — the trigger never fires.",
                            ref=upstream,
                        ),
                    )
                )

        for call in desc.get("calls", []):
            if call.get("repo") is not None:
                continue  # cross-repo reusable workflow — can't verify from this repo's files
            call_path = str(call.get("path") or "")
            if call_path and _basename(call_path) not in basenames:
                out.append(
                    (
                        path,
                        Finding(
                            FindingType.MISSING_REUSABLE_WORKFLOW,
                            Severity.HIGH,
                            f"Calls local reusable workflow '{call_path}', which doesn't exist in "
                            "the repo — the job fails to start.",
                            ref=call_path,
                        ),
                    )
                )

    return out

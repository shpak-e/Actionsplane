"""Workflow YAML → typed AST parser (plan §4, Phase 2).

Parses a workflow file into the ``Workflow`` / ``Job`` / ``Step`` Pydantic models the audit and
drift engines reason over. We load with ruamel in safe mode for analysis; the *editor* uses a
separate round-trip load to preserve comments when rewriting.

YAML 1.1 quirk: a bare ``on:`` key parses as the boolean ``True`` (likewise ``yes``/``no``), so we
normalise that back to the string ``"on"`` before building the model.
"""

from __future__ import annotations

import io
from typing import Any

from ruamel.yaml import YAML

from actionsplane.models.workflow import Job, Step, Workflow

_yaml = YAML(typ="safe")


def _normalize_on(data: dict[Any, Any]) -> Any:
    # `on:` -> True under YAML 1.1; recover the trigger spec from either key.
    if "on" in data:
        return data["on"]
    return data.get(True)


def _build_step(raw: dict[str, Any]) -> Step:
    return Step(
        id=raw.get("id"),
        name=raw.get("name"),
        uses=raw.get("uses"),
        run=raw.get("run"),
        **{"with": raw.get("with", {}) or {}},
        env=raw.get("env", {}) or {},
    )


def _build_job(job_id: str, raw: dict[str, Any]) -> Job:
    steps = [_build_step(s) for s in (raw.get("steps") or []) if isinstance(s, dict)]
    needs = raw.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    return Job(
        id=job_id,
        name=raw.get("name"),
        runs_on=raw.get("runs-on"),
        permissions=raw.get("permissions"),
        steps=steps,
        uses=raw.get("uses"),
        needs=needs or [],
    )


def parse_workflow(text: str, path: str) -> Workflow:
    """Parse workflow YAML text into a typed ``Workflow``. Raises on non-mapping documents."""
    data = _yaml.load(io.StringIO(text))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: workflow root is not a mapping")
    jobs_raw = data.get("jobs") or {}
    jobs = {jid: _build_job(jid, jraw) for jid, jraw in jobs_raw.items() if isinstance(jraw, dict)}
    return Workflow(
        path=path,
        name=data.get("name"),
        on=_normalize_on(data),
        permissions=data.get("permissions"),
        concurrency=data.get("concurrency"),
        jobs=jobs,
    )

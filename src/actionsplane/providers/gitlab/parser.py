"""Parse `.gitlab-ci.yml` into a typed pipeline model (GitLab provider, plan §13).

GitLab's config is a flat mapping: a fixed set of *reserved* keys (stages, variables, include,
default, workflow, …) plus everything-else-is-a-job. The supply-chain surface lives in
``include:`` — local files, other projects (pinned by ``ref:``), CI/CD **components**
(``…/comp@version``), remote URLs, and GitLab templates. We model includes + job names; the
include pinning analysis lives in ``audit.py``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from ruamel.yaml import YAML

_yaml = YAML(typ="safe")

# Top-level keys that are NOT jobs (GitLab keyword keys).
_RESERVED = {
    "stages",
    "variables",
    "include",
    "default",
    "workflow",
    "image",
    "services",
    "before_script",
    "after_script",
    "cache",
    "pages",
    "types",
    "spec",
}


@dataclass(frozen=True, slots=True)
class GitLabInclude:
    raw: dict[str, Any] | str
    kind: str  # local | project | component | remote | template | unknown
    target: str | None = None  # project path or component path
    ref: str | None = None  # ref/version pinning the include, if any


@dataclass(frozen=True, slots=True)
class GitLabPipeline:
    path: str
    includes: list[GitLabInclude] = field(default_factory=list)
    jobs: list[str] = field(default_factory=list)


def _classify_include_entry(entry: dict[str, Any] | str) -> GitLabInclude:
    if isinstance(entry, str):
        # bare string is a local include (or a list of them)
        return GitLabInclude(raw=entry, kind="local", target=entry)
    if "local" in entry:
        return GitLabInclude(raw=entry, kind="local", target=entry["local"])
    if "template" in entry:
        return GitLabInclude(raw=entry, kind="template", target=entry["template"])
    if "remote" in entry:
        return GitLabInclude(raw=entry, kind="remote", target=entry["remote"])
    if "component" in entry:
        comp = str(entry["component"])
        target, _, ref = comp.partition("@")
        return GitLabInclude(raw=entry, kind="component", target=target, ref=ref or None)
    if "project" in entry:
        return GitLabInclude(
            raw=entry, kind="project", target=entry["project"], ref=entry.get("ref")
        )
    return GitLabInclude(raw=entry, kind="unknown")


def _normalize_includes(node: Any) -> list[GitLabInclude]:
    if node is None:
        return []
    items = node if isinstance(node, list) else [node]
    return [_classify_include_entry(i) for i in items]


def parse_gitlab_ci(text: str, path: str = ".gitlab-ci.yml") -> GitLabPipeline:
    """Parse a `.gitlab-ci.yml` document into a :class:`GitLabPipeline`."""
    data = _yaml.load(io.StringIO(text))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: pipeline root is not a mapping")
    includes = _normalize_includes(data.get("include"))
    jobs = [
        key
        for key, val in data.items()
        if key not in _RESERVED and not str(key).startswith(".") and isinstance(val, dict)
    ]
    return GitLabPipeline(path=path, includes=includes, jobs=jobs)

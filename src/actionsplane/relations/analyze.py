"""Pure relations analysis (plan §13 / Pipelines).

Two pure functions, no I/O:

* ``extract_relations(workflow)`` distils one parsed ``Workflow`` into a compact, storable
  descriptor of how it connects to *other* workflows — what triggers it, what reusable workflows
  it calls, and what it emits (PRs / dispatches to other repos). Persisted per workflow during a
  sweep so the graph survives without re-fetching files.

* ``build_pipeline_graph(items)`` assembles those descriptors (across all repos) into a typed
  node/edge graph and groups it into connected components ("pipelines").

Edge precision is labelled: ``workflow_run`` / reusable ``calls`` / dispatch *listeners* are
precise (read straight from ``on:`` / ``uses:``); PR-opening and dispatch *senders* are heuristic
(pattern-matched from steps) and flagged ``heuristic: true`` so the UI can mark them.
"""

from __future__ import annotations

from typing import Any

from actionsplane.audit.pins import classify
from actionsplane.models.workflow import Workflow

# Actions/commands that signal a workflow *emits* a cross-workflow effect.
_PR_ACTIONS = ("peter-evans/create-pull-request", "repo-sync/pull-request", "gh-actions/create-pr")
_DISPATCH_ACTIONS = ("peter-evans/repository-dispatch", "benc-uk/workflow-dispatch")


def _trigger_map(on: Any) -> dict[str, Any]:
    """Normalise the loose ``on:`` spec to ``{trigger_name: config}``."""
    if on is None:
        return {}
    if isinstance(on, str):
        return {on: {}}
    if isinstance(on, list):
        return {t: {} for t in on if isinstance(t, str)}
    if isinstance(on, dict):
        return {str(k): (v or {}) for k, v in on.items()}
    return {}


def _is_workflow_path(path: str | None) -> bool:
    return bool(path) and path.endswith((".yml", ".yaml")) and "workflows" in path


def _emits(workflow: Workflow) -> list[dict[str, Any]]:
    """Heuristic: scan steps for PR-opening / dispatch-sending to other repos."""
    out: list[dict[str, Any]] = []
    for job in workflow.jobs.values():
        for step in job.steps:
            uses = (step.uses or "").split("@")[0]
            with_ = step.with_ or {}
            run = step.run or ""
            target = with_.get("repository") or with_.get("repo")
            target = str(target) if target else None
            if uses.startswith(_PR_ACTIONS) or "gh pr create" in run:
                out.append(
                    {"kind": "opens-pr", "target_repo": target, "detail": uses or "gh pr create"}
                )
            elif (
                uses.startswith(_DISPATCH_ACTIONS)
                or "/dispatches" in run
                or "gh workflow run" in run
            ):
                out.append({"kind": "dispatch", "target_repo": target, "detail": uses or "gh"})
    return out


def extract_relations(workflow: Workflow) -> dict[str, Any]:
    """Distil a workflow into its cross-workflow relation facts (storable JSON)."""
    triggers = _trigger_map(workflow.on)

    upstreams: list[str] = []
    wr = triggers.get("workflow_run")
    if isinstance(wr, dict):
        wf = wr.get("workflows")
        if isinstance(wf, str):
            upstreams = [wf]
        elif isinstance(wf, list):
            upstreams = [str(w) for w in wf]

    rd = triggers.get("repository_dispatch")
    dispatch_types: list[str] = []
    if isinstance(rd, dict) and isinstance(rd.get("types"), list):
        dispatch_types = [str(t) for t in rd["types"]]

    calls: list[dict[str, Any]] = []
    for job in workflow.jobs.values():
        if not job.uses:
            continue
        ref = classify(job.uses)
        if ref.action and _is_workflow_path(ref.subpath):  # cross-repo reusable workflow
            calls.append({"repo": ref.action, "path": ref.subpath})
        elif job.uses.startswith(("./", "../")) and _is_workflow_path(job.uses):  # local reusable
            calls.append({"repo": None, "path": job.uses.lstrip("./")})

    return {
        "name": workflow.name,
        "triggers": sorted(triggers),
        "workflow_run_upstreams": upstreams,
        "is_reusable": "workflow_call" in triggers,
        "accepts_dispatch": "repository_dispatch" in triggers or "workflow_dispatch" in triggers,
        "dispatch_types": dispatch_types,
        "calls": calls,
        "emits": _emits(workflow),
    }


def _node_id(repo: str, path: str) -> str:
    return f"{repo}:{path}"


def build_pipeline_graph(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble per-workflow descriptors into a node/edge graph + connected components.

    ``items``: ``[{"repo": "owner/name", "path": str, "descriptor": {...}}, ...]``.
    Returns ``{"nodes": [...], "edges": [...], "pipelines": [[node_id, ...], ...]}``.
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    by_name: dict[tuple[str, str], str] = {}  # (repo, workflow name) -> node id

    def ensure(repo: str, path: str, *, external: bool = False) -> str:
        nid = _node_id(repo, path)
        if nid not in nodes:
            nodes[nid] = {
                "id": nid,
                "repo": repo,
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "external": external,
                "badges": [],
                # latest-run status (None for external/unobserved nodes); filled below from items
                "status": None,
                "conclusion": None,
                "run_id": None,
                "run_number": None,
                "failed_job": None,
                "failed_step": None,
            }
        return nid

    # First pass: real nodes + name index.
    for it in items:
        d = it["descriptor"]
        nid = ensure(it["repo"], it["path"])
        node = nodes[nid]
        node["external"] = False
        node["name"] = d.get("name") or node["name"]
        if d.get("is_reusable"):
            node["badges"].append("reusable")
        if d.get("accepts_dispatch"):
            node["badges"].append("dispatch-listener")
        status = it.get("status")
        if status:  # latest-run status passed in by the API (None in pure/unit use)
            node.update(
                {
                    "status": status.get("status"),
                    "conclusion": status.get("conclusion"),
                    "run_id": status.get("run_id"),
                    "run_number": status.get("run_number"),
                    "failed_job": status.get("failed_job"),
                    "failed_step": status.get("failed_step"),
                }
            )
        if d.get("name"):
            by_name[(it["repo"], d["name"])] = nid

    # Second pass: edges.
    for it in items:
        repo, path, d = it["repo"], it["path"], it["descriptor"]
        this = _node_id(repo, path)

        for upstream_name in d.get("workflow_run_upstreams", []):
            src = by_name.get((repo, upstream_name))
            if src:
                edges.append(
                    {"source": src, "target": this, "type": "triggers", "heuristic": False}
                )

        for call in d.get("calls", []):
            target_repo = call.get("repo") or repo
            tid = ensure(target_repo, call["path"], external=target_repo != repo)
            edges.append({"source": this, "target": tid, "type": "calls", "heuristic": False})

        for emit in d.get("emits", []):
            trepo = emit.get("target_repo")
            if trepo and trepo != repo:
                tid = ensure(trepo, "(repository)", external=True)
                edges.append(
                    {"source": this, "target": tid, "type": emit["kind"], "heuristic": True}
                )
            else:
                label = "opens PR" if emit["kind"] == "opens-pr" else "sends dispatch"
                if label not in nodes[this]["badges"]:
                    nodes[this]["badges"].append(label)

    pipelines = _connected_components(nodes, edges)
    return {"nodes": list(nodes.values()), "edges": edges, "pipelines": pipelines}


def _connected_components(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]
) -> list[list[str]]:
    """Group nodes touched by ≥1 edge into connected components (undirected), largest first."""
    adj: dict[str, set[str]] = {nid: set() for nid in nodes}
    touched: set[str] = set()
    for e in edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
        touched.add(e["source"])
        touched.add(e["target"])

    seen: set[str] = set()
    components: list[list[str]] = []
    for start in nodes:
        if start in seen or start not in touched:
            continue
        stack, comp = [start], []
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            comp.append(n)
            stack.extend(adj[n] - seen)
        components.append(sorted(comp))
    components.sort(key=len, reverse=True)
    return components

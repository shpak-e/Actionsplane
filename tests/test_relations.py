"""Tests for the cross-workflow relations analyzer (Pipelines)."""

from __future__ import annotations

from actionsplane.audit.parser import parse_workflow
from actionsplane.relations import build_pipeline_graph, extract_relations

DEPLOY_WF = """
name: Deploy
on:
  workflow_run:
    workflows: [CI]
    types: [completed]
  workflow_dispatch:
jobs:
  call:
    uses: demo-org/infra/.github/workflows/apply.yml@v1
  pr:
    runs-on: ubuntu-latest
    steps:
      - uses: peter-evans/create-pull-request@v5
        with:
          repository: demo-org/other
"""


def test_extract_relations_reads_triggers_calls_and_emits():
    d = extract_relations(parse_workflow(DEPLOY_WF, ".github/workflows/deploy.yml"))
    assert d["name"] == "Deploy"
    assert d["workflow_run_upstreams"] == ["CI"]
    assert d["accepts_dispatch"] is True  # workflow_dispatch
    assert d["is_reusable"] is False
    assert {"repo": "demo-org/infra", "path": ".github/workflows/apply.yml"} in d["calls"]
    assert any(e["kind"] == "opens-pr" and e["target_repo"] == "demo-org/other" for e in d["emits"])


def test_reusable_workflow_call_marks_callee():
    wf = parse_workflow("on:\n  workflow_call:\njobs: {}\n", ".github/workflows/apply.yml")
    assert extract_relations(wf)["is_reusable"] is True


def _item(repo, path, descriptor):
    return {"repo": repo, "path": path, "descriptor": descriptor}


def test_build_graph_carries_node_status():
    ci = extract_relations(
        parse_workflow("name: CI\non: [push]\njobs: {}\n", ".github/workflows/ci.yml")
    )
    item = _item("demo-org/app", ".github/workflows/ci.yml", ci)
    item["status"] = {
        "status": "completed",
        "conclusion": "failure",
        "run_id": 42,
        "run_number": 7,
        "failed_job": "build",
        "failed_step": "Run tests",
    }
    node = build_pipeline_graph([item])["nodes"][0]
    assert node["conclusion"] == "failure"
    assert node["failed_step"] == "Run tests" and node["failed_job"] == "build"
    assert node["run_id"] == 42 and node["run_number"] == 7


def test_build_graph_status_defaults_none_without_runs():
    ci = extract_relations(
        parse_workflow("name: CI\non: [push]\njobs: {}\n", ".github/workflows/ci.yml")
    )
    node = build_pipeline_graph([_item("demo-org/app", ".github/workflows/ci.yml", ci)])["nodes"][0]
    assert node["conclusion"] is None and node["status"] is None and node["failed_step"] is None


def test_build_graph_links_workflow_run_calls_and_cross_repo_pr():
    ci = extract_relations(
        parse_workflow("name: CI\non: [push]\njobs: {}\n", ".github/workflows/ci.yml")
    )
    deploy = extract_relations(parse_workflow(DEPLOY_WF, ".github/workflows/deploy.yml"))
    release_wf = (
        "name: Release\non: [push]\njobs:\n"
        "  r:\n    uses: demo-org/infra/.github/workflows/apply.yml@v1\n"
    )
    release = extract_relations(parse_workflow(release_wf, ".github/workflows/release.yml"))
    graph = build_pipeline_graph(
        [
            _item("demo-org/app", ".github/workflows/ci.yml", ci),
            _item("demo-org/app", ".github/workflows/deploy.yml", deploy),
            _item("demo-org/web", ".github/workflows/release.yml", release),
        ]
    )

    types = {(e["source"], e["target"], e["type"]) for e in graph["edges"]}
    ci_id = "demo-org/app:.github/workflows/ci.yml"
    deploy_id = "demo-org/app:.github/workflows/deploy.yml"
    apply_id = "demo-org/infra:.github/workflows/apply.yml"

    # CI triggers Deploy (workflow_run, same repo, matched by name)
    assert (ci_id, deploy_id, "triggers") in types
    # Deploy + Release both call the cross-repo reusable apply.yml → external node exists
    assert any(t == (deploy_id, apply_id, "calls") for t in types)
    assert any(
        t == ("demo-org/web:.github/workflows/release.yml", apply_id, "calls") for t in types
    )
    assert any(n["id"] == apply_id and n["external"] for n in graph["nodes"])
    # the cross-repo PR is a heuristic edge
    assert any(e["type"] == "opens-pr" and e["heuristic"] for e in graph["edges"])
    # and at least one multi-node pipeline component was found
    assert any(len(c) >= 2 for c in graph["pipelines"])

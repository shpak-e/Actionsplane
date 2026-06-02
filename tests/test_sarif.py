"""SARIF emission (pure) + Code Scanning upload (MockTransport)."""

from __future__ import annotations

import base64
import gzip
import json

import httpx
import pytest

from actionsplane.audit.findings import Finding
from actionsplane.audit.sarif import findings_to_sarif
from actionsplane.github.client import GitHubClient
from actionsplane.models.enums import FindingType, Severity


def _findings():
    return [
        Finding(FindingType.UNPINNED_ACTION, Severity.HIGH, "x is unpinned", ref="x/y@main"),
        Finding(FindingType.MISSING_PERMISSIONS, Severity.MEDIUM, "no perms"),
        Finding(FindingType.DEPRECATED_ACTION, Severity.LOW, "old", ref="actions/x@v1"),
    ]


def test_sarif_shape():
    doc = findings_to_sarif(_findings())
    assert doc["version"] == "2.1.0"
    assert doc["$schema"].endswith("sarif-schema-2.1.0.json")
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "actionsplane"
    # one rule per distinct finding_type
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert rule_ids == {"unpinned_action", "missing_permissions", "deprecated_action"}
    # 3 results, with mapped levels
    assert len(run["results"]) == 3
    levels = sorted(r["level"] for r in run["results"])
    assert levels == ["error", "note", "warning"]


def test_sarif_partial_fingerprints_stable():
    # same logical findings emitted twice -> identical partialFingerprints (alert dedup)
    a = findings_to_sarif(_findings())
    b = findings_to_sarif(_findings())
    fa = [r["partialFingerprints"]["actionsplanePrimary/v1"] for r in a["runs"][0]["results"]]
    fb = [r["partialFingerprints"]["actionsplanePrimary/v1"] for r in b["runs"][0]["results"]]
    assert fa == fb
    assert all(len(fp) == 64 for fp in fa)  # sha256 hex


@pytest.mark.asyncio
async def test_upload_sarif_round_trip():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(202, json={"id": 42, "url": "https://api.github.com/x/42"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        doc = findings_to_sarif(_findings())
        result = await gh.upload_sarif(
            "acme", "infra", doc, commit_sha="d" * 40, ref="refs/heads/main"
        )

    assert result == {"id": 42, "url": "https://api.github.com/x/42"}
    assert captured["url"].endswith("/repos/acme/infra/code-scanning/sarifs")
    body = captured["body"]
    assert body["commit_sha"] == "d" * 40
    assert body["tool_name"] == "actionsplane"
    # round-trip the encoded sarif: gunzip+decode should reproduce our document
    recovered = json.loads(gzip.decompress(base64.b64decode(body["sarif"])))
    assert recovered == doc

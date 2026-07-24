"""D1 — ingest external SARIF (zizmor/poutine/Scorecard) as ActionsPlane findings."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.audit.findings import Finding
from actionsplane.audit.sarif_ingest import parse_sarif
from actionsplane.audit.sarif_ingest_service import ingest_sarif
from actionsplane.db.base import Base
from actionsplane.db.models import Installation, Repo
from actionsplane.db.repository import open_findings, upsert_finding
from actionsplane.models.enums import FindingType, Severity


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _zizmor_sarif(rule="template-injection", level="error", line=12, results=1):
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "zizmor",
                        "rules": [{"id": rule, "defaultConfiguration": {"level": level}}],
                    }
                },
                "results": [
                    {
                        "ruleId": rule,
                        "level": level,
                        "message": {"text": f"{rule} detected"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": ".github/workflows/ci.yml"},
                                    "region": {"startLine": line + i},
                                }
                            }
                        ],
                    }
                    for i in range(results)
                ],
            }
        ],
    }


def test_parse_sarif_maps_tool_namespaced_type_and_severity():
    findings = parse_sarif(_zizmor_sarif(rule="artipacked", level="warning"))
    assert len(findings) == 1
    f = findings[0]
    assert f.finding_type == "zizmor:artipacked"  # namespaced by tool
    assert f.severity is Severity.MEDIUM  # warning -> medium
    assert f.path == ".github/workflows/ci.yml"
    assert f.line == 12


def test_parse_sarif_security_severity_overrides_level():
    doc = _zizmor_sarif(level="warning")
    doc["runs"][0]["results"][0]["properties"] = {"security-severity": "9.1"}
    assert parse_sarif(doc)[0].severity is Severity.CRITICAL


def test_parse_sarif_distinct_lines_are_distinct_findings():
    rows = [f.as_row(repo_id=1) for f in parse_sarif(_zizmor_sarif(results=3))]
    assert len({r["fingerprint"] for r in rows}) == 3  # same rule, 3 lines -> 3 findings


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(
            Installation(
                id=1, account_login="acme", account_type="User", installed_at=datetime.now(UTC)
            )
        )
        s.add(Repo(id=10, installation_id=1, owner="acme", name="a"))
        await s.commit()
        yield s
    await engine.dispose()


async def test_ingest_persists_and_lifecycles_per_tool(session):
    # A native ActionsPlane finding that must survive external ingest untouched.
    native = Finding(FindingType.MISSING_PERMISSIONS, Severity.MEDIUM, "no perms").as_row(
        repo_id=10, path=".github/workflows/ci.yml"
    )
    native["first_seen_at"] = native["last_seen_at"] = datetime.now(UTC)
    await upsert_finding(session, native)
    await session.commit()

    r1 = await ingest_sarif(session, repo_id=10, doc=_zizmor_sarif(rule="template-injection"))
    assert r1 == {"ingested": 1, "resolved": 0, "tools": ["zizmor"]}

    types = {f.finding_type for f in await open_findings(session, repo_id=10)}
    assert "zizmor:template-injection" in types
    assert FindingType.MISSING_PERMISSIONS.value in types  # native finding untouched

    # Re-ingest with a DIFFERENT zizmor rule: the old zizmor finding resolves, the native stays.
    r2 = await ingest_sarif(session, repo_id=10, doc=_zizmor_sarif(rule="artipacked"))
    assert r2["ingested"] == 1
    assert r2["resolved"] == 1  # the template-injection finding closed

    open_types = {f.finding_type for f in await open_findings(session, repo_id=10)}
    assert open_types == {"zizmor:artipacked", FindingType.MISSING_PERMISSIONS.value}


async def test_ingest_endpoint_requires_operate_and_returns_summary(session):
    from actionsplane.api.app import ingest_repo_sarif_endpoint
    from actionsplane.api.schemas import SarifIngestIn

    out = await ingest_repo_sarif_endpoint(
        10, SarifIngestIn(sarif=_zizmor_sarif()), session=session, actor="tester"
    )
    assert out.ingested == 1
    assert out.tools == ["zizmor"]

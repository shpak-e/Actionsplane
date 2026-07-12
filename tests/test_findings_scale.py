"""DB-backed: the scorecard is exact above the old 1000-row cap, and /findings paginates
(review 3, item 6). Seeds 1,500 open findings on sqlite and drives the real handlers.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.api.app import get_findings, get_scorecard
from actionsplane.audit.findings import Finding
from actionsplane.db.base import Base
from actionsplane.db.repository import upsert_finding, upsert_repo
from actionsplane.models.enums import FindingType, Severity

_SEVERITIES = [Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.CRITICAL, Severity.INFO]
_N = 1500  # deliberately above the old default limit=1000


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await upsert_repo(
            s,
            {"id": 1, "owner": "demo", "name": "api", "default_branch": "main", "archived": False},
            installation_id=1,
        )
        for i in range(_N):
            sev = _SEVERITIES[i % len(_SEVERITIES)]
            # unique ref → unique fingerprint, so each is a distinct open finding
            row = Finding(FindingType.UNPINNED_ACTION, sev, "pin it", f"actions/x@v{i}").as_row(
                repo_id=1, path="ci.yml"
            )
            await upsert_finding(s, row)
        await s.commit()
        yield s
    await engine.dispose()


async def test_scorecard_is_exact_above_the_row_cap(session):
    out = await get_scorecard(session=session)
    assert out.open_findings == _N  # exact count, not a capped 1000-row slice
    assert sum(out.by_severity.values()) == _N
    assert out.by_severity[Severity.HIGH.value] == _N // len(_SEVERITIES)


async def test_findings_pagination_walks_all(session):
    seen: set[int] = set()
    page_size = 500
    for offset in range(0, _N, page_size):
        page = await get_findings(
            repo_id=None,
            severity=None,
            finding_type=None,
            limit=page_size,
            offset=offset,
            session=session,
        )
        assert page.total == _N  # unpaginated total travels with every page
        seen.update(f.id for f in page.items)
    assert len(seen) == _N  # every finding reached exactly once across the pages

"""Per-install rate-limit budget tracking (Phase 5.5).

The client snapshots the ``X-RateLimit-*`` headers on every response (MockTransport-fed here);
the worker's ``RateGate`` trips a sweep-wide pause once the observed budget dips under the
configured floor, so remaining repos defer to the next sweep instead of erroring.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from actionsplane.github.client import GitHubClient, RateBudget
from actionsplane.sync.worker import RateGate


def _client(handler) -> tuple[httpx.AsyncClient, GitHubClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return http, GitHubClient("tok", client=http, api_url="https://api.github.com")


async def test_rate_budget_parsed_from_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"workflow_runs": []},
            headers={
                "X-RateLimit-Remaining": "4321",
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Reset": "1780000000",
            },
        )

    http, gh = _client(handler)
    async with http:
        assert gh.rate_budget.remaining is None  # unknown until the first response
        await gh.list_workflow_runs("acme", "infra")

    budget = gh.rate_budget
    assert budget.remaining == 4321
    assert budget.limit == 5000
    assert budget.reset_at == datetime.fromtimestamp(1780000000, tz=UTC)


async def test_rate_budget_ignores_garbled_and_absent_headers():
    responses = iter(
        [
            {"X-RateLimit-Remaining": "100", "X-RateLimit-Limit": "5000"},
            {"X-RateLimit-Remaining": "not-a-number"},  # mangled → keep the old snapshot
            {},  # absent → keep the old snapshot
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"workflow_runs": []}, headers=next(responses))

    http, gh = _client(handler)
    async with http:
        for _ in range(3):
            await gh.list_workflow_runs("acme", "infra")

    assert gh.rate_budget.remaining == 100


def test_budget_below_floor_predicate():
    assert RateBudget(remaining=100).below(250) is True
    assert RateBudget(remaining=250).below(250) is False
    assert RateBudget(remaining=None).below(250) is False  # unknown → don't pause
    assert RateBudget(remaining=0).below(0) is False  # floor 0 disables the guard


async def test_rate_gate_trips_sweep_below_floor():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"workflow_runs": []}, headers={"X-RateLimit-Remaining": "42"}
        )

    gate = RateGate(floor=250)
    http, gh = _client(handler)
    async with http:
        await gh.list_workflow_runs("acme", "infra")

    assert gate.tripped is False
    gate.note(gh)
    assert gate.tripped is True  # 42 < 250 → the sweep's not-yet-started repos now skip


async def test_rate_gate_stays_open_with_healthy_budget():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"workflow_runs": []}, headers={"X-RateLimit-Remaining": "4800"}
        )

    gate = RateGate(floor=250)
    http, gh = _client(handler)
    async with http:
        await gh.list_workflow_runs("acme", "infra")
    gate.note(gh)
    assert gate.tripped is False

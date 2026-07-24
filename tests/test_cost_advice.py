"""Cost/time optimization advice heuristics."""

from __future__ import annotations

from actionsplane.metrics.cost_advice import cost_suggestions


def _kinds(suggestions):
    return {s.kind for s in suggestions}


def test_no_suggestions_for_cheap_healthy_workflow():
    out = cost_suggestions(
        runs=10,
        failures=0,
        total_minutes=5,
        p95_duration_s=60,
        p95_queue_s=5,
        fleet_minutes=1000,
    )
    assert out == []


def test_flags_high_failure_rate():
    out = cost_suggestions(
        runs=10,
        failures=4,
        total_minutes=20,
        p95_duration_s=90,
        p95_queue_s=5,
        fleet_minutes=1000,
    )
    assert "failures" in _kinds(out)
    assert any("40%" in s.message for s in out)


def test_small_sample_does_not_flag_failures():
    out = cost_suggestions(
        runs=3,
        failures=2,  # 66% but only 3 runs — too small to trust
        total_minutes=5,
        p95_duration_s=60,
        p95_queue_s=5,
        fleet_minutes=1000,
    )
    assert "failures" not in _kinds(out)


def test_queue_offers_concurrency_fix_only_when_missing():
    with_block = cost_suggestions(
        runs=10,
        failures=0,
        total_minutes=20,
        p95_duration_s=60,
        p95_queue_s=300,
        fleet_minutes=1000,
        has_concurrency=True,
    )
    without_block = cost_suggestions(
        runs=10,
        failures=0,
        total_minutes=20,
        p95_duration_s=60,
        p95_queue_s=300,
        fleet_minutes=1000,
        has_concurrency=False,
    )
    q_with = next(s for s in with_block if s.kind == "queue")
    q_without = next(s for s in without_block if s.kind == "queue")
    assert q_with.fix_operation is None
    assert q_without.fix_operation == "add-concurrency"


def test_flags_long_duration_and_fleet_share():
    out = cost_suggestions(
        runs=20,
        failures=0,
        total_minutes=300,  # 30% of the 1000-minute fleet
        p95_duration_s=1200,  # 20m
        p95_queue_s=5,
        fleet_minutes=1000,
    )
    kinds = _kinds(out)
    assert "duration" in kinds
    assert "share" in kinds

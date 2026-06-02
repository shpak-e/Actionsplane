"""Tests for the pure metrics functions."""

from __future__ import annotations

import pytest

from actionsplane.metrics import flake_rate, percentile, success_rate, summarize_runs


def test_percentile_basic():
    assert percentile([10], 50) == 10
    assert percentile([10, 20], 50) == 15.0
    assert percentile([1, 2, 3, 4], 0) == 1
    assert percentile([1, 2, 3, 4], 100) == 4
    assert percentile([], 50) is None


def test_percentile_p95():
    data = list(range(1, 101))  # 1..100
    assert percentile(data, 95) == pytest.approx(95.05, abs=0.01)


def test_percentile_bad_p():
    with pytest.raises(ValueError):
        percentile([1, 2], 150)


def test_success_rate():
    runs = [
        {"conclusion": "success"},
        {"conclusion": "success"},
        {"conclusion": "failure"},
        {"conclusion": None},  # in-progress, ignored
        {"conclusion": "cancelled"},  # not success/failure, ignored
    ]
    assert success_rate(runs) == pytest.approx(2 / 3)
    assert success_rate([]) is None
    assert success_rate([{"conclusion": None}]) is None


def test_flake_rate():
    runs = [
        {"head_sha": "a", "conclusion": "failure"},
        {"head_sha": "a", "conclusion": "success"},  # a is flaky (both)
        {"head_sha": "b", "conclusion": "success"},  # b stable
        {"head_sha": "c", "conclusion": "failure"},  # c stable-fail
    ]
    assert flake_rate(runs) == pytest.approx(1 / 3)
    assert flake_rate([]) is None


def test_summarize_runs():
    runs = [
        {"conclusion": "success", "head_sha": "a", "duration_s": 100.0, "queue_s": 5.0},
        {"conclusion": "failure", "head_sha": "a", "duration_s": 200.0, "queue_s": 15.0},
        {"conclusion": "success", "head_sha": "b", "duration_s": 300.0, "queue_s": 25.0},
    ]
    m = summarize_runs(runs)
    assert m.runs == 3
    assert m.successes == 2
    assert m.failures == 1
    assert m.success_rate == pytest.approx(2 / 3)
    assert m.p50_duration_s == pytest.approx(200.0)
    assert m.flake_rate == pytest.approx(0.5)  # sha "a" flaky of 2 shas

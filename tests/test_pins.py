"""Tests for the pin classifier — the highest-value pure logic in Phase 2."""

from __future__ import annotations

import pytest

from actionsplane.audit.pins import classify, is_pinned_safely
from actionsplane.models.enums import PinState

SHA = "8f4b7f84864484a7bf31766abe9204da3cbe65b3"  # 40 hex


@pytest.mark.parametrize(
    ("uses", "expected"),
    [
        (f"actions/checkout@{SHA}", PinState.SHA_PINNED),
        ("actions/checkout@v4", PinState.TAG_PINNED),
        ("actions/checkout@v4.1.2", PinState.TAG_PINNED),
        ("actions/checkout@4.1.2", PinState.TAG_PINNED),
        ("actions/checkout@main", PinState.BRANCH_PINNED),
        ("actions/checkout@master", PinState.BRANCH_PINNED),
        ("actions/checkout", PinState.UNPINNED),
        ("./.github/actions/setup", PinState.LOCAL),
        ("../shared/action", PinState.LOCAL),
        ("docker://alpine:3.20", PinState.DOCKER),
        ("actions/checkout@release-2024", PinState.UNKNOWN_REF),  # arbitrary ref — escalate
        ("actions/checkout@stable", PinState.UNKNOWN_REF),  # commonly mutable in practice
    ],
)
def test_classify_pin_state(uses: str, expected: PinState) -> None:
    assert classify(uses).pin_state is expected


def test_action_name_parsing() -> None:
    ref = classify(f"github/codeql-action/analyze@{SHA}")
    assert ref.owner == "github"
    assert ref.repo == "codeql-action"
    assert ref.subpath == "analyze"
    assert ref.action == "github/codeql-action"
    assert ref.ref == SHA


def test_is_pinned_safely() -> None:
    assert is_pinned_safely(f"actions/checkout@{SHA}") is True
    assert is_pinned_safely("./.github/actions/setup") is True
    assert is_pinned_safely("actions/checkout@v4") is False
    assert is_pinned_safely("actions/checkout@main") is False
    assert is_pinned_safely("actions/checkout") is False

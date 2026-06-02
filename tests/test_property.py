"""Property-based tests (hypothesis) for the two pure cores that must never surprise us:

* ``audit/pins.classify`` — the supply-chain verdict. SHA-pinning is the only safe state, so the
  classification must be stable and never mis-rank a 40/64-hex ref as anything but SHA-pinned.
* ``executor/operations.pin_workflow_to_sha`` — the flagship edit. It must be idempotent, must
  never lose existing comments, and must never emit YAML that fails to re-parse (a broken PR is
  worse than no PR).

These complement the example-based tests by hammering generated inputs at the invariants.
"""

from __future__ import annotations

import io

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from ruamel.yaml import YAML

from actionsplane.audit.pins import classify, is_pinned_safely
from actionsplane.executor.operations import pin_workflow_to_sha
from actionsplane.models.enums import PinState

# ---- strategies -------------------------------------------------------------------------------

_owners = st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=12)
_repos = st.text(alphabet="abcdefghijklmnopqrstuvwxyz-_.", min_size=1, max_size=16)
_shas = st.one_of(
    st.text(alphabet="0123456789abcdef", min_size=40, max_size=40),
    st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
)
_version_tags = st.builds(
    lambda v, parts: "v" * v + ".".join(str(p) for p in parts),
    st.integers(0, 1),  # optional leading 'v'
    st.lists(st.integers(0, 99), min_size=1, max_size=3),
)
_branches = st.sampled_from(["main", "master", "develop", "dev", "trunk"])


# ---- pins.classify ----------------------------------------------------------------------------


@given(_owners, _repos, _shas)
def test_sha_refs_always_classified_safe(owner, repo, sha):
    """Any owner/repo@<40|64-hex> is SHA-pinned and reported safe — never mis-ranked."""
    ref = classify(f"{owner}/{repo}@{sha}")
    assert ref.pin_state is PinState.SHA_PINNED
    assert is_pinned_safely(f"{owner}/{repo}@{sha}")
    assert ref.action == f"{owner}/{repo}"


@given(_owners, _repos, _version_tags)
def test_version_tags_are_tag_pinned(owner, repo, tag):
    assert classify(f"{owner}/{repo}@{tag}").pin_state is PinState.TAG_PINNED


@given(_owners, _repos, _branches)
def test_branch_refs_are_branch_pinned_and_unsafe(owner, repo, branch):
    uses = f"{owner}/{repo}@{branch}"
    assert classify(uses).pin_state is PinState.BRANCH_PINNED
    assert not is_pinned_safely(uses)


@given(st.text(min_size=1, max_size=40))
def test_classify_never_raises_and_is_idempotent_on_raw(s):
    """classify never throws on arbitrary input, and re-classifying its stripped raw is stable."""
    first = classify(s)
    second = classify(first.raw)
    assert second.pin_state is first.pin_state


# ---- operations.pin_workflow_to_sha -----------------------------------------------------------

_SHA = "d" * 40


def _workflow_with(uses_refs: list[str]) -> str:
    steps = "\n".join(f"      - uses: {u}  # keep this comment" for u in uses_refs)
    return (
        "name: ci  # top comment\n"
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"{steps}\n"
    )


def _count_comments(text: str) -> int:
    return sum(line.count("#") for line in text.splitlines())


def _reparses(text: str) -> bool:
    YAML().load(io.StringIO(text))
    return True


_uses_lists = st.lists(
    st.one_of(
        st.builds(lambda o, r, t: f"{o}/{r}@{t}", _owners, _repos, _version_tags),
        st.just("actions/checkout@main"),
        st.just(f"already/pinned@{'c' * 40}"),
        st.just("./.github/actions/local"),
    ),
    min_size=1,
    max_size=6,
)


@settings(max_examples=120, suppress_health_check=[HealthCheck.too_slow])
@given(_uses_lists)
def test_pin_is_idempotent_and_comment_preserving(uses_refs):
    resolver = lambda owner, repo, ref: _SHA  # noqa: E731 — resolve everything resolvable
    text = _workflow_with(uses_refs)
    before_comments = _count_comments(text)

    first = pin_workflow_to_sha(text, resolver)
    assert _reparses(first.new_text)  # never emits un-parseable YAML
    # existing comments are never dropped (pinning may *add* the tag-as-comment, never remove)
    assert _count_comments(first.new_text) >= before_comments

    # second pass is a no-op: everything resolvable is now a SHA -> nothing left to change
    second = pin_workflow_to_sha(first.new_text, resolver)
    assert second.changes == []
    assert second.new_text == first.new_text


@settings(max_examples=120, suppress_health_check=[HealthCheck.too_slow])
@given(_uses_lists)
def test_pinned_output_has_no_remaining_tag_or_branch_refs(uses_refs):
    """After pinning with a total resolver, no step is left tag- or branch-pinned."""
    resolver = lambda owner, repo, ref: _SHA  # noqa: E731
    result = pin_workflow_to_sha(_workflow_with(uses_refs), resolver)
    data = YAML().load(io.StringIO(result.new_text))
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            if hasattr(step, "get") and "uses" in step:
                assert classify(step["uses"]).pin_state not in (
                    PinState.TAG_PINNED,
                    PinState.BRANCH_PINNED,
                )

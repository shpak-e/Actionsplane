"""Resolve which of a workflow's tag-pinned refs are backed by GitHub immutable releases (W1).

The pin classifier is pure, so it can't know whether ``actions/checkout@v4`` is an immutable
release — that needs the API. This is the thin I/O layer that resolves it: classify the refs, and
for each distinct tag-pinned ``owner/repo@tag`` ask GitHub whether that tag is immutable. The result
is a frozenset of ``owner/repo@ref`` keys the audit engine and the pin-shas campaign both consult,
so they agree: an immutable tag is neither flagged nor rewritten to a raw SHA.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from actionsplane.audit.pins import classify, ref_key
from actionsplane.models.enums import PinState

if TYPE_CHECKING:
    from actionsplane.github.client import GitHubClient


async def resolve_immutable_refs(gh: GitHubClient, uses_refs: Iterable[str]) -> frozenset[str]:
    """Return the ``owner/repo@ref`` keys among ``uses_refs`` proven to be immutable releases.

    One API call per *distinct* tag-pinned ref (deduped); SHA/branch/local/docker refs are skipped
    for free. A lookup failure resolves to "not immutable" (see ``is_immutable_release``), so this
    only ever shrinks the flagged/rewritten set on solid evidence — never expands the safe set on a
    guess.
    """
    candidates: dict[str, tuple[str, str, str]] = {}
    for raw in uses_refs:
        u = classify(raw)
        if u.pin_state is PinState.TAG_PINNED and u.owner and u.repo and u.ref:
            candidates[ref_key(u.owner, u.repo, u.ref)] = (u.owner, u.repo, u.ref)

    immutable: set[str] = set()
    for key, (owner, repo, tag) in candidates.items():
        if await gh.is_immutable_release(owner, repo, tag):
            immutable.add(key)
    return frozenset(immutable)

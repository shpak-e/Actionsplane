"""InstallationCache is a real LRU bounded by entries AND bytes (review 4, NEW-7).

The ETag cache is now long-lived (per installation, across sweeps), so unbounded growth or a
plain FIFO would either leak memory or evict hot URLs. These pure tests pin the eviction policy.
"""

from __future__ import annotations

from actionsplane.github.client import InstallationCache


def test_get_returns_none_for_absent_key():
    assert InstallationCache().get("nope") is None


def test_store_then_get_roundtrips_etag_and_body():
    c = InstallationCache()
    c.store("k", "etag-1", {"hello": "world"}, 10)
    assert c.get("k") == ("etag-1", {"hello": "world"})


def test_entry_cap_evicts_oldest():
    c = InstallationCache(cap=2)
    c.store("a", "e", "A", 1)
    c.store("b", "e", "B", 1)
    c.store("c", "e", "C", 1)  # pushes "a" out
    assert c.get("a") is None
    assert c.get("b") == ("e", "B")
    assert c.get("c") == ("e", "C")


def test_lru_hit_protects_a_hot_key_from_eviction():
    c = InstallationCache(cap=2)
    c.store("a", "e", "A", 1)
    c.store("b", "e", "B", 1)
    assert c.get("a") == ("e", "A")  # touch "a" → now most-recently-used
    c.store("c", "e", "C", 1)  # should evict "b" (the LRU), not "a"
    assert c.get("a") == ("e", "A")
    assert c.get("b") is None
    assert c.get("c") == ("e", "C")


def test_byte_cap_evicts_until_under_budget():
    c = InstallationCache(cap=100, max_bytes=100)
    c.store("a", "e", "A", 60)
    c.store("b", "e", "B", 60)  # 120 > 100 → oldest ("a") evicted
    assert c.get("a") is None
    assert c.get("b") == ("e", "B")
    assert c._bytes == 60


def test_replacing_a_key_updates_byte_accounting():
    c = InstallationCache(cap=100, max_bytes=1000)
    c.store("a", "e1", "small", 10)
    c.store("a", "e2", "bigger", 40)  # same key, new size — not a second entry
    assert len(c.etag_cache) == 1
    assert c._bytes == 40
    assert c.get("a") == ("e2", "bigger")


def test_oversized_body_is_not_retained():
    c = InstallationCache(cap=100, max_bytes=100)
    c.store("huge", "e", "X", 250)  # bigger than the whole budget on its own
    assert c.get("huge") is None  # added then immediately evicted; safe to re-fetch later
    assert c._bytes == 0

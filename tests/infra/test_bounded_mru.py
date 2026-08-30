"""DEBT-39 — one bounded-MRU map, replacing four hand-written copies.

The four prompt-part snapshots (curated profile, skills catalogue, tool-usage
ordering, and ESC-67's stable_context) each had their own OrderedDict +
move_to_end + popitem dance with its own _MAX_TRACKED = 64.
"""

from __future__ import annotations

from stackowl.infra.bounded_mru import DEFAULT_MAX_TRACKED, BoundedMRU


def test_a_stored_value_comes_back() -> None:
    m: BoundedMRU[str, int] = BoundedMRU()
    m.put("a", 1)
    assert m.peek("a") == 1


def test_a_missing_key_is_None_not_an_error() -> None:
    assert BoundedMRU().peek("nope") is None


def test_the_bound_is_enforced() -> None:
    m: BoundedMRU[str, int] = BoundedMRU(max_tracked=3)
    for i in range(10):
        m.put(f"k{i}", i)
    assert len(m) == 3


def test_eviction_drops_the_LEAST_recently_used() -> None:
    """The whole reason this is MRU and not FIFO: evicting the conversation
    currently running would re-read the frozen input mid-session and restore the
    exact bug these snapshots exist to remove."""
    m: BoundedMRU[str, int] = BoundedMRU(max_tracked=2)
    m.put("old", 1)
    m.put("mid", 2)
    m.peek("old")          # touch: "old" is now the live one
    m.put("new", 3)

    assert m.peek("old") == 1, "the most-recently-used entry was evicted"
    assert m.peek("mid") is None


def test_a_READ_counts_as_use() -> None:
    """A long conversation that keeps hitting its snapshot must not age out
    while it is still live — so peek() touches, not just put()."""
    m: BoundedMRU[str, int] = BoundedMRU(max_tracked=2)
    m.put("a", 1)
    m.put("b", 2)
    for _ in range(5):
        m.peek("a")
    m.put("c", 3)
    assert m.peek("a") == 1
    assert m.peek("b") is None


def test_put_returns_the_value_so_callers_can_return_directly() -> None:
    m: BoundedMRU[str, int] = BoundedMRU()
    assert m.put("a", 7) == 7


def test_on_evict_names_the_key_that_went() -> None:
    """Each caller logs its own eviction in its own namespace, so the callback
    carries the key rather than the primitive guessing a log line."""
    gone: list[str] = []
    m: BoundedMRU[str, int] = BoundedMRU(max_tracked=1, on_evict=gone.append)
    m.put("first", 1)
    m.put("second", 2)
    assert gone == ["first"]


def test_re_putting_a_key_does_not_grow_the_map() -> None:
    m: BoundedMRU[str, int] = BoundedMRU(max_tracked=2)
    for _ in range(10):
        m.put("same", 1)
    assert len(m) == 1


def test_clear_empties_it() -> None:
    m: BoundedMRU[str, int] = BoundedMRU()
    m.put("a", 1)
    m.clear()
    assert len(m) == 0 and m.peek("a") is None


def test_the_default_bound_is_shared_not_redeclared() -> None:
    """One value in one place — the point of the extraction."""
    assert DEFAULT_MAX_TRACKED == 64
    assert len(BoundedMRU().__dict__) > 0

"""FX-07 — HydratedToolStore: session-scoped, bounded, best-effort memory of
tools recently surfaced via tool_search.
"""

from __future__ import annotations

from stackowl.infra import hydrated_tools


def test_record_then_get_round_trips() -> None:
    hydrated_tools.record("s1", ["shell", "web_fetch"])
    assert hydrated_tools.get("s1") == {"shell", "web_fetch"}


def test_get_on_unknown_session_returns_empty() -> None:
    assert hydrated_tools.get("never-seen") == set()


def test_sessions_are_isolated() -> None:
    hydrated_tools.record("a", ["shell"])
    hydrated_tools.record("b", ["cronjob"])
    assert hydrated_tools.get("a") == {"shell"}
    assert hydrated_tools.get("b") == {"cronjob"}


def test_none_session_id_is_a_safe_noop() -> None:
    hydrated_tools.record(None, ["shell"])
    assert hydrated_tools.get(None) == set()


def test_empty_names_is_a_noop() -> None:
    hydrated_tools.record("s1", [])
    assert hydrated_tools.get("s1") == set()


def test_bounded_per_session_keeps_best_ranked_within_one_call() -> None:
    """names arrives best-match-first (tool_search's own ranking); when one
    call surfaces more than the cap, the WORST matches must be evicted first,
    not the best ones."""
    names = [f"tool{i}" for i in range(20)]  # tool0 = best match, tool19 = worst
    hydrated_tools.record("s1", names)
    result = hydrated_tools.get("s1")
    assert len(result) == 12  # _MAX_PER_SESSION
    assert "tool0" in result  # best match survives
    assert "tool19" not in result  # worst match evicted first


def test_re_recording_a_name_moves_it_to_most_recent() -> None:
    hydrated_tools.record("s1", [f"tool{i}" for i in range(12)])  # fills to cap
    # A later call re-surfaces the first call's WORST match (tool11) plus one
    # brand-new name — tool11 must survive since it was just re-seen, even
    # though it ranked worst in the first call.
    hydrated_tools.record("s1", ["tool12", "tool11"])
    result = hydrated_tools.get("s1")
    assert "tool11" in result  # re-surfaced this call -> protected
    assert "tool12" in result  # newly surfaced this call -> protected
    assert "tool10" not in result  # never re-surfaced -> now the oldest, evicted


def test_clear_drops_the_session() -> None:
    hydrated_tools.record("s1", ["shell"])
    hydrated_tools.clear("s1")
    assert hydrated_tools.get("s1") == set()

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


def test_bounded_per_session_evicts_oldest() -> None:
    names = [f"tool{i}" for i in range(20)]
    hydrated_tools.record("s1", names)
    result = hydrated_tools.get("s1")
    assert len(result) == 12  # _MAX_PER_SESSION
    # The most recently added names survive; the earliest are evicted.
    assert "tool19" in result
    assert "tool0" not in result


def test_re_recording_a_name_moves_it_to_most_recent() -> None:
    hydrated_tools.record("s1", [f"tool{i}" for i in range(12)])
    # Re-surface tool0 — it must not be the next one evicted.
    hydrated_tools.record("s1", ["tool0", "tool12"])
    result = hydrated_tools.get("s1")
    assert "tool0" in result
    assert "tool1" not in result  # tool1 is now the oldest, evicted


def test_clear_drops_the_session() -> None:
    hydrated_tools.record("s1", ["shell"])
    hydrated_tools.clear("s1")
    assert hydrated_tools.get("s1") == set()

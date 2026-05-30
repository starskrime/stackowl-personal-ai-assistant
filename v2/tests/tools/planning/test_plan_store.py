"""Tests for the shared :class:`PlanStore` — the one plan slot behind todo + update_plan."""

from __future__ import annotations

from stackowl.tools.planning.store import VALID_STATUSES, PlanStore


def test_status_enum_matches_ported_set() -> None:
    # Confirm the status enum 1:1 against the ported substrate.
    assert {"pending", "in_progress", "completed", "cancelled"} == VALID_STATUSES


def test_replace_adds_items() -> None:
    store = PlanStore()
    items = store.replace(
        [
            {"id": "1", "content": "research", "status": "in_progress"},
            {"id": "2", "content": "write", "status": "pending"},
        ]
    )
    assert [i.id for i in items] == ["1", "2"]
    assert items[0].status == "in_progress"


def test_dedupe_by_id_last_wins_position_kept() -> None:
    store = PlanStore()
    items = store.replace(
        [
            {"id": "a", "content": "first", "status": "pending"},
            {"id": "b", "content": "other", "status": "pending"},
            {"id": "a", "content": "updated", "status": "completed"},
        ]
    )
    # One 'a' entry, in its first position, carrying the LAST occurrence's value.
    assert [i.id for i in items] == ["a", "b"]
    assert items[0].content == "updated"
    assert items[0].status == "completed"


def test_merge_updates_existing_and_appends_new() -> None:
    store = PlanStore()
    store.replace([{"id": "1", "content": "step one", "status": "pending"}])
    items = store.merge(
        [
            {"id": "1", "status": "completed"},  # update existing
            {"id": "2", "content": "step two", "status": "pending"},  # new
        ]
    )
    by_id = {i.id: i for i in items}
    assert by_id["1"].status == "completed"
    assert by_id["1"].content == "step one"  # untouched field preserved
    assert by_id["2"].content == "step two"
    assert [i.id for i in items] == ["1", "2"]  # order preserved


def test_replace_clears_prior_state() -> None:
    store = PlanStore()
    store.replace([{"id": "old", "content": "stale", "status": "pending"}])
    items = store.replace([{"id": "new", "content": "fresh", "status": "pending"}])
    assert [i.id for i in items] == ["new"]


def test_single_in_progress_auto_correct() -> None:
    store = PlanStore()
    items = store.replace(
        [
            {"id": "1", "content": "a", "status": "in_progress"},
            {"id": "2", "content": "b", "status": "in_progress"},
            {"id": "3", "content": "c", "status": "in_progress"},
        ]
    )
    statuses = {i.id: i.status for i in items}
    # First stays in_progress; the rest are demoted to pending (NOT rejected).
    assert statuses["1"] == "in_progress"
    assert statuses["2"] == "pending"
    assert statuses["3"] == "pending"
    in_progress = [i for i in items if i.status == "in_progress"]
    assert len(in_progress) == 1


def test_merge_also_enforces_single_in_progress() -> None:
    # Merging item 2 as in_progress while item 1 is active KEEPS item 2 (the
    # just-touched item) and demotes item 1 — "start the next step" intent wins.
    store = PlanStore()
    store.replace([{"id": "1", "content": "a", "status": "in_progress"}])
    items = store.merge([{"id": "2", "content": "b", "status": "in_progress"}])
    in_progress = [i.id for i in items if i.status == "in_progress"]
    assert in_progress == ["2"]


def test_format_for_injection_shape() -> None:
    store = PlanStore()
    store.replace(
        [
            {"id": "1", "content": "do research", "status": "in_progress"},
            {"id": "2", "content": "write up", "status": "pending"},
            {"id": "3", "content": "done thing", "status": "completed"},
        ]
    )
    rendered = store.format_for_injection()
    assert rendered is not None
    lines = rendered.splitlines()
    assert lines[0].startswith("[Your active task list")
    # Completed item is omitted from re-injection.
    assert "done thing" not in rendered
    assert "[>] 1. do research (in_progress)" in rendered
    assert "[ ] 2. write up (pending)" in rendered


def test_format_for_injection_empty_is_none() -> None:
    assert PlanStore().format_for_injection() is None
    # Only-completed plan also has nothing active to re-inject.
    store = PlanStore()
    store.replace([{"id": "1", "content": "x", "status": "completed"}])
    assert store.format_for_injection() is None


def test_malformed_items_are_normalised_not_raised() -> None:
    store = PlanStore()
    items = store.replace(
        [
            {"content": "no id"},  # missing id -> "?"
            {"id": "2"},  # missing content -> placeholder
            {"id": "3", "content": "c", "status": "bogus"},  # bad status -> pending
            "junk",  # non-mapping -> skipped
        ]
    )
    by_id = {i.id: i for i in items}
    assert by_id["?"].content == "no id"
    assert by_id["2"].content == "(no description)"
    assert by_id["3"].status == "pending"
    assert "junk" not in [i.id for i in items]


def test_clear_empties_plan() -> None:
    store = PlanStore()
    store.replace([{"id": "1", "content": "a", "status": "pending"}])
    assert store.clear() == []
    assert not store.has_items()


def test_counts() -> None:
    store = PlanStore()
    store.replace(
        [
            {"id": "1", "content": "a", "status": "in_progress"},
            {"id": "2", "content": "b", "status": "pending"},
            {"id": "3", "content": "c", "status": "completed"},
        ]
    )
    counts = store.counts()
    assert counts["total"] == 3
    assert counts["in_progress"] == 1
    assert counts["pending"] == 1
    assert counts["completed"] == 1


def test_set_status_advances_active_step() -> None:
    # MAJOR fix: starting step 2 while step 1 is in_progress keeps step 2 active
    # (the just-touched item wins), not silently undone.
    s = PlanStore()
    s.replace([{"id": "1", "content": "a", "status": "in_progress"},
               {"id": "2", "content": "b", "status": "pending"}])
    s.merge([{"id": "2", "status": "in_progress"}])  # "start step 2"
    by_id = {it.id: it.status for it in s.read()}
    assert by_id["2"] == "in_progress"
    assert by_id["1"] == "pending"  # the older active step was demoted, not step 2
    assert "1" in s.last_demoted()


def test_replace_keeps_first_in_progress() -> None:
    # Whole-plan replace has no per-item intent → keep the first in_progress.
    s = PlanStore()
    s.replace([{"id": "1", "content": "a", "status": "in_progress"},
               {"id": "2", "content": "b", "status": "in_progress"}])
    by_id = {it.id: it.status for it in s.read()}
    assert by_id["1"] == "in_progress" and by_id["2"] == "pending"


def test_format_for_injection_caps_large_plan() -> None:
    s = PlanStore()
    s.replace([{"id": str(i), "content": f"step {i}", "status": "pending"} for i in range(200)])
    out = s.format_for_injection() or ""
    assert "more active items not shown" in out
    assert out.count("\n- ") <= 51  # 50 items + the overflow footer line

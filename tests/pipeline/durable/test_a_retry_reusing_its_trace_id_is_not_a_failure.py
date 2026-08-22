"""A turn already recorded as a task is not an error — it is recorded.

FROM THE AGENT'S OWN SELF-CHECK, 2026-08-21: "sqlite3.IntegrityError: UNIQUE
constraint tasks.owner_id/task_id: turns not durable/replayable." It was right
that the error was real and wrong about the consequence, because the platform told
it so.

`enqueue_turn_task` keys the row on `trace_id` and its docstring claimed that id
"is already unique per turn". It is not: a RETRY DERIVES its trace id from the turn
it retries — `retry-eba4141b-fix`, `retry-eba4141b-fix-fix`, `<trace>-fix` — so a
retry re-entering this path collides with the row its own first attempt wrote.

THE HANDLER THEN REPORTED THE OPPOSITE OF THE TRUTH. It logged ERROR saying the
turn "is NOT recoverable if it fails" — about a turn whose row was sitting in the
table, claimable by the loop, which is precisely what recovery needs. An operator
reading that would go looking for a durability bug that does not exist, and an
agent reading it (this one did) reports its turns as non-replayable.

Measured twice, 2026-08-21 and 2026-08-22. Rare, and wrong every time.
"""

from __future__ import annotations

import inspect

from stackowl.pipeline.durable import turn_task as mod

_SRC = inspect.getsource(mod)


def test_a_task_id_collision_returns_quietly_instead_of_erroring() -> None:
    """The collision branch must exist, must be INFO, and must return before the
    ERROR path — otherwise the operator is told a recoverable turn is lost."""
    start = _SRC.index("except Exception as exc:")
    tail = _SRC[start:]
    collision = tail.index("UNIQUE constraint failed")
    error_log = tail.index("it is NOT recoverable if it fails")
    assert collision < error_log, (
        "the UNIQUE-collision check must come BEFORE the error log, or an "
        "already-recorded turn is still reported as unrecoverable"
    )
    branch = tail[collision:error_log]
    assert "log.tasks.info" in branch, "an already-recorded turn is not an ERROR"
    assert "return" in branch, "the collision branch must not fall through"


def test_a_DIFFERENT_integrity_error_still_surfaces_at_ERROR() -> None:
    """The carve-out is narrow on purpose. A UNIQUE violation on some other column
    is a real defect and must keep surfacing — catching IntegrityError broadly
    would hide it, which is the failure mode this whole session kept finding."""
    start = _SRC.index("except Exception as exc:")
    tail = _SRC[start:]
    branch = tail[:tail.index("it is NOT recoverable if it fails")]
    assert 'task_id" in str(exc)' in branch or "'task_id' in str(exc)" in branch, (
        "the collision check must be specific to the task_id constraint"
    )
    assert "log.tasks.error" in tail, "the genuine-failure path must remain"


def test_the_docstring_no_longer_claims_trace_ids_are_unique() -> None:
    """The false invariant is what made the collision look impossible, so nobody
    handled it. If it comes back, so does the bug."""
    doc = mod.enqueue_turn_task.__doc__ or ""
    assert "already unique per turn" not in doc, (
        "the docstring claims an invariant the retry path breaks"
    )
    assert "NOT ALWAYS UNIQUE" in doc or "not always unique" in doc.lower()

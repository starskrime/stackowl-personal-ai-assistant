"""One package used `idempotency_key` for two different things, and the weaker
one guarded the user-visible side.

MEASURED 2026-09-10. `delivery_ledger` uses `idempotency_key` for a JOB's
occurrence key — `occurrence_key = idempotency_key@next_run_at` — and that one IS
enforced: a second write with the same key is refused. `Notification` carried a
field of the same name that bought nothing. `_route` read it as
`notification_id = notification.idempotency_key or uuid4().hex` and handed it to
the INSERT as the row id; **nothing anywhere asked whether that id had been
delivered before.**

WHAT IT COST: 16 identical messages delivered twice within two minutes of each
other — 13 `goal_answer` on 2026-07-14 and three `turn_answer` (2026-08-18,
2026-08-30, and 2026-09-08 with a 4.0s gap). And an hour of a loop reasoning about
exactly-once delivery on the strength of the name, before reading the one line
that consumes it.

WHY THIS RENAMES RATHER THAN WIRES, stated so the next reader does not "finish the
job" by adding a check. Both senders pass `str(trace_id)`, and a retry mints a NEW
trace (`retry-x` then `retry-x-fix`). So a DUPLICATE carries a different key and
would pass any check, and a legitimate RECURRENCE carries a different key too and
would pass the same check — enforcing this key as it stands is a no-op in both
directions. Once-ness needs a key stable across attempts and distinct across
recurrences, and choosing it needs the cause of those 16, which is not isolated:
re-dispatch and the stream-miss fallback both re-deliver, and nothing today
distinguishes them.

So the provable defect is the NAME, and that is what this fixes.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from stackowl.notifications.router import Notification

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"


@pytest.mark.tripwire
def test_the_notification_field_no_longer_claims_idempotency() -> None:
    """THE REGRESSION. A field named for a guarantee it does not provide."""
    fields = set(Notification.model_fields)
    assert "notification_id" in fields
    assert "idempotency_key" not in fields, (
        "the name is back, and nothing enforces it any more than before"
    )


@pytest.mark.tripwire
def test_the_old_name_is_REJECTED_not_quietly_accepted() -> None:
    """`extra='forbid'` is what makes the rename real rather than cosmetic — a
    caller still passing the old word fails loudly instead of having its value
    dropped on the floor."""
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        Notification(
            message="m", urgency="normal", category="c", idempotency_key="x",
        )
    assert Notification(
        message="m", urgency="normal", category="c", notification_id="x",
    ).notification_id == "x"


@pytest.mark.tripwire
def test_the_JOB_key_keeps_the_name_because_it_earns_it() -> None:
    """THE CONTROL, and the whole point of the distinction.

    `Job.idempotency_key` is enforced — `delivery_ledger` refuses a second write
    on the same occurrence key. Renaming that one too would have been a blanket
    edit rather than a distinction, and would have destroyed the one place where
    the word is TRUE.
    """
    from stackowl.scheduler.job import Job

    assert "idempotency_key" in Job.model_fields


@pytest.mark.tripwire
def test_no_sender_passes_a_per_attempt_id_under_an_enforcing_name() -> None:
    """The shape, kept checkable for senders nobody has written yet.

    A `Notification(...)` built with a kwarg whose name promises once-ness is the
    defect this file exists for. The field is gone, so this asserts nothing
    reintroduces it via the constructor.
    """
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "Notification":
                continue
            for kw in node.keywords:
                if kw.arg and "idempot" in kw.arg:
                    offenders.append(f"{path.relative_to(_SRC.parent)}:{node.lineno}")
    assert not offenders, (
        "a Notification is being built with an idempotency-named kwarg again:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )

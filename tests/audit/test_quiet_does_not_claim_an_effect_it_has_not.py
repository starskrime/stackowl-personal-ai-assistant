"""`/quiet` writes a row nothing reads, and used to report success anyway.

MEASURED 2026-09-04. `QuietHoursCommand` INSERTs into `notification_overrides`
and returned `"quiet: global 22:00-08:00 until <expires>"` — a confident success.
But `notification_overrides` has exactly ONE reference in the whole tree outside
that command, and it is the health-cadence DECLARATION, not a reader. Nothing
consults the row. The telegram `QuietHoursChecker`, which is what actually gates
delivery, reads no database state at all — it is config-only.

So the operator could type `/quiet`, be told a window was applied, and keep
receiving notifications. That is the fake-success this codebase forbids in its own
words ("never a fake-success"), sitting on a user-facing command.

THIS TEST DOES NOT FIX THE ROOT CAUSE, and says so deliberately. The cause is a
write with no reader; the remedies are to WIRE a reader or to RETIRE the command,
and both change user-facing behaviour, so both are the operator's call (ESC-126).
What is fixed here is only the claim: the command must not report an effect it
does not have. Recording the distinction matters because shipping a symptom fix
and filing the cause is the exact move that was rejected on 2026-08-31 — the
difference is that here the CAUSE is escalated as a DECISION, not as a diagnosis,
and the diagnosis is complete.
"""

from __future__ import annotations

import inspect

from stackowl.commands import quiet_command


def test_the_success_message_does_not_claim_the_window_is_in_force() -> None:
    """It may confirm the record; it may not imply notifications will stop."""
    src = inspect.getsource(quiet_command).lower()
    # Case-insensitive on purpose: the message shouts NOT YET ENFORCED, and an
    # assertion that cares about case is testing the shout, not the meaning.
    assert "not yet enforced" in src, (
        "the success message must say the override is recorded but not enforced — "
        "nothing reads notification_overrides"
    )


def test_notification_overrides_still_has_no_reader() -> None:
    """The premise. When this fails, the row IS read and the message should change.

    Deliberately a failing-on-success test: it exists to notice the day someone
    wires the reader, so the honest message stops being honest.
    """
    import pathlib

    src_root = pathlib.Path(inspect.getfile(quiet_command)).resolve().parents[1]
    readers = [
        p
        for p in src_root.rglob("*.py")
        if p.name not in {"quiet_command.py", "store_cadence.py"}
        and "notification_overrides" in p.read_text(errors="ignore")
    ]
    assert not readers, (
        "something now references notification_overrides — if it READS the row, "
        f"quiet hours may be enforced and the message should be revisited: {readers}"
    )

"""CapabilityGapEscalation — the reader a bounds refusal never had.

Bakir, 2026-08-22: "Why are we doing anything for system health agent? It is not a
root cause of issue."

THE ROOT CAUSE, measured across three days: 85 bounds refusals (36 / 37 / 12) and
not one reached him. `mailbutler` was refused `shell` TWENTY-FOUR times — it needs
the tool, asks every run, is refused, reports honestly, and starts the next run
knowing nothing. No amount of self-healing could close that, because "should this
owl have this tool" is the operator's decision and he was never asked.

The refusal WAS recorded — into a ContextVar that `reset()` clears at the end of
the turn. Per-turn state doing a durable job, so nothing accumulated and nothing
could ever cross a threshold.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.capability_gap_escalation import (
    CapabilityGap,
    CapabilityGapEscalationHandler,
    find_recurring_gaps,
    render_gap_message,
)
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


def _job(**params: object) -> Job:
    return Job(
        job_id=f"capability_gap_escalation-{uuid.uuid4().hex[:6]}",
        handler_name="capability_gap_escalation",
        schedule="every 6h",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params=dict(params),
    )


async def _audit_table(db: DbPool) -> None:
    """No-op: `audit_log` comes from migration 0023, and `tmp_db` runs migrations.

    Kept as a named step because the first version of this file CREATED its own
    table — which silently did nothing (CREATE TABLE IF NOT EXISTS against the real
    one) and then failed every insert on `datatype mismatch`: the real `audit_id`
    is INTEGER AUTOINCREMENT, not the hex string the double used, and
    `chain_version` is TEXT. A fixture that stopped resembling the real thing, in
    the exact shape this codebase names as its second recurring defect.
    """


async def _write(db: DbPool, event: str, owl: str, tool: str, ts: float) -> None:
    """Insert exactly as `AuditLogger.append` does — audit_id autoincrements."""
    await db.execute(
        "INSERT INTO audit_log (event_type, actor, target, timestamp, details, "
        "integrity_hash, chain_version) VALUES (?,?,?,?,?,?,?)",
        (event, owl, tool, ts, "{}", "", "v1"),
    )


async def _deny(db: DbPool, owl: str, tool: str, n: int, *, age_days: float = 0) -> None:
    ts = time.time() - (age_days * 86_400)
    for _ in range(n):
        await _write(db, "capability.denied", owl, tool, ts)


async def _escalate(db: DbPool, owl: str, tool: str) -> None:
    await _write(db, "capability.escalated", owl, tool, time.time())


async def test_the_real_measured_shape_is_found(tmp_db: DbPool) -> None:
    """The live board on 2026-08-22, reproduced."""
    await _audit_table(tmp_db)
    await _deny(tmp_db, "mailbutler", "shell", 24)
    await _deny(tmp_db, "syshealth", "send_message", 3)
    await _deny(tmp_db, "sysdesign", "web_search", 2)  # below threshold

    gaps = await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7)

    assert [(g.owl, g.tool, g.occurrences) for g in gaps] == [
        ("mailbutler", "shell", 24),
        ("syshealth", "send_message", 3),
        ("sysdesign", "web_search", 2),
    ], gaps


async def test_a_ONE_OFF_refusal_never_bothers_the_operator(tmp_db: DbPool) -> None:
    """An owl probing once for a tool it lacks must not interrupt a human.

    The threshold now lives at the ESCALATION boundary rather than in the query, so
    `find_recurring_gaps` surfaces the single refusal and the HANDLER holds it back.
    That split matters: the threshold protects the operator's attention, and a
    self-heal he is never told about has no reason to wait. `sysdesign` runs DAILY,
    so gating heals at 3 would have left a within-ceiling gap open for three days.
    """
    await _audit_table(tmp_db)
    await _deny(tmp_db, "scout", "browser_navigate", 1)

    found = await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7)
    assert [(g.owl, g.tool, g.occurrences) for g in found] == [
        ("scout", "browser_navigate", 1)
    ]

    # ...and the handler does NOT escalate it.
    result = await CapabilityGapEscalationHandler(tmp_db).execute(
        _job(min_occurrences=3)
    )
    assert result.metadata["escalated"] == 0
    assert result.metadata["delivered"] is False


async def test_a_gap_is_raised_ONCE_not_once_per_occurrence(tmp_db: DbPool) -> None:
    """THE FAILURE THAT WOULD MAKE THIS WORSE THAN NOTHING.

    A gap fires on every run by definition — `mailbutler shell` 24 times. Alerting
    per occurrence would send 24 messages and train its reader to ignore the
    channel, which is the cries-wolf shape this codebase already pays for. Once the
    pair has been escalated it stays quiet.
    """
    await _audit_table(tmp_db)
    await _deny(tmp_db, "mailbutler", "shell", 24)
    await _escalate(tmp_db, "mailbutler", "shell")

    assert await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7) == []


async def test_refusals_outside_the_window_are_forgotten(tmp_db: DbPool) -> None:
    """A tool an owl stopped needing weeks ago must stop nagging."""
    await _audit_table(tmp_db)
    await _deny(tmp_db, "mailbutler", "claude_code", 9, age_days=30)

    assert await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7) == []


async def test_the_handler_stamps_what_it_raised(tmp_db: DbPool) -> None:
    """Without the stamp the same gap re-alerts every six hours forever."""
    await _audit_table(tmp_db)
    await _deny(tmp_db, "mailbutler", "shell", 24)

    stamped: list[tuple[str, str]] = []

    class _Audit:
        def append(self, *, event_type, actor, target, details):  # noqa: ANN001
            stamped.append((event_type, f"{actor}:{target}"))

    result = await CapabilityGapEscalationHandler(
        tmp_db, audit_logger=_Audit()
    ).execute(_job())

    assert result.success, result.error
    assert result.metadata["gaps"] == 1
    assert stamped == [("capability.escalated", "mailbutler:shell")]


async def test_no_deliverer_is_reported_not_swallowed(tmp_db: DbPool) -> None:
    """A gap nobody can be TOLD about is still a gap.

    `delivered` must stay False rather than defaulting to a comfortable True — the
    honest-delivery rule this platform already applies to every other send.
    """
    await _audit_table(tmp_db)
    await _deny(tmp_db, "syshealth", "send_message", 5)

    result = await CapabilityGapEscalationHandler(tmp_db).execute(_job())

    assert result.success
    assert result.metadata["delivered"] is False


async def test_a_clean_board_is_a_quiet_no_op(tmp_db: DbPool) -> None:
    await _audit_table(tmp_db)
    result = await CapabilityGapEscalationHandler(tmp_db).execute(_job())
    assert result.success
    assert result.metadata == {"gaps": 0, "healed": 0, "escalated": 0}


async def test_the_message_names_the_exact_command_that_closes_it() -> None:
    """An alert that does not say what to DO is a chore, not a fix."""
    text = render_gap_message([
        CapabilityGap(owl="mailbutler", tool="shell", occurrences=24),
        CapabilityGap(owl="syshealth", tool="send_message", occurrences=3),
    ])
    assert "mailbutler needs shell — refused 24 times" in text
    assert "owl_build edit mailbutler --allow-tool shell" in text
    assert "owl_build edit syshealth --allow-tool send_message" in text


async def test_one_message_covers_every_gap() -> None:
    """Six gaps must not become six notifications — same flood, different shape."""
    text = render_gap_message([
        CapabilityGap(owl=f"owl{i}", tool="shell", occurrences=5) for i in range(6)
    ])
    assert text.count("To grant one:") == 1


# --------------------------------------------------------------------------
# Self-healing within the ceiling.
#
# Bakir, 2026-08-22: "Why platform does not have capability to self heal himself
# instead you manually grant access to". He was right to push. `owl_build`'s
# widening path "always asks the user" for EVERY tool, so a blocked owl needed a
# human even for a capability the human had already approved at mint time.
#
# The creation ceiling IS that prior approval — for an operator-created owl it is
# SAFE_DEFAULT_CEILING, read-only-ish by construction with no shell/exec/write. So
# widening bounds up to it grants nothing new. Crossing it does, and still asks.
# --------------------------------------------------------------------------

from stackowl.authz.bounds import BoundsSpec  # noqa: E402
from stackowl.scheduler.handlers.capability_gap_escalation import (  # noqa: E402
    within_ceiling,
)


class _Manifest:
    """Shape-compatible stand-in — `within_ceiling` reads two attributes."""

    def __init__(self, bounds: BoundsSpec | None, ceiling: BoundsSpec | None) -> None:
        self.bounds = bounds
        self.creation_ceiling = ceiling


async def test_a_tool_inside_the_ceiling_is_self_healable() -> None:
    """THE LIVE CASE: sysdesign/web_search — in the ceiling, missing from bounds."""
    m = _Manifest(
        bounds=BoundsSpec(tools=frozenset({"memory", "tool_search"})),
        ceiling=BoundsSpec(tools=frozenset({"memory", "tool_search", "web_search"})),
    )
    assert within_ceiling(m, "web_search") is True


async def test_a_tool_OUTSIDE_the_ceiling_is_NEVER_self_healable() -> None:
    """THE SECURITY LINE: syshealth/send_message — in neither.

    Self-granting past the ceiling would be the platform deciding its own
    authority, which is the inversion this entire arc began with. It must escalate,
    never heal.
    """
    m = _Manifest(
        bounds=BoundsSpec(tools=frozenset({"read_logs"})),
        ceiling=BoundsSpec(tools=frozenset({"read_logs", "shell"})),
    )
    assert within_ceiling(m, "send_message") is False


async def test_an_ABSENT_ceiling_is_not_a_licence_to_self_grant() -> None:
    """`None` means "no constraint recorded", NOT "everything is permitted".

    Reading absence as permission is how an unbounded creator once turned the
    bounds clamp into a no-op — the exact footgun SAFE_DEFAULT_CEILING exists to
    close. Pinned so it cannot be reintroduced from the other end.
    """
    m = _Manifest(bounds=BoundsSpec(tools=frozenset({"memory"})), ceiling=None)
    assert within_ceiling(m, "shell") is False

    m2 = _Manifest(bounds=BoundsSpec(tools=frozenset({"memory"})),
                   ceiling=BoundsSpec(tools=None))
    assert within_ceiling(m2, "shell") is False


async def test_an_already_granted_tool_is_not_healed_again() -> None:
    """Nothing to widen — and re-granting would churn a write every sweep."""
    m = _Manifest(
        bounds=BoundsSpec(tools=frozenset({"memory", "web_search"})),
        ceiling=BoundsSpec(tools=frozenset({"memory", "web_search"})),
    )
    assert within_ceiling(m, "web_search") is False


async def test_an_unbounded_owl_is_not_healed() -> None:
    """bounds=None is already unbounded; there is no narrower set to widen."""
    m = _Manifest(bounds=None, ceiling=BoundsSpec(tools=frozenset({"shell"})))
    assert within_ceiling(m, "shell") is False

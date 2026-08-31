"""The self-heal loop has been chasing owls that no longer exist, 165 times.

MEASURED 2026-08-31 across four days of logs::

    [scheduler] capability_gap_escalation: owl vanished before self-heal
      08-28  37   08-29  47   08-30  48   08-31  33     = 165

and the owls it names are jobmarket, newsdesk and Brain — none of which is in the
registry, which holds archivist, english_tutor, hypothesis, librarian, mailbutler,
rca_gatherer, scout, secretary, syshealth and verifier.

THE DETECTOR'S INPUT IS MOSTLY GHOSTS. Of 90 ``capability.denied`` rows in
``audit_log``, **75 (83%)** name a deleted owl: jobmarket 61, headhunter 10,
newsdesk 3, Brain 1.

WHY IT REPEATS FOR EVER RATHER THAN ONCE. A pair is skipped on later runs only if
it carries a ``capability.escalated`` marker. For a deleted owl the run gets as far
as ``heal_within_ceiling``, ``snapshot_owl`` returns None, the warning is logged and
False is returned — and nothing is ever written. So the same dead pair is
re-detected on the next run, and the next, indefinitely.

A GAP FOR AN OWL THAT DOES NOT EXIST IS NOT A GAP. The fix is at the query, not at
the heal: work that will certainly be discarded should not be planned. This is
"measure the EFFECT" pointed one step earlier — do not do the work you already know
you will throw away.

AND IT IS NOT ONLY A CLEANUP. These particular rows age out of the 7-day window in
about three days. What does not age out is the SHAPE: every future owl deletion
recreates it, exactly as this one did.

THE EMPTY-TABLE GUARD IS THE SAME ONE THE ORPHAN SWEEP USES, for the same reason
and in Bakir's own words — "an empty table is a QUESTION, not an answer". With no
owls readable, every gap looks orphaned; filtering there would silently disable the
whole self-heal. So an empty or unreadable ``owls`` table means DO NOT FILTER,
which also keeps every pre-existing test in this suite honest: they seed audit rows
without ever creating an owl.
"""

from __future__ import annotations

import logging
import time

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.capability_gap_escalation import find_recurring_gaps

pytestmark = pytest.mark.asyncio


async def _deny(db: DbPool, owl: str, tool: str, n: int = 3) -> None:
    for _ in range(n):
        await db.execute(
            "INSERT INTO audit_log (event_type, actor, target, timestamp, details, "
            "integrity_hash, chain_version) VALUES (?,?,?,?,?,?,?)",
            ("capability.denied", owl, tool, time.time(), "{}", "", "v1"),
        )


async def _owl(db: DbPool, name: str) -> None:
    await db.execute(
        "INSERT INTO owls (name, display_name, role, lifecycle, origin, "
        "manifest_json, owner_id, created_at, updated_at) "
        "VALUES (?,?,'r','permanent','test','{}','principal-default','t','t')",
        (name, name),
    )


async def test_a_gap_for_a_DELETED_owl_is_not_returned(tmp_db: DbPool) -> None:
    """The live case: 61 of 90 denials name `jobmarket`, which is gone."""
    await _owl(tmp_db, "secretary")
    await _deny(tmp_db, "jobmarket", "browser_navigate", 14)
    await _deny(tmp_db, "secretary", "shell", 3)

    gaps = await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7)

    assert [(g.owl, g.tool) for g in gaps] == [("secretary", "shell")], (
        "a gap for an owl that does not exist is not a gap — it produced 165 "
        "'owl vanished before self-heal' warnings in four days"
    )


async def test_a_LIVE_owls_gap_is_untouched(tmp_db: DbPool) -> None:
    """The expensive direction. Losing a real gap disables the self-heal silently."""
    await _owl(tmp_db, "mailbutler")
    await _deny(tmp_db, "mailbutler", "shell", 24)

    gaps = await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7)

    assert [(g.owl, g.tool, g.occurrences) for g in gaps] == [
        ("mailbutler", "shell", 24)
    ]


async def test_an_EMPTY_owls_table_filters_NOTHING(tmp_db: DbPool) -> None:
    """"An empty table is a QUESTION, not an answer." With no owls readable every
    gap looks orphaned, and filtering there would silently disable the whole
    self-heal — the same guard, and the same reason, as the orphan sweep."""
    await _deny(tmp_db, "mailbutler", "shell", 24)

    gaps = await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7)

    assert [(g.owl, g.tool) for g in gaps] == [("mailbutler", "shell")]


async def test_an_UNREADABLE_owls_table_filters_NOTHING(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail toward doing the work. A broken read must not look like 'no owls'."""
    await _owl(tmp_db, "mailbutler")
    await _deny(tmp_db, "mailbutler", "shell", 5)

    real = tmp_db.fetch_all

    async def _selective(sql: str, *a: object, **k: object):  # noqa: ANN202
        if "FROM owls" in sql:
            raise RuntimeError("owls table unreadable")
        return await real(sql, *a, **k)

    monkeypatch.setattr(tmp_db, "fetch_all", _selective)
    gaps = await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7)

    assert [(g.owl, g.tool) for g in gaps] == [("mailbutler", "shell")]


async def test_the_drop_is_reported_at_INFO(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    """Production runs at INFO, and this line is the evidence that closes the
    acceptance check. A DEBUG line here could never be seen."""
    await _owl(tmp_db, "secretary")
    await _deny(tmp_db, "jobmarket", "browser_navigate", 14)
    await _deny(tmp_db, "newsdesk", "shell", 3)

    with caplog.at_level(logging.INFO):
        await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7)

    records = [r for r in caplog.records if "deleted owl" in r.getMessage()]
    assert records, "the drop is invisible — nothing could ever confirm it happened"
    fields = getattr(records[-1], "_fields", {})
    assert fields.get("dropped") == 2
    assert sorted(fields.get("owls") or []) == ["jobmarket", "newsdesk"]


async def test_an_already_escalated_pair_is_still_skipped(tmp_db: DbPool) -> None:
    """The pre-existing rule must survive: a gap alerts once, not every sweep."""
    await _owl(tmp_db, "mailbutler")
    await _deny(tmp_db, "mailbutler", "shell", 24)
    await tmp_db.execute(
        "INSERT INTO audit_log (event_type, actor, target, timestamp, details, "
        "integrity_hash, chain_version) VALUES (?,?,?,?,?,?,?)",
        ("capability.escalated", "mailbutler", "shell", time.time(), "{}", "", "v1"),
    )

    assert await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7) == []


async def test_a_clean_board_stays_quiet(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    """No ghosts, no line. A record emitted every run for zero dropped rows puts a
    zero into every future denominator for nothing."""
    await _owl(tmp_db, "mailbutler")
    await _deny(tmp_db, "mailbutler", "shell", 5)

    with caplog.at_level(logging.INFO):
        await find_recurring_gaps(tmp_db, min_occurrences=3, window_days=7)

    assert not [r for r in caplog.records if "deleted owl" in r.getMessage()]

"""His one interrupt: a failure signature that recurs AFTER its own fix.

THE DECISION. Bakir, 2026-09-02, choosing what may interrupt him: "Page on repeat
signatures." He rejected "page if unresolved" once the measurement was put in
front of him — 86 of 100 RCAs conclude verified=False, so that rule would have
taken him from ~12 pages a day to ~70 — and he did not pick "page when the RCA
gives up" either. The event worth an interrupt is the self-healing loop FAILING.

THE ARITHMETIC THAT MAKES IT SAFE, measured over 2,000 failed outcomes / 30 days:

    shell/stop                10 recurrences after its fix
    browser_navigate/stop      6
    shell/unachieved_effect    5
                              --
                              21 recurrences, THREE signatures, ~3.5 days

Paging per OCCURRENCE is 6 messages a day. Paging per SIGNATURE is three
messages, ever. Everything below exists to hold that second number, because the
first one is the mistake he already rejected.

AND THE ALERT NAMES THE OWNER, which is not decoration. All three signatures
above belong to skills no owl owned, so no owl was ever shown the lesson — of
course it did not hold. "The lesson recurred and nobody owns it" and "the lesson
recurred and its owner ignored it" are different problems and must not arrive
looking the same.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.learning.lesson_recurrence import (
    RECURRENCE_EVENT,
    Recurrence,
    already_paged,
    detect_recurrences,
    outcome_signature,
    record_paged,
    unreported,
)

pytestmark = pytest.mark.asyncio

_FIXED_AT = 1_000.0


class _Db:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.written: list[tuple] = []

    async def fetch_all(self, sql: str, params: tuple) -> list[dict]:
        return self.rows

    async def execute(self, sql: str, params: tuple) -> None:
        self.written.append(params)


def _cluster(capability: str, failure: str, *stamps_and_owls) -> SimpleNamespace:
    return SimpleNamespace(
        capability_class=capability,
        failure_class=failure,
        outcomes=tuple(
            SimpleNamespace(captured_at=t, owl_name=o) for t, o in stamps_and_owls
        ),
    )


def _names(capability: str, failure: str) -> set[str]:
    return {f"incident_{capability}", f"incident_{capability}_{failure}"}


async def test_a_signature_that_failed_AFTER_its_fix_is_reported() -> None:
    found = await detect_recurrences(
        [_cluster("shell", "stop", (_FIXED_AT + 5, "secretary"))],
        {"incident_shell": _FIXED_AT},
        owned_skills=[],
        skill_names_for=_names,
    )
    assert len(found) == 1
    assert found[0].signature == "outcome:shell:stop"
    assert found[0].recurrences == 1
    assert found[0].owls == ("secretary",)


async def test_failures_BEFORE_the_fix_are_not_a_recurrence() -> None:
    """The control, and it is the one that matters: without it every signature
    with a skill reports for ever, which is a rename of the 70-pages-a-day rule
    he rejected."""
    found = await detect_recurrences(
        [_cluster("shell", "stop", (_FIXED_AT - 5, "secretary"))],
        {"incident_shell": _FIXED_AT},
        owned_skills=[],
        skill_names_for=_names,
    )
    assert found == []


async def test_a_signature_with_NO_fix_is_an_ordinary_incident_not_this() -> None:
    found = await detect_recurrences(
        [_cluster("shell", "stop", (_FIXED_AT + 5, "secretary"))],
        {},
        owned_skills=[],
        skill_names_for=_names,
    )
    assert found == []


async def test_the_EARLIEST_fix_is_the_clock() -> None:
    """Using the latest would reset the clock every time the miner re-authored,
    and a lesson that never worked would look brand new for ever."""
    found = await detect_recurrences(
        [_cluster("shell", "stop", (_FIXED_AT + 5, "secretary"))],
        {"incident_shell": _FIXED_AT + 100, "incident_shell_stop": _FIXED_AT},
        owned_skills=[],
        skill_names_for=_names,
    )
    assert len(found) == 1
    assert found[0].fixed_at == _FIXED_AT


async def test_the_report_says_whether_ANYBODY_owns_the_lesson() -> None:
    """The field that would have surfaced the orphan defect on day one."""
    orphan = (await detect_recurrences(
        [_cluster("shell", "stop", (_FIXED_AT + 5, "secretary"))],
        {"incident_shell": _FIXED_AT}, owned_skills=[], skill_names_for=_names,
    ))[0]
    owned = (await detect_recurrences(
        [_cluster("shell", "stop", (_FIXED_AT + 5, "secretary"))],
        {"incident_shell": _FIXED_AT}, owned_skills=["incident_shell"],
        skill_names_for=_names,
    ))[0]

    assert orphan.has_owner is False
    assert "owned by NOBODY" in orphan.brief()
    assert owned.has_owner is True
    assert "is owned and still did not hold" in owned.brief()


# ------------------------------------------------------------------ the dedup


def _rec(fixed_at: float = _FIXED_AT) -> Recurrence:
    return Recurrence(
        signature=outcome_signature("shell", "stop"),
        capability_class="shell", failure_class="stop",
        skill_name="incident_shell", fixed_at=fixed_at, recurrences=3,
        owls=("secretary",), has_owner=False,
    )


async def test_the_same_signature_against_the_same_fix_pages_ONCE() -> None:
    """Three messages ever, not six a day. The sweep runs every 10 minutes."""
    db = _Db(rows=[{
        "actor": "outcome:shell:stop",
        "details": f'{{"fixed_at": {_FIXED_AT}}}',
    }])
    assert unreported([_rec()], await already_paged(db)) == []


async def test_a_NEW_fix_that_also_fails_pages_AGAIN() -> None:
    """Precisely the event he asked to hear about: a second attempt that also
    did not hold. Suppressing this would make the detector go quiet exactly when
    the loop is failing hardest."""
    db = _Db(rows=[{
        "actor": "outcome:shell:stop",
        "details": f'{{"fixed_at": {_FIXED_AT}}}',
    }])
    due = unreported([_rec(fixed_at=_FIXED_AT + 500)], await already_paged(db))
    assert len(due) == 1


async def test_an_unreadable_ledger_PAGES_rather_than_going_quiet() -> None:
    """Fails toward paging, deliberately. The cost of the wrong direction here is
    one duplicate message; the cost of the other is a self-healing loop that
    fails in silence, which is the failure mode this whole arc exists to stop."""
    db = _Db()

    async def _boom(*a: object, **k: object) -> list:
        raise RuntimeError("ledger unreadable")

    db.fetch_all = _boom  # type: ignore[method-assign]
    assert await already_paged(db) == {}
    assert len(unreported([_rec()], {})) == 1


async def test_the_marker_records_the_fix_it_was_reported_against() -> None:
    """Without fixed_at in the details the dedup degrades to 'this signature was
    reported once, ever' and a second failed fix would never be heard about."""
    db = _Db()
    await record_paged(db, _rec(), now=42.0)

    assert len(db.written) == 1
    params = db.written[0]
    assert params[0] == RECURRENCE_EVENT
    assert params[1] == "outcome:shell:stop"
    assert '"fixed_at": 1000.0' in params[4]


async def test_a_failing_ledger_write_does_not_raise() -> None:
    db = _Db()

    async def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("write failed")

    db.execute = _boom  # type: ignore[method-assign]
    await record_paged(db, _rec())  # must not raise


async def test_a_sibling_of_a_DIFFERENT_failure_class_is_not_the_fix() -> None:
    """Caught by a dry run against the live database before shipping.

    ``shell/unachieved_effect`` was attributed to ``incident_shell_stop`` — the
    same capability, a different failure — because every sibling was a candidate
    and the earliest one won the clock. The alert named the wrong lesson and dated
    the fix from an unrelated one, which would have sent him to look at a skill
    that had nothing to do with the failure.
    """

    def _only_this_class(capability: str, failure: str) -> set[str]:
        return {f"incident_{capability}", f"incident_{capability}_{failure}"}

    found = await detect_recurrences(
        [_cluster("shell", "unachieved_effect", (_FIXED_AT + 5, "secretary"))],
        {
            # The WRONG one is older, so an unfiltered candidate set picks it.
            "incident_shell_stop": _FIXED_AT - 500,
            "incident_shell_unachieved_effect": _FIXED_AT,
        },
        owned_skills=[],
        skill_names_for=_only_this_class,
    )
    assert len(found) == 1
    assert found[0].skill_name == "incident_shell_unachieved_effect"
    assert found[0].fixed_at == _FIXED_AT

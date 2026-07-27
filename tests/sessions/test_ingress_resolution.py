"""D01.7 test stage — the INGRESS SEQUENCE, against a real store.

The pieces were already covered by tests/sessions/: `resolve_for` persists the
outcome, `reset_notice` renders a note, `consume_reset_notice` clears it. What
had NO test was the COMPOSITION — the order ingress performs them in, which is
where Bakir's actual requirement lives:

    "A short one-line note, shown once. A boundary the user cannot see is one
     they experience as amnesia."  (brainstorm round 1, 2026-07-25)

Shown ONCE is a property of the sequence, not of any one function: render the
notice, then consume it, then the next turn must be silent. Getting that order
wrong shows the note forever or never, and both unit tests would still pass.

Until this stage that sequence lived in a nested closure inside
`startup/orchestrator.py`'s gateway phase, reachable only by constructing the
whole orchestrator — so nothing tested it. It is now
`sessions/ingress.resolve_turn_session`, and the orchestrator delegates.

REAL: the DbPool (fully migrated), SessionStore, the reset policy, the notice
renderer. FAKED: only the inbound message and the identity resolver. No AI
provider is involved — session resolution happens before any model call.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from stackowl.config.settings import SessionSettings
from stackowl.db.pool import DbPool
from stackowl.sessions.ingress import resolve_turn_session
from stackowl.sessions.store import SessionStore

USER = "72055773"


@dataclass
class _Msg:
    """The fields ingress reads off an IngressMessage."""

    channel: str = "telegram"
    is_direct: bool = True
    session_key: str = USER
    chat_id: int | None = 72055773


class _NoIdentityServices:
    """StepServices with no resolver wired — resolve_identity_key returns ""."""

    identity_resolver = None


def _settings(**kw: object) -> SessionSettings:
    return SessionSettings(**kw)  # type: ignore[arg-type]


def _now(hour: int = 12, day: int = 26) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, 0, tzinfo=datetime.UTC).astimezone()


async def _store(db: DbPool) -> SessionStore:
    return SessionStore(db, policy=None)


async def test_the_lane_survives_the_turn_and_the_incarnation_is_minted(
    tmp_db: DbPool,
) -> None:
    """The first turn of a brand-new conversation gets a lane AND a run."""
    store = SessionStore(tmp_db)

    key, run, notice = await resolve_turn_session(
        _Msg(), owl_name="secretary", session_store=store,
        session_settings=_settings(), services=_NoIdentityServices(), now=_now(),
    )

    assert key, "a lane must be minted"
    assert run, "an incarnation must be minted"
    assert "secretary" in key, "Bakir's Q1 — the owl is part of the lane"
    assert notice is None, "a first-ever conversation never 'expired'"


async def test_a_second_turn_keeps_both_the_lane_and_the_incarnation(
    tmp_db: DbPool,
) -> None:
    """Nothing rolled over, so the conversation continues — same run.

    This is the case that must NOT mint: an incarnation per turn would give
    every message its own 'conversation' and defeat the whole item.
    """
    store = SessionStore(tmp_db)
    kw = dict(owl_name="secretary", session_store=store,
              session_settings=_settings(), services=_NoIdentityServices())

    key1, run1, _ = await resolve_turn_session(_Msg(), now=_now(12), **kw)  # type: ignore[arg-type]
    key2, run2, notice = await resolve_turn_session(_Msg(), now=_now(13), **kw)  # type: ignore[arg-type]

    assert key1 == key2
    assert run1 == run2, "a quiet turn must not mint a new incarnation"
    assert notice is None


async def test_the_boundary_note_is_shown_exactly_once(tmp_db: DbPool) -> None:
    """Invariant I5, as a SEQUENCE — Bakir's requirement, not a unit property.

    Turn 1 sits on 2026-07-25. Turn 2 arrives after the next 4 AM boundary, so
    it crosses a daily rollover and must carry the note. Turn 3 follows
    immediately and must be SILENT: the note was consumed by turn 2.

    A note shown twice reads as a bug; a note never shown is amnesia.
    """
    store = SessionStore(tmp_db)
    kw = dict(owl_name="secretary", session_store=store,
              session_settings=_settings(), services=_NoIdentityServices())

    _k1, run1, n1 = await resolve_turn_session(_Msg(), now=_now(20, day=25), **kw)  # type: ignore[arg-type]
    assert n1 is None

    _k2, run2, n2 = await resolve_turn_session(_Msg(), now=_now(9, day=26), **kw)  # type: ignore[arg-type]
    assert run2 != run1, "crossing 4 AM must mint a new incarnation"
    assert n2 is not None, "the user must SEE the boundary — otherwise it is amnesia"

    _k3, run3, n3 = await resolve_turn_session(_Msg(), now=_now(10, day=26), **kw)  # type: ignore[arg-type]
    assert run3 == run2, "the same conversation continues"
    assert n3 is None, "the note is consumed — showing it twice reads as a bug"


async def test_notify_off_suppresses_the_note_but_not_the_rollover(
    tmp_db: DbPool,
) -> None:
    """The note is presentation; the boundary is behaviour. Turning the note off
    must not quietly stop conversations from rolling over."""
    store = SessionStore(tmp_db)
    kw = dict(owl_name="secretary", session_store=store,
              session_settings=_settings(notify_on_reset=False),
              services=_NoIdentityServices())

    _k1, run1, _ = await resolve_turn_session(_Msg(), now=_now(20, day=25), **kw)  # type: ignore[arg-type]
    _k2, run2, n2 = await resolve_turn_session(_Msg(), now=_now(9, day=26), **kw)  # type: ignore[arg-type]

    assert run2 != run1, "the rollover still happens"
    assert n2 is None, "the note is suppressed"


async def test_a_different_owl_is_a_different_conversation(tmp_db: DbPool) -> None:
    """Bakir's Q1, through the ingress path rather than the key builder alone."""
    store = SessionStore(tmp_db)
    kw = dict(session_store=store, session_settings=_settings(),
              services=_NoIdentityServices(), now=_now())

    key_a, run_a, _ = await resolve_turn_session(_Msg(), owl_name="secretary", **kw)  # type: ignore[arg-type]
    key_b, run_b, _ = await resolve_turn_session(_Msg(), owl_name="scout", **kw)  # type: ignore[arg-type]

    assert key_a != key_b
    assert run_a != run_b, "separate lanes cannot share an incarnation"


async def test_a_broken_store_costs_the_user_nothing(tmp_db: DbPool) -> None:
    """FAILS OPEN, LOUDLY. A session-store problem must never cost the user
    their reply — the turn degrades to the channel-native lane with no
    incarnation, which is exactly the pre-D01.7 behaviour.

    This is the one path that CANNOT be verified by reading the code, because
    the whole point is what happens when the code raises.
    """
    class _BrokenStore:
        async def resolve_for(self, *a: object, **k: object) -> object:
            raise RuntimeError("database is gone")

    key, run, notice = await resolve_turn_session(
        _Msg(), owl_name="secretary", session_store=_BrokenStore(),
        session_settings=_settings(), services=_NoIdentityServices(), now=_now(),
    )

    assert key == USER, "falls back to the channel-native lane"
    assert run == "", "no incarnation is invented"
    assert notice is None

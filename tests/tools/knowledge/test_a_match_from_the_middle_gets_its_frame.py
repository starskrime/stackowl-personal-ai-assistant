"""D11.3 — bookends: a `discover` hit carries how the session opened and closed.

THE ASK is a build instruction, not a question: "Adopt bookends — cheap and
clearly useful." Measured before building it, across all nine daily logs
(2026-08-28 to 2026-09-04), `session_search` was invoked **18 times: discover 16,
browse 2, scroll 0**. Control for that count: 1,765 `skill_view` mentions in the
same files, so the instrument reads.

The live log alone would have said ZERO — it rotates daily and was 46 minutes old
when this was measured, and `skill_view` was zero in it too. A zero whose control
is also zero measures the instrument, not the system.

WHY BOOKENDS BELONG ON `discover`. All three modes are scoped to ONE session
(`WHERE c.session_key = ?`); `browse` pages through a single session's messages
rather than listing sessions. So `discover` returns matches from the middle of a
session with nothing to say what the session was — the caller sees five hits and
no frame. First and last turns are that frame, and they cost two bounded queries
and no model call, which is the parity property the map records for this tool:
"discovery / scroll / browse from one tool with zero LLM cost."

REDACTION IS THE TRAP. `_render` runs `redact_secrets` over every turn's content.
Bookends that reached the output by any other path would be a hole in exactly the
"same rule, one case short" shape this map keeps turning up — so they render
through the same function, and a test below pins that.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.knowledge.session_search import SessionSearchTool


@pytest.fixture()
def services_with_db(tmp_db: DbPool) -> Iterator[DbPool]:
    """Bind ``tmp_db`` into ambient pipeline services so the tool resolves it."""
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        yield tmp_db
    finally:
        reset_services(token)


def _in_session(session_key: str) -> object:
    """Start a TraceContext as if the caller is currently in ``session_key``.

    The tool REFUSES a cross-session read with no current session to authorize
    against — a real guard, and the reason these tests must enter a session
    rather than pass `session_key` from nowhere.
    """
    return TraceContext.start(session_key=session_key)


async def _seed(db: DbPool, *, session_key: str, turns: list[tuple[str, str]]) -> None:
    conv_id = uuid.uuid4().hex
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await db.execute(
        "INSERT INTO conversations (id, session_key, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (conv_id, session_key, "Daria", base.isoformat(), len(turns)),
    )
    for i, (role, content) in enumerate(turns):
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, conv_id, role, content,
             (base + timedelta(seconds=i)).isoformat()),
        )


def _long_session() -> list[tuple[str, str]]:
    """Twenty turns whose opening and closing are distinguishable from the middle."""
    turns = [("user", "OPENING QUESTION about deployments"),
             ("assistant", "opening answer"),
             ("user", "second turn")]
    turns += [("assistant", f"middle filler {i} needle") for i in range(14)]
    turns += [("user", "CLOSING REMARK, thanks"),
              ("assistant", "closing answer"),
              ("user", "goodbye")]
    return turns


@pytest.mark.asyncio
async def test_a_discover_hit_carries_the_sessions_opening_and_close(
    services_with_db: DbPool,
) -> None:
    await _seed(services_with_db, session_key="s1", turns=_long_session())

    _in_session("s1")

    res = await SessionSearchTool().execute(
        mode="discover", query="needle", session_key="s1",
    )

    assert res.success
    out = res.output
    assert "middle filler" in out, "the matches themselves must still be there"
    assert "OPENING QUESTION" in out, "the caller cannot place a hit with no opening"
    assert "goodbye" in out, "nor tell whether the session ever resolved"


@pytest.mark.asyncio
async def test_the_frame_is_labelled_not_mixed_into_the_matches(
    services_with_db: DbPool,
) -> None:
    """Unlabelled extra turns would read as additional matches — a result that
    silently overstates what was found is worse than one that omits context."""
    await _seed(services_with_db, session_key="s2", turns=_long_session())

    _in_session("s2")

    out = (await SessionSearchTool().execute(
        mode="discover", query="needle", session_key="s2",
    )).output

    assert "session opens" in out
    assert "session closes" in out
    assert out.index("session opens") < out.index("session closes")


@pytest.mark.asyncio
async def test_a_short_session_is_not_printed_twice(services_with_db: DbPool) -> None:
    """Head and tail overlap when the session is shorter than both bookends.

    Emitting them anyway would duplicate the whole conversation and, on a
    two-turn session, trip the reader into thinking there were four turns.
    """
    await _seed(services_with_db, session_key="s3",
                turns=[("user", "only needle here"), ("assistant", "short reply")])

    _in_session("s3")

    out = (await SessionSearchTool().execute(
        mode="discover", query="needle", session_key="s3",
    )).output

    assert out.count("only needle here") == 1
    assert "session opens" not in out, (
        "a session the caller can already see whole needs no frame"
    )


@pytest.mark.asyncio
async def test_no_matches_means_no_frame(services_with_db: DbPool) -> None:
    """The control. Bookends decorate a result; they must not manufacture one."""
    await _seed(services_with_db, session_key="s4", turns=_long_session())

    _in_session("s4")

    out = (await SessionSearchTool().execute(
        mode="discover", query="zzz-absent-zzz", session_key="s4",
    )).output

    assert "session opens" not in out
    assert "OPENING QUESTION" not in out


@pytest.mark.asyncio
async def test_the_frame_is_redacted_like_every_other_turn(
    services_with_db: DbPool,
) -> None:
    """The one way this feature could be actively harmful.

    `_render` redacts every turn it prints. A bookend reaching the output by any
    other path would leak from the two turns MOST likely to carry a credential —
    the ones where a task is set up and signed off.
    """
    secret = "sk-ant-api03-" + "A" * 40
    turns = [("user", f"here is my key {secret}"), ("assistant", "noted")]
    turns += [("assistant", f"middle filler {i} needle") for i in range(14)]
    turns += [("user", f"and again {secret}"), ("assistant", "done"), ("user", "bye")]
    await _seed(services_with_db, session_key="s5", turns=turns)

    _in_session("s5")

    out = (await SessionSearchTool().execute(
        mode="discover", query="needle", session_key="s5",
    )).output

    assert "session opens" in out, "the frame must actually be present to be tested"
    assert secret not in out, "a bookend must be redacted like any other turn"

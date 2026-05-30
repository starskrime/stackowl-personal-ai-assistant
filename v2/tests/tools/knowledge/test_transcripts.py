"""Tests for the E4-S6 ``transcripts`` tool.

``transcripts`` returns the ORDERED full message log of a session (distinct
from ``session_search``'s ranked recall). It shares the S5 redaction +
visibility guard via :mod:`stackowl.tools.knowledge.session_access`. Tests run
against the real ``messages`` + ``conversations`` store over ``tmp_db``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.base import ToolManifest
from stackowl.tools.knowledge.transcripts import TranscriptsTool

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

    from stackowl.db.pool import DbPool


# --------------------------------------------------------------------- helpers


async def _seed_session(
    db: DbPool,
    *,
    session_id: str,
    owl_name: str,
    turns: list[tuple[str, str]],
) -> None:
    conv_id = uuid.uuid4().hex
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await db.execute(
        "INSERT INTO conversations (id, session_id, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (conv_id, session_id, owl_name, base.isoformat(), len(turns)),
    )
    for i, (role, content) in enumerate(turns):
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, conv_id, role, content, base.replace(second=i).isoformat()),
        )


@pytest.fixture()
def services_with_db(tmp_db: DbPool) -> Iterator[DbPool]:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        yield tmp_db
    finally:
        reset_services(token)


def _in_session(session_id: str) -> object:
    return TraceContext.start(session_id=session_id)


# ---------------------------------------------------------------------- manifest


def test_manifest_is_read_knowledge() -> None:
    m = TranscriptsTool().manifest
    assert isinstance(m, ToolManifest)
    assert m.action_severity == "read"
    assert m.toolset_group == "knowledge"
    assert TranscriptsTool().name == "transcripts"


# ------------------------------------------------------------------------ ordered


async def test_returns_ordered_transcript(services_with_db: DbPool) -> None:
    db = services_with_db
    await _seed_session(
        db,
        session_id="s1",
        owl_name="scout",
        turns=[
            ("user", "first"),
            ("assistant", "second"),
            ("user", "third"),
        ],
    )
    token = _in_session("s1")
    try:
        res = await TranscriptsTool().execute(session_id="s1")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success
    # Order preserved: first before second before third.
    assert res.output.index("first") < res.output.index("second") < res.output.index("third")


async def test_tool_turns_excluded_by_default(services_with_db: DbPool) -> None:
    db = services_with_db
    await _seed_session(
        db,
        session_id="s1",
        owl_name="scout",
        turns=[
            ("user", "ask"),
            ("tool", "TOOL_PAYLOAD_XYZ"),
            ("assistant", "answer"),
        ],
    )
    token = _in_session("s1")
    try:
        default = await TranscriptsTool().execute(session_id="s1")
        with_tools = await TranscriptsTool().execute(
            session_id="s1", include_tool_calls=True,
        )
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert default.success and "TOOL_PAYLOAD_XYZ" not in default.output
    assert with_tools.success and "TOOL_PAYLOAD_XYZ" in with_tools.output


# ----------------------------------------------------------------------- redaction


async def test_redaction_applied(services_with_db: DbPool) -> None:
    db = services_with_db
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    await _seed_session(
        db, session_id="s1", owl_name="scout",
        turns=[("user", f"token {secret}")],
    )
    token = _in_session("s1")
    try:
        res = await TranscriptsTool().execute(session_id="s1")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success
    assert secret not in res.output
    assert "REDACTED" in res.output


async def test_redaction_applied_to_included_tool_payload(
    services_with_db: DbPool,
) -> None:
    db = services_with_db
    secret = "sk-SECRETSECRETSECRETSECRET1234567890"
    await _seed_session(
        db, session_id="s1", owl_name="scout",
        turns=[("tool", f"result with {secret}")],
    )
    token = _in_session("s1")
    try:
        res = await TranscriptsTool().execute(
            session_id="s1", include_tool_calls=True,
        )
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success
    assert secret not in res.output


# ------------------------------------------------------------------- visibility


async def test_unknown_session_is_structured(services_with_db: DbPool) -> None:
    token = _in_session("s1")
    try:
        # Caller's own current session s1 has no messages → empty, structured.
        res = await TranscriptsTool().execute(session_id="s1")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success  # known-empty is a successful empty transcript
    assert "no" in res.output.lower() or res.output.strip() == "" or "empty" in res.output.lower()


async def test_cross_session_blocked_for_different_owner(
    services_with_db: DbPool,
) -> None:
    db = services_with_db
    await _seed_session(
        db, session_id="s1", owl_name="scout", turns=[("user", "scout")],
    )
    await _seed_session(
        db, session_id="s2", owl_name="oracle",
        turns=[("user", "oracle private transcript")],
    )
    token = _in_session("s1")
    try:
        res = await TranscriptsTool().execute(session_id="s2")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert not res.success
    assert res.error and "refus" in res.error.lower()
    assert "oracle private transcript" not in (res.output or "")


# ----------------------------------------------------------------------- healing


async def test_store_unavailable_is_structured() -> None:
    token_services = set_services(StepServices(db_pool=None))
    trace = _in_session("s1")
    try:
        res = await TranscriptsTool().execute(session_id="s1")
    finally:
        TraceContext.reset(trace)  # type: ignore[arg-type]
        reset_services(token_services)
    assert not res.success
    assert res.error and "unavailable" in res.error.lower()

"""Tests for the E4-S5 ``session_search`` tool.

These exercise the three recall shapes (browse / discover / scroll), the
shared value-level redaction, the cross-session visibility guard, and
self-healing. They run against the REAL conversation store — ``messages`` +
``conversations`` (migration 0002) — seeded over the ``tmp_db`` fixture, since
that store is plain SQLite (not the heavy tri-store), so a faithful seed is
cheaper and more faithful than a fake.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.base import ToolManifest
from stackowl.tools.knowledge.session_search import SessionSearchTool

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
) -> list[str]:
    """Seed one conversation/session with ordered ``(role, content)`` turns.

    Returns the ordered list of inserted message ids.
    """
    conv_id = uuid.uuid4().hex
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await db.execute(
        "INSERT INTO conversations (id, session_id, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (conv_id, session_id, owl_name, base.isoformat(), len(turns)),
    )
    ids: list[str] = []
    for i, (role, content) in enumerate(turns):
        mid = uuid.uuid4().hex
        # Monotonic created_at so ORDER BY created_at is deterministic.
        ts = (base + timedelta(seconds=i)).isoformat()
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, conv_id, role, content, ts),
        )
        ids.append(mid)
    return ids


@pytest.fixture()
def services_with_db(tmp_db: DbPool) -> Iterator[DbPool]:
    """Bind ``tmp_db`` into ambient pipeline services for the tool to resolve."""
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        yield tmp_db
    finally:
        reset_services(token)


def _in_session(session_id: str) -> object:
    """Start a TraceContext as if the caller is currently in ``session_id``."""
    return TraceContext.start(session_id=session_id)


# ---------------------------------------------------------------------- manifest


def test_manifest_is_read_knowledge() -> None:
    m = SessionSearchTool().manifest
    assert isinstance(m, ToolManifest)
    assert m.action_severity == "read"
    assert m.toolset_group == "knowledge"


def test_description_states_lane_and_antilane() -> None:
    d = SessionSearchTool().description.lower()
    assert "session_search" in SessionSearchTool().name
    # LANE: verbatim past conversation. ANTI-LANE: not facts, not procedures.
    assert "said" in d or "verbatim" in d
    assert "memory" in d and "skill_view" in d


# ------------------------------------------------------------------------ browse


async def test_browse_paginates_own_session(services_with_db: DbPool) -> None:
    db = services_with_db
    await _seed_session(
        db,
        session_id="s1",
        owl_name="scout",
        turns=[("user", f"line {i}") for i in range(10)],
    )
    token = _in_session("s1")
    try:
        page1 = await SessionSearchTool().execute(mode="browse", limit=4, offset=0)
        page2 = await SessionSearchTool().execute(mode="browse", limit=4, offset=4)
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]

    assert page1.success and page2.success
    assert "line 0" in page1.output and "line 3" in page1.output
    assert "line 4" not in page1.output
    assert "line 4" in page2.output


# ----------------------------------------------------------------------- discover


async def test_discover_returns_matching_turns(services_with_db: DbPool) -> None:
    db = services_with_db
    await _seed_session(
        db,
        session_id="s1",
        owl_name="scout",
        turns=[
            ("user", "how do I configure nginx"),
            ("assistant", "edit the server block"),
            ("user", "unrelated chatter about lunch"),
        ],
    )
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(mode="discover", query="nginx")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]

    assert res.success
    assert "nginx" in res.output
    assert "lunch" not in res.output


async def test_discover_empty_query_is_structured(services_with_db: DbPool) -> None:
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(mode="discover", query="   ")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert not res.success
    assert res.error and "query" in res.error.lower()


# ------------------------------------------------------------------------- scroll


async def test_scroll_returns_neighbors_with_capped_radius(
    services_with_db: DbPool,
) -> None:
    db = services_with_db
    from stackowl.tools.knowledge.session_search import _MAX_RADIUS

    # Seed well beyond 2*cap so a hostile radius is demonstrably truncated.
    total = _MAX_RADIUS * 2 + 40
    anchor_idx = total // 2
    ids = await _seed_session(
        db,
        session_id="s1",
        owl_name="scout",
        turns=[("user", f"turn {i}") for i in range(total)],
    )
    anchor = ids[anchor_idx]
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(
            mode="scroll", anchor_id=anchor, radius=2,
        )
        # An absurd radius must be capped, not honoured verbatim.
        capped = await SessionSearchTool().execute(
            mode="scroll", anchor_id=anchor, radius=10_000,
        )
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]

    assert res.success
    # radius=2 → anchor ± 2 inclusive.
    assert f"turn {anchor_idx}" in res.output
    assert f"turn {anchor_idx - 2}" in res.output and f"turn {anchor_idx + 2}" in res.output
    assert f"turn {anchor_idx - 3}" not in res.output
    assert f"turn {anchor_idx + 3}" not in res.output
    # Capped radius window cannot exceed 2*_MAX_RADIUS + 1 turns: the far ends
    # (which are > _MAX_RADIUS away from the anchor) must be absent.
    assert capped.success
    assert "turn 0" not in capped.output
    assert f"turn {total - 1}" not in capped.output
    assert f"turn {anchor_idx - _MAX_RADIUS}" in capped.output
    assert f"turn {anchor_idx - _MAX_RADIUS - 1}" not in capped.output


async def test_scroll_unknown_anchor_is_structured(services_with_db: DbPool) -> None:
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(mode="scroll", anchor_id="nope")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert not res.success
    assert res.error


# ----------------------------------------------------------------------- redaction


async def test_redaction_masks_secret_in_returned_turn(
    services_with_db: DbPool,
) -> None:
    db = services_with_db
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX1234567890"
    await _seed_session(
        db,
        session_id="s1",
        owl_name="scout",
        turns=[("user", f"my key is {secret} please use nginx")],
    )
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(mode="discover", query="nginx")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success
    assert secret not in res.output
    assert "REDACTED" in res.output


# ------------------------------------------------------------------- visibility


async def test_cross_session_blocked_for_different_owner(
    services_with_db: DbPool,
) -> None:
    db = services_with_db
    await _seed_session(
        db, session_id="s1", owl_name="scout",
        turns=[("user", "scout secrets")],
    )
    await _seed_session(
        db, session_id="s2", owl_name="oracle",
        turns=[("user", "oracle private notes")],
    )
    # Caller is in s1 (owner scout); tries to read s2 (owner oracle).
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(
            mode="browse", session_id="s2", limit=10,
        )
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert not res.success
    assert res.error and "refus" in res.error.lower()
    assert "oracle private notes" not in (res.output or "")


async def test_cross_session_allowed_for_same_owner(
    services_with_db: DbPool,
) -> None:
    db = services_with_db
    await _seed_session(
        db, session_id="s1", owl_name="scout", turns=[("user", "current")],
    )
    await _seed_session(
        db, session_id="s2", owl_name="scout", turns=[("user", "earlier scout work")],
    )
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(
            mode="browse", session_id="s2", limit=10,
        )
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success
    assert "earlier scout work" in res.output


# ----------------------------------------------------------------------- healing


async def test_store_unavailable_is_structured() -> None:
    token_services = set_services(StepServices(db_pool=None))
    trace = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(mode="browse")
    finally:
        TraceContext.reset(trace)  # type: ignore[arg-type]
        reset_services(token_services)
    assert not res.success
    assert res.error and "unavailable" in res.error.lower()


async def test_unknown_mode_is_structured(services_with_db: DbPool) -> None:
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(mode="teleport")
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert not res.success
    assert res.error and "mode" in res.error.lower()


# ----------------------------------------------- security-branch regression (QA)


async def test_no_current_session_cross_session_refused(services_with_db) -> None:  # noqa: ANN001
    # Fail-closed: with NO current session, a cross-session read must be refused.
    db = services_with_db
    await _seed_session(db, session_id="s2", owl_name="oracle", turns=[("user", "oracle private")])
    res = await SessionSearchTool().execute(mode="browse", session_id="s2", limit=10)
    assert res.success is False
    assert "oracle private" not in res.output


async def test_ambiguous_target_owner_cross_session_refused(services_with_db) -> None:  # noqa: ANN001
    # A DIFFERENT session whose owner is ambiguous (spans >1 owl_name) cannot be
    # authorized → fail-closed refusal. (Reading your OWN session is always allowed.)
    db = services_with_db
    await _seed_session(db, session_id="mine", owl_name="scout", turns=[("user", "x")])
    await _seed_session(db, session_id="shared", owl_name="scout", turns=[("user", "a-secret")])
    await _seed_session(db, session_id="shared", owl_name="oracle", turns=[("user", "b-secret")])
    token = _in_session("mine")
    try:
        res = await SessionSearchTool().execute(mode="browse", session_id="shared", limit=10)
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success is False
    assert "a-secret" not in res.output and "b-secret" not in res.output


async def test_discover_like_wildcard_escaped(services_with_db) -> None:  # noqa: ANN001
    # A discover query of '%' must match only the literal-'%' turn, not everything.
    db = services_with_db
    await _seed_session(db, session_id="s1", owl_name="scout",
                        turns=[("user", "alpha"), ("assistant", "100%done")])
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(mode="discover", query="%", limit=10)
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success
    assert "100%done" in res.output
    assert "alpha" not in res.output


async def test_redacts_aws_slack_pem_basicauth(services_with_db) -> None:  # noqa: ANN001
    db = services_with_db
    secret_turn = (
        "creds: AKIAIOSFODNN7EXAMPLE and xoxb-123456789012-abcdefghijkl and "
        "https://user:p4ssw0rdSecret@host/x"
    )
    await _seed_session(db, session_id="s1", owl_name="scout", turns=[("user", secret_turn)])
    token = _in_session("s1")
    try:
        res = await SessionSearchTool().execute(mode="browse", limit=10)
    finally:
        TraceContext.reset(token)  # type: ignore[arg-type]
    assert res.success
    assert "AKIAIOSFODNN7EXAMPLE" not in res.output
    assert "xoxb-123456789012-abcdefghijkl" not in res.output
    assert "p4ssw0rdSecret" not in res.output

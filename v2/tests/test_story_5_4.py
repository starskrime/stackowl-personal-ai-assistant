"""Story 5.4 — Parliament commands & multi-owl gateway scanner."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.commands.parliament_command import ParliamentCommand
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.gateway.scanner import (
    GatewayScanner,
    IngressMessage,
    RouteDecision,
)
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.parliament.models import ParliamentSession
from stackowl.parliament.session_store import SessionStore
from stackowl.pipeline.state import PipelineState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False)


@pytest.fixture()
async def parliament_db(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "parliament.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _msg(text: str) -> IngressMessage:
    return IngressMessage(
        text=text,
        session_id="sess-1",
        channel="cli",
        trace_id="trace-1",
    )


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="command",
    )


# ---------------------------------------------------------------------------
# RouteDecision shape
# ---------------------------------------------------------------------------


class TestRouteDecision:
    def test_parliament_owls_field_optional(self) -> None:
        decision = RouteDecision(route="owl", target="secretary")
        assert decision.parliament_owls is None

    def test_parliament_owls_field_round_trip(self) -> None:
        decision = RouteDecision(
            route="parliament",
            target="parliament",
            parliament_owls=["a", "b"],
            stripped_text="topic",
        )
        assert decision.parliament_owls == ["a", "b"]
        assert decision.route == "parliament"


# ---------------------------------------------------------------------------
# GatewayScanner — multi-owl detection
# ---------------------------------------------------------------------------


class TestScannerMultiOwl:
    def test_two_owl_mentions_route_to_parliament(self) -> None:
        scanner = GatewayScanner()
        decision = scanner.scan(_msg("@OwlA @OwlB should we ship the release"))
        assert decision.route == "parliament"
        assert decision.target == "parliament"

    def test_parliament_owls_list_populated(self) -> None:
        scanner = GatewayScanner()
        decision = scanner.scan(_msg("@OwlA @OwlB topic"))
        assert decision.parliament_owls == ["OwlA", "OwlB"]

    def test_stripped_text_has_no_at_mentions(self) -> None:
        scanner = GatewayScanner()
        decision = scanner.scan(_msg("@OwlA @OwlB please debate this"))
        assert decision.stripped_text is not None
        assert "@OwlA" not in decision.stripped_text
        assert "@OwlB" not in decision.stripped_text
        assert "please debate this" in decision.stripped_text

    def test_single_owl_is_not_parliament(self) -> None:
        scanner = GatewayScanner()
        decision = scanner.scan(_msg("@OwlA hello there"))
        assert decision.route == "owl"
        assert decision.target == "OwlA"

    def test_panic_beats_multi_owl(self) -> None:
        scanner = GatewayScanner()
        decision = scanner.scan(_msg("!panic @a @b"))
        assert decision.route == "panic"

    def test_three_mentions_all_captured(self) -> None:
        scanner = GatewayScanner()
        decision = scanner.scan(_msg("@a @b @c quick question"))
        assert decision.route == "parliament"
        assert decision.parliament_owls == ["a", "b", "c"]

    def test_unicode_owl_names_in_multi_mention(self) -> None:
        scanner = GatewayScanner()
        decision = scanner.scan(_msg("@野口 @müller どう思う？"))
        assert decision.route == "parliament"
        assert decision.parliament_owls is not None
        assert "野口" in decision.parliament_owls
        assert "müller" in decision.parliament_owls


# ---------------------------------------------------------------------------
# ParliamentCommand — not-configured paths
# ---------------------------------------------------------------------------


class TestParliamentCommandNotConfigured:
    async def test_start_without_orchestrator(self) -> None:
        cmd = ParliamentCommand()
        result = await cmd.handle("should we ship", _state())
        assert "not configured" in result.lower()

    async def test_log_without_store(self) -> None:
        cmd = ParliamentCommand(orchestrator=AsyncMock())
        result = await cmd.handle("log", _state())
        assert "not configured" in result.lower()

    async def test_push_without_orchestrator(self) -> None:
        cmd = ParliamentCommand()
        result = await cmd.handle("push hello", _state())
        assert "not configured" in result.lower()

    async def test_empty_args_shows_usage(self) -> None:
        cmd = ParliamentCommand()
        result = await cmd.handle("", _state())
        assert "Usage" in result


# ---------------------------------------------------------------------------
# ParliamentCommand — happy paths with mocks
# ---------------------------------------------------------------------------


class _StubOrchestrator:
    def __init__(self, session: ParliamentSession) -> None:
        self._session = session
        self.run_calls: list[tuple[str, list[str], str | None]] = []
        self.injected: list[str] = []
        self._inject_result = True

    async def run(
        self,
        topic: str,
        owl_names: list[str],
        session_id: str | None = None,
    ) -> ParliamentSession:
        self.run_calls.append((topic, owl_names, session_id))
        return self._session

    async def inject_interjection(self, message: str) -> bool:
        self.injected.append(message)
        return self._inject_result


def _make_session(
    *,
    synthesis: str | None = None,
    status: Any = "completed",
    owl_names: list[str] | None = None,
) -> ParliamentSession:
    s = ParliamentSession(topic="t", owl_names=owl_names or ["a", "b"])
    if status == "completed":
        s = s.complete(synthesis=synthesis)
    return s


class TestParliamentCommandStart:
    async def test_start_returns_synthesis_with_rollcall(
        self,
        parliament_db: DbPool,
    ) -> None:
        session = _make_session(
            synthesis="[confidence: 85%]\nParliament: a · b\n\nbody\n◆",
            owl_names=["a", "b"],
        )
        orch = _StubOrchestrator(session)
        registry = OwlRegistry()
        registry.register(
            OwlAgentManifest(
                name="a", role="x", system_prompt="p", model_tier="standard"
            )
        )
        registry.register(
            OwlAgentManifest(
                name="b", role="y", system_prompt="p", model_tier="standard"
            )
        )
        cmd = ParliamentCommand(
            orchestrator=orch,  # type: ignore[arg-type]
            session_store=SessionStore(parliament_db),
            owl_registry=registry,
        )
        result = await cmd.handle("should we ship", _state())
        assert "Parliament:" in result
        assert "body" in result
        # Orchestrator was called with the topic
        assert orch.run_calls[0][0] == "should we ship"

    async def test_start_with_no_synthesis(self) -> None:
        session = _make_session(synthesis=None)
        orch = _StubOrchestrator(session)
        cmd = ParliamentCommand(orchestrator=orch)  # type: ignore[arg-type]
        result = await cmd.handle("topic here", _state())
        assert "no synthesis produced" in result.lower()

    async def test_start_falls_back_to_critic_when_registry_empty(self) -> None:
        session = _make_session(synthesis="ok")
        orch = _StubOrchestrator(session)
        registry = OwlRegistry()  # empty
        cmd = ParliamentCommand(
            orchestrator=orch,  # type: ignore[arg-type]
            owl_registry=registry,
        )
        await cmd.handle("topic", _state())
        assert orch.run_calls
        owl_names = orch.run_calls[0][1]
        assert "secretary" in owl_names
        assert "critic" in owl_names


# ---------------------------------------------------------------------------
# ParliamentCommand — log / push / expand / unsuppress
# ---------------------------------------------------------------------------


class TestParliamentCommandLog:
    async def test_log_empty_history_shows_no_sessions(
        self,
        parliament_db: DbPool,
    ) -> None:
        store = SessionStore(parliament_db)
        cmd = ParliamentCommand(
            orchestrator=AsyncMock(),
            session_store=store,
        )
        result = await cmd.handle("log", _state())
        assert "No Parliament sessions" in result

    async def test_log_lists_recent(self, parliament_db: DbPool) -> None:
        store = SessionStore(parliament_db)
        await store.create(ParliamentSession(topic="t1", owl_names=["a", "b"]))
        cmd = ParliamentCommand(session_store=store)
        result = await cmd.handle("log", _state())
        assert "t1" in result
        assert "session" in result  # table header


class TestParliamentCommandPush:
    async def test_push_no_active_session(self) -> None:
        session = _make_session()
        orch = _StubOrchestrator(session)
        orch._inject_result = False
        cmd = ParliamentCommand(orchestrator=orch)  # type: ignore[arg-type]
        result = await cmd.handle("push hello", _state())
        assert "No active Parliament" in result

    async def test_push_queues_interjection(self) -> None:
        session = _make_session()
        orch = _StubOrchestrator(session)
        cmd = ParliamentCommand(orchestrator=orch)  # type: ignore[arg-type]
        result = await cmd.handle("push reconsider X", _state())
        assert "queued" in result.lower()
        assert orch.injected == ["reconsider X"]


class TestParliamentCommandExpand:
    async def test_expand_no_history(self, parliament_db: DbPool) -> None:
        store = SessionStore(parliament_db)
        session = _make_session(synthesis="ok")
        orch = _StubOrchestrator(session)
        cmd = ParliamentCommand(
            orchestrator=orch,  # type: ignore[arg-type]
            session_store=store,
        )
        result = await cmd.handle("expand the claim", _state())
        assert "No completed Parliament sessions" in result


class TestParliamentCommandUnsuppress:
    def test_unsuppress_emits_event(self) -> None:
        bus = EventBus()
        received: list[Any] = []
        bus.subscribe(
            "parliament_suggestions_unsuppressed",
            lambda payload: received.append(payload),
        )
        cmd = ParliamentCommand(event_bus=bus)
        # _unsuppress is sync; call via handle (which dispatches)
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            cmd.handle("unsuppress", _state())
        ) if False else None
        # easier — call sync helper directly
        result = cmd._unsuppress()
        assert "re-enabled" in result.lower()
        assert received == [None]

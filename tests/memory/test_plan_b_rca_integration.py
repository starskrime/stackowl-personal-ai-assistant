"""RC-A end-to-end integration — the long-term-memory chain, proven and GUARDED.

This is the gateway-layer acceptance test for the user's RC-A goal: long-term
recall must actually see conversation-derived facts. It proves the full chain AND
guards its wiring (it FAILS if ``DreamWorkerJobHandler.execute()`` stops calling
``_mine()``):

    real conversation turn (driven through the gateway scanner + AsyncioBackend,
      AI provider mocked)
      → ``consolidate`` stores it as a staged ``conversation`` turn
      → nightly ``DreamWorkerJobHandler.execute()`` calls ``_mine()``
      → ``ConversationMiner`` extracts a ``conversation_fact`` (count 0)
      → a SECOND dream pass re-derives the SAME fact → reinforced to count 1
      → ``FactPromoter`` (conversation_fact_reinforcement_required=1) promotes it
      → ``bridge.recall()`` returns it.

What is mocked vs real
----------------------
* MOCKED (AI provider side only):
  - The turn pipeline's provider is a ``_RecordingProvider`` fake (same pattern as
    ``tests/pipeline/test_plan_a_gateway_integration.py``).
  - The extraction AI boundary is a ``_StubExtractor`` (same style as
    ``tests/memory/test_plan_b_conversation_miner.py``) that returns a FIXED
    high-confidence ``conversation_fact``. The fixed content is what lets the two
    dream passes reinforce the SAME staged row (exact-content match in the miner).
* REAL: the ``SqliteMemoryBridge``, the ``ConversationMiner`` wiring, the
  ``FactPromoter``, the ``MemoryPruner``, the ``ContradictionDetector``, and the
  ``DreamWorkerJobHandler`` itself (full ``execute()`` runs end to end). Only the
  heavy Kuzu graph sync handler is replaced with a no-op stub — it is graph-sync
  plumbing irrelevant to the mine→reinforce→promote→recall chain under test, and
  standing the real Kuzu adapter up in a unit test pulls heavy native deps.

Drove-a-real-turn decision: we DRIVE A REAL BACKEND TURN (preferred for fidelity)
— scanner.scan → secretary route → ``AsyncioBackend.run`` — so the ``consolidate``
step writes the staged ``conversation`` row organically (not seeded). The mocked
provider keeps the turn deterministic and offline.

Full-execute() decision: we run the FULL ``handler.execute(job)`` (TWICE), NOT a
fall back to ``handler._mine()`` directly — so the test guards the
``await self._mine()`` line inside ``execute()``. ``TestModeGuard`` is neutralised
via monkeypatch because ``execute()`` asserts not-test-mode at the top.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.contradiction_detector import ContradictionDetector
from stackowl.memory.conversation_miner import ConversationMiner
from stackowl.memory.dream_worker import DreamWorkerJobHandler
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.models import StagedFact
from stackowl.memory.pruner import MemoryPruner
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job, JobResult
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


# The fixed fact the extractor "derives" from every conversation turn. Fixed
# content is load-bearing: it is what makes the two dream passes reinforce the
# SAME staged row rather than stage a second row.
_FIXED_FACT_CONTENT = "User lives in Baku"
_RECALL_QUERY = "Baku"


# ---- Mocked AI provider (resolved THROUGH the provider_registry) -------------


class _RecordingProvider(ModelProvider):
    """Fake provider for the turn pipeline. Returns a canned reply, zero tools."""

    def __init__(self) -> None:
        self._name = "fake"
        self.tool_loop_calls = 0
        self.stream_calls = 0
        self.complete_calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.complete_calls += 1
        return CompletionResult(
            content="noted",
            input_tokens=10,
            output_tokens=3,
            model="fake-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        self.stream_calls += 1
        yield "noted"

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
        **_kwargs: object,  # absorb persistence_check (now passed on every turn)
    ) -> tuple[str, list]:
        self.tool_loop_calls += 1
        return "noted", []


# ---- Stub extraction AI boundary (same style as the miner unit tests) --------


class _StubExtractor:
    """Returns ONE fixed high-confidence conversation_fact per session.

    Fixed content across calls → the second dream pass reinforces the SAME staged
    row instead of staging a duplicate.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def extract(self, messages: list[object], session_id: str) -> list[StagedFact]:
        self.calls.append((session_id, len(messages)))
        return [
            StagedFact(
                content=_FIXED_FACT_CONTENT,
                source_type="conversation_fact",
                source_ref=session_id,
                confidence=0.95,
            )
        ]


# ---- Minimal no-op Kuzu sync handler (graph-sync plumbing, out of scope) -----


class _NoopKuzuHandler:
    """Satisfies DreamWorker's kuzu_sync phase without the heavy native adapter."""

    @property
    def handler_name(self) -> str:
        return "kuzu_sync"

    async def execute(self, job: Job) -> JobResult:
        return JobResult(
            job_id=job.job_id, success=True, output="noop", error=None, duration_ms=0.0
        )


# ---- Helpers -----------------------------------------------------------------


def _build_services(
    bridge: SqliteMemoryBridge,
    provider: _RecordingProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    return StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )


def _state_from_decision(
    decision, *, trace_id: str, session_id: str, channel: str, raw_text: str
) -> PipelineState:
    """Build PipelineState exactly as startup/orchestrator.py does for an owl route."""
    input_text = decision.stripped_text if decision.stripped_text is not None else raw_text
    return PipelineState(
        trace_id=trace_id,
        session_id=session_id,
        input_text=input_text,
        channel=channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )


def _make_job() -> Job:
    return Job(
        job_id="rca-dream-1",
        handler_name="dream_worker",
        schedule="manual",
        idempotency_key="rca:dream:test",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )


async def _staged_conversation_fact_rows(db: DbPool, session_id: str):
    return await db.fetch_all(
        "SELECT content, reinforcement_count, status FROM staged_facts "
        "WHERE source_type='conversation_fact' AND source_ref=?",
        (session_id,),
    )


# ---- Test --------------------------------------------------------------------


async def test_rca_conversation_turn_to_recall_end_to_end(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --- (a) real bridge over the temp-sqlite pool (migrations already run) ----
    bridge = SqliteMemoryBridge(db=tmp_db)

    # --- (b) drive ONE REAL conversation turn through the gateway + backend ----
    provider = _RecordingProvider()
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()
    services = _build_services(bridge, provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    session_id = "sess-rca-e2e"

    msg = IngressMessage(
        text="I live in Baku",
        session_id=session_id,
        channel="cli",
        trace_id="trace-rca-1",
    )
    decision = scanner.scan(msg)
    assert decision.route == "owl", f"expected owl route, got {decision.route!r}"
    assert decision.target == "secretary", f"expected secretary, got {decision.target!r}"

    state = _state_from_decision(
        decision,
        trace_id=msg.trace_id,
        session_id=session_id,
        channel=msg.channel,
        raw_text=msg.text,
    )
    await backend.run(state)

    # The consolidate step must have written a REAL staged 'conversation' row.
    conv_rows = await tmp_db.fetch_all(
        "SELECT content FROM staged_facts WHERE source_type='conversation' AND source_ref=?",
        (session_id,),
    )
    assert conv_rows, (
        "consolidate did not persist a staged 'conversation' turn — the real "
        "backend turn did not reach the memory bridge."
    )
    assert any("I live in Baku" in r["content"] for r in conv_rows)

    # --- (c) REAL promoter + REAL miner (stub extractor) + REAL dream worker ---
    promoter = FactPromoter(
        tmp_db,
        confidence_threshold=0.8,
        reinforcement_required=3,
        conversation_fact_reinforcement_required=1,
    )
    extractor = _StubExtractor()
    miner = ConversationMiner(
        db=tmp_db, extractor=extractor, bridge=bridge, message_limit=40
    )
    pruner = MemoryPruner(db=tmp_db)
    detector = ContradictionDetector()
    handler = DreamWorkerJobHandler(
        bridge=bridge,
        promoter=promoter,
        pruner=pruner,
        kuzu_handler=_NoopKuzuHandler(),  # type: ignore[arg-type]
        detector=detector,
        miner=miner,
    )

    # --- (d) neutralise TestModeGuard, then run execute() TWICE ----------------
    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda op: None)
    )

    # PASS 1 — mine stages the conversation_fact at reinforcement_count 0; the
    # promotion phase must NOT promote it yet (needs reinforcement_count >= 1).
    result1 = await handler.execute(_make_job())
    assert result1.success, f"dream pass 1 failed: {result1.error}"
    assert extractor.calls, "miner.extract was never called — _mine() did not run in execute()"

    rows_after_1 = await _staged_conversation_fact_rows(tmp_db, session_id)
    assert len(rows_after_1) == 1, f"expected exactly one staged fact, got {rows_after_1}"
    assert rows_after_1[0]["content"] == _FIXED_FACT_CONTENT
    assert rows_after_1[0]["reinforcement_count"] == 0, (
        "after pass 1 the conversation_fact must be staged at reinforcement_count 0"
    )
    not_yet = await tmp_db.fetch_all(
        "SELECT content FROM committed_facts WHERE content=?", (_FIXED_FACT_CONTENT,)
    )
    assert not not_yet, "fact must NOT be committed after a single pass (count still 0)"

    # PASS 2 — re-mine reinforces the SAME row to count 1, then the promotion
    # phase commits it (conversation_fact_reinforcement_required=1).
    result2 = await handler.execute(_make_job())
    assert result2.success, f"dream pass 2 failed: {result2.error}"

    # The staged row was reinforced to 1 then consumed by promotion. After
    # promotion it leaves the staged queue, so assert it is no longer 'staged'
    # and is now present in committed_facts.
    remaining_staged = await tmp_db.fetch_all(
        "SELECT 1 FROM staged_facts WHERE source_type='conversation_fact' "
        "AND source_ref=? AND content=? AND status='staged'",
        (session_id, _FIXED_FACT_CONTENT),
    )
    assert not remaining_staged, (
        "after pass 2 the reinforced fact must have been promoted out of the staged queue"
    )

    # --- (e) FINAL RC-A acceptance: committed AND recallable -------------------
    committed = await tmp_db.fetch_all(
        "SELECT fact_id, content FROM committed_facts WHERE content=?",
        (_FIXED_FACT_CONTENT,),
    )
    assert committed, "RC-A FAIL: conversation-derived fact never reached committed_facts"
    committed_id = committed[0]["fact_id"]

    recalled = await bridge.recall(_RECALL_QUERY, limit=5)
    assert recalled, "RC-A FAIL: recall() returned nothing for the promoted fact"
    assert any(r.fact_id == committed_id for r in recalled), (
        f"RC-A FAIL: recall() did not return the promoted conversation fact; "
        f"got {[r.fact_id for r in recalled]}"
    )
    assert any(_FIXED_FACT_CONTENT in r.content for r in recalled), (
        f"RC-A FAIL: recall() result did not contain the fact content; "
        f"got {[r.content for r in recalled]}"
    )

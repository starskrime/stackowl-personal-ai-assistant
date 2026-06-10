"""J2 JOURNEY — "Research a topic across the web and remember it" (PRD §3, J2).

The business requirement, verbatim from ``_bmad-output/planning-artifacts/
prd-tool-expansion.md`` §3:

  > **J2 — Research a topic across the web and remember it.** *"Research the
  > current state of ARM64 ML inference and remember the key findings."* →
  > ``web_search`` (SearXNG-first) → ``web_fetch`` top hits → synthesize →
  > ``memory`` (write via MemoryBridge). Later recall surfaces findings via
  > hybrid rank **without re-searching**.

THE headline business outcome: the user can RESEARCH ONCE and RECALL LATER
**without the system re-searching the web**. That is the wiring guard — it proves
the findings were actually stored in memory AND that recall retrieves them from
memory, not by hitting the web again.

This is NOT a per-tool smoke. It proves the USER's END-TO-END OUTCOME across two
tools/epics (E6 web_search → E4 memory) over two turns, driving real inbound
Telegram messages through the GENUINE path (TelegramChannelAdapter →
GatewayScanner → AsyncioBackend pipeline → execute._dispatch → ToolRegistry) and
the REAL classify→assemble recall path.

REAL (everything except the egress edge): the migrated ``DbPool`` (tmp_db), the
whole pipeline (classify→assemble→execute), the ``ToolRegistry`` +
``WebSearchTool``/``MemoryTool``, the REAL ``SqliteMemoryBridge`` over tmp_db
(``stage`` → ``FactPromoter.force_promote`` → ``committed_facts`` +
``committed_facts_fts``), the REAL ``WebSearchRegistry`` cascade, and the
Telegram adapter's inbound + outbound transport. On turn 2 the REAL classify step
calls ``bridge.retrieve`` → ``recall`` → FTS5 BM25 over the committed finding and
folds it into ``system_text`` via the REAL assemble step.

FAKED — ONLY the egress edge: (1) the AI provider (a scripted, owl-aware
secretary that drives the real tool loop), and (2) the external web-search
backends (``WebSearchProvider`` fakes returning fixture hits about "ARM64 ML
inference", with a CALL COUNTER so we can prove they are NOT hit on recall). The
Telegram bot HTTP transport is faked in-process (``_FakeBot``) — it is transport,
not a decision-maker.

Business-outcome assertions (NOT tool return-shapes):
  1. After turn 1, the ARM64 finding — DERIVED from the REAL web_search hit text
     (a slice of what the faked backend actually returned, not a constant) — is
     ACTUALLY STORED in the REAL ``memory_bridge`` (it surfaces from
     ``recall``/``list_staged`` over tmp_db).
  2. On turn 2 the recalled finding reaches the user's Telegram chat, AND the
     faked web-search backends' CALL COUNTER did NOT increase on turn 2 — recall,
     not re-search. THIS is the headline guard.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry
from stackowl.web_search.base import WebHit, WebSearchProvider, WebSearchResult, success_result
from stackowl.web_search.registry import WebSearchRegistry

USER_ID = 424242
SESSION_HINT = "the topic the user asked to research"

# The fixture hits the faked web backend returns for the ARM64 query. The agent's
# stored finding is DERIVED from this text (a slice of the real returned hit), so
# the test can only go green if web_search actually returned it AND memory stored
# it — it can never false-pass on a canned string the provider emits regardless.
_ARM64_HIT_TITLE = "ARM64 ML inference: the 2026 landscape"
_ARM64_HIT_DESC = (
    "ARM64 ML inference now runs production transformer workloads via "
    "llama.cpp NEON kernels and ONNX Runtime; Jetson and Graviton lead throughput."
)
_ARM64_HIT_URL = "https://searxng.test/arm64-ml-inference"

# A distinctive multi-word phrase that exists ONLY because the faked backend
# returned it AND it was stored — used to prove the recall surfaced the finding.
_FINDING_PHRASE = "llama.cpp NEON kernels and ONNX Runtime"


# --- FAKED (egress edge #1): external web-search backend, with a call counter ---


class _CountingWebProvider(WebSearchProvider):
    """A network-free web backend returning fixture ARM64 hits.

    ``calls`` is the load-bearing counter: the headline guard asserts it does NOT
    increase on the recall turn — proving the system recalled from memory rather
    than re-searching the web. This stands in for the external egress edge (the
    real SearXNG/Brave/DDG HTTP calls), analogous to the AI provider mock.
    """

    def __init__(self, name: str = "searxng") -> None:
        self._name = name
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    async def search(self, query: str, limit: int) -> WebSearchResult:
        self.calls += 1
        return success_result(
            [
                WebHit(
                    title=_ARM64_HIT_TITLE,
                    url=_ARM64_HIT_URL,
                    description=_ARM64_HIT_DESC,
                    position=1,
                ),
                WebHit(
                    title="ARM64 NPU acceleration survey",
                    url="https://searxng.test/arm64-npu",
                    description="Edge NPUs and quantized int8 inference on ARM64.",
                    position=2,
                ),
            ]
        )


# --- FAKED (egress edge #2): the secretary owl's scripted provider --------------


class _ScriptedSecretary:
    """The AI mock: stands in for the secretary owl's LLM.

    Turn 1 drives the REAL tool loop via the REAL ``tool_dispatcher``:
      1. web_search — hits the faked backend (fixture ARM64 hits).
      2. memory(add) — STORE a finding DERIVED from the real web_search output
         (a verbatim slice of the returned hit text), through the REAL bridge.
      3. return a final reply confirming the research+remember.

    Turn 2 is RECALL: it MUST NOT call web_search (a real owl recalls from
    memory). It reads what the REAL classify/assemble surfaced (the recalled
    finding folded into ``system_text``) and answers from THAT — proving the
    finding reached the model via memory, not the web.
    """

    protocol = "anthropic"
    # Honor the ModelProvider contract (base.py: `name` property) so the real
    # `triage` step (router.py reads `provider.name`) runs genuinely instead of
    # silently erroring + being swallowed by the backend's per-step self-heal.
    name = "scripted-secretary"

    def __init__(self) -> None:
        self.turn = 0
        self.web_out: str = ""
        self.mem_out: str = ""
        self.stored_finding: str = ""
        self.turn2_system_text: str | None = None
        self.turn2_called_web = False

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.turn += 1
        if self.turn == 1:
            return await self._research_and_remember(tool_dispatcher)
        return await self._recall(system_text, history)

    async def _research_and_remember(self, tool_dispatcher) -> tuple[str, list]:  # noqa: ANN001
        calls: list[dict] = []

        # 1. Real web_search → faked backend returns the ARM64 fixture hits.
        self.web_out = await tool_dispatcher(
            "web_search", {"query": "current state of ARM64 ML inference", "limit": 5}
        )
        calls.append({"name": "web_search", "args": {"query": "ARM64 ML inference"}, "result": self.web_out})

        # Derive the finding FROM the real web_search output (slice the actual
        # returned hit description), NOT from a constant — so the stored finding
        # can only contain the fixture's words if web_search truly returned them.
        # This keeps the search->memory coupling in the data flow and survives
        # ``python -O`` (which strips bare asserts). (The J1 lesson.)
        payload = json.loads(self.web_out)
        assert payload["success"] is True, f"web_search did not succeed: {self.web_out!r}"
        hits = payload["data"]["web"]
        assert hits, f"web_search returned no hits: {self.web_out!r}"
        top_desc = hits[0]["description"]
        assert _FINDING_PHRASE in top_desc, (
            "web_search hit text missing the expected ARM64 finding phrase — the "
            f"stored finding cannot be derived from real search output. Got: {top_desc!r}"
        )
        _idx = top_desc.find(_FINDING_PHRASE)
        derived = top_desc[_idx : _idx + len(_FINDING_PHRASE)]
        self.stored_finding = (
            f"Key finding on ARM64 ML inference: production transformer workloads "
            f"run via {derived}."
        )

        # 2. Real memory(add) → REAL SqliteMemoryBridge stage + force_promote.
        self.mem_out = await tool_dispatcher(
            "memory", {"action": "add", "content": self.stored_finding}
        )
        calls.append({"name": "memory", "args": {"action": "add"}, "result": self.mem_out})

        final = (
            "I researched the current state of ARM64 ML inference and remembered the "
            "key findings for later."
        )
        return (final, calls)

    async def _recall(self, system_text, history) -> tuple[str, list]:  # noqa: ANN001
        # A real owl recalls from memory: it does NOT call web_search here. The
        # REAL classify step has already folded the committed finding into
        # ``system_text`` (and any session turns into ``history``). Answer from
        # what the memory system surfaced — never re-search.
        self.turn2_system_text = system_text
        surfaced = system_text or ""
        if history:
            surfaced += "\n" + "\n".join(getattr(m, "content", "") for m in history)
        # The owl quotes back what recall surfaced. If the finding is NOT in the
        # surfaced context, this answer will not contain it and assertion 2 fails
        # honestly (no re-search rescue).
        if _FINDING_PHRASE in surfaced:
            idx = surfaced.find(_FINDING_PHRASE)
            quoted = surfaced[idx : idx + len(_FINDING_PHRASE)]
            answer = (
                "From what I remember about ARM64 ML inference: production "
                f"transformer workloads run via {quoted}."
            )
        else:
            answer = "I don't have anything remembered about ARM64 ML inference yet."
        return (answer, [])

    async def complete(self, *a, **k) -> CompletionResult:  # noqa: ANN002,ANN003
        # The real triage step CALLS complete() (router reads .input_tokens), so
        # honor the ModelProvider result contract — return a real CompletionResult
        # so triage runs genuinely instead of crashing + being swallowed.
        return CompletionResult(
            content="", input_tokens=1, output_tokens=1, model="scripted",
            provider_name="scripted-secretary", duration_ms=0.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedSecretary) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedSecretary:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedSecretary:
        return self._p

    def get_with_cascade(self, tier: str) -> _ScriptedSecretary:
        # The persistence-judge resolves a fast-tier provider via this method
        # (fail-OPEN if absent). Resolve to the scripted secretary so the harness
        # stays self-contained and never reaches for a real provider.
        return self._p


# --- FAKED transport: the Telegram bot HTTP layer (in-process capture) ----------


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedSecretary
    web_provider: _CountingWebProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


async def _turn(env: _Env, text: str) -> str:
    """Drive one real inbound Telegram message end-to-end, return delivered text."""
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return "\n".join(
        m["text"] for m in env.bot.messages[before:] if m["chat_id"] == USER_ID
    )


def _build(tmp_db: DbPool) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    provider = _ScriptedSecretary()
    web_provider = _CountingWebProvider("searxng")
    # REAL registry, SearXNG-first; the single provider IS the egress edge.
    web_registry = WebSearchRegistry([web_provider])
    # REAL bridge over the migrated tmp_db — the load-bearing real service.
    bridge = SqliteMemoryBridge(db=tmp_db)

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),  # REAL web_search + memory
        consent_gate=ConsequentialActionGate(),  # memory is write (ungated); no gate fires
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        memory_bridge=bridge,  # REAL SqliteMemoryBridge
        db_pool=tmp_db,  # REAL migrated DbPool — FactPromoter writes here
        web_search_registry=web_registry,  # REAL registry over the faked backend
    )
    return _Env(
        adapter=adapter,
        bot=bot,
        scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services),
        stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
        web_provider=web_provider,
    )


async def test_j2_research_then_recall_without_re_searching(tmp_db: DbPool) -> None:
    env = _build(tmp_db)
    bridge = SqliteMemoryBridge(db=tmp_db)  # an independent reader over the same db

    # ===================================================================
    # TURN 1 — research + remember (real inbound Telegram).
    # ===================================================================
    delivered1 = await _turn(
        env,
        "Research the current state of ARM64 ML inference and remember the key findings.",
    )
    assert env.web_provider.calls >= 1, (
        "Turn 1 never hit the web backend — web_search did not run through the real "
        f"registry. provider.web_out={env.provider.web_out!r}"
    )
    calls_after_turn1 = env.web_provider.calls
    assert "researched" in delivered1.lower() or delivered1, (
        f"Turn 1 produced no reply to the user. Delivered: {delivered1!r}"
    )

    # ===================================================================
    # BUSINESS OUTCOME 1 — the finding is ACTUALLY STORED in the REAL bridge.
    # We derive the asserted text from the REAL web_search hit (the provider sliced
    # it out of the actual returned description), then prove it landed in memory by
    # querying the REAL bridge — both via recall (the production read path) AND via
    # the raw committed/staged rows. No constant the provider emits regardless.
    # ===================================================================
    finding = env.provider.stored_finding
    assert _FINDING_PHRASE in finding, (
        "BUSINESS OUTCOME 1 PRECONDITION FAIL: the stored finding was not derived "
        f"from the real web_search hit. Finding: {finding!r}"
    )
    # The memory tool reported a successful store.
    assert "Remembered" in env.provider.mem_out, (
        f"BUSINESS OUTCOME 1 FAIL: memory(add) did not confirm a store. Got: {env.provider.mem_out!r}"
    )
    # Production read path: hybrid recall surfaces it from the REAL bridge.
    recalled = await bridge.recall("ARM64 ML inference", limit=10)
    assert any(_FINDING_PHRASE in r.content for r in recalled), (
        "BUSINESS OUTCOME 1 FAIL: the ARM64 finding was NOT stored/recallable in the "
        f"REAL memory_bridge. recall() returned: {[r.content for r in recalled]!r}"
    )
    # And it is genuinely persisted as an agent_self fact (committed, not just staged).
    committed = await bridge.list_staged(status="committed")
    assert any(
        _FINDING_PHRASE in f.content and f.source_type == "agent_self" for f in committed
    ), (
        "BUSINESS OUTCOME 1 FAIL: the finding is not a committed agent_self fact. "
        f"committed facts: {[(f.source_type, f.content) for f in committed]!r}"
    )

    # ===================================================================
    # TURN 2 — recall later (real inbound Telegram). The owl recalls from memory;
    # the scripted provider does NOT call web_search on this turn.
    # ===================================================================
    delivered2 = await _turn(env, "What did you find about ARM64 ML inference?")

    # ===================================================================
    # BUSINESS OUTCOME 2 (THE HEADLINE GUARD) — the recalled finding reaches the
    # user's chat, AND the web backend was NOT hit again on turn 2 (recall, not
    # re-search). The finding text the user sees is the SAME slice derived from the
    # turn-1 web hit; it can only appear if the REAL classify→assemble path
    # surfaced the committed fact into system_text and the owl quoted it back.
    # ===================================================================
    # The adapter MarkdownV2-escapes punctuation (e.g. 'llama\.cpp') on the way
    # out, so compare against the escape-stripped delivered text (the J1 pattern).
    delivered2_unescaped = delivered2.replace("\\", "")
    assert _FINDING_PHRASE in delivered2_unescaped, (
        "BUSINESS OUTCOME 2 FAIL: the recalled ARM64 finding did NOT reach the user's "
        f"chat on the recall turn. Delivered: {delivered2!r} | turn-2 system_text "
        f"was: {env.provider.turn2_system_text!r}"
    )
    # The load-bearing guard: zero new web-backend calls on the recall turn.
    assert env.web_provider.calls == calls_after_turn1, (
        "BUSINESS OUTCOME 2 FAIL (HEADLINE): the system RE-SEARCHED the web on recall "
        f"— web backend calls went from {calls_after_turn1} to {env.web_provider.calls}. "
        "Recall must surface the stored finding WITHOUT hitting the web again."
    )
    # Belt-and-suspenders: the REAL classify step actually folded the committed
    # finding into the model's system_text on turn 2 (the recall wiring under test).
    assert env.provider.turn2_system_text and _FINDING_PHRASE in env.provider.turn2_system_text, (
        "BUSINESS OUTCOME 2 FAIL: the recall path did not surface the finding into "
        f"turn-2 system_text. Got: {env.provider.turn2_system_text!r}"
    )

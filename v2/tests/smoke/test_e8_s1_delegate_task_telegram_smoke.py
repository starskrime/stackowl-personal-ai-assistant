"""E8-S1 SMOKE — delegate_task driven AS THE USER, Telegram input → child round-trip.

A real inbound Telegram update traverses the GENUINE path (TelegramChannelAdapter →
GatewayScanner → AsyncioBackend pipeline → execute._dispatch → ToolRegistry →
DelegateTaskTool → a REAL ``A2ADelegator.delegate`` → a CHILD ``AsyncioBackend.run``
sub-pipeline for the specialist → the child's reply travels back over the real
``A2AQueue`` → the parent surfaces it to the user over Telegram).

The PARENT turn (owl=secretary) emits a ``delegate_task`` tool call
``{"goal": ..., "to_owl": "scout"}``. That call runs through the real pipeline and
the real delegator, which spawns a sibling pipeline for ``scout``. The CHILD turn
(owl=scout, delegation_depth=1) emits a plain final answer with no tool call; that
text is joined into the child state's responses and returned by ``delegate()``.
The parent then returns that text (with the tool's provenance footer) as its final
answer, which the adapter delivers to the user.

REAL: the DbPool (tmp_db, fully migrated), the whole pipeline (both parent and
child runs), the ToolRegistry + DelegateTaskTool, the ``A2ADelegator`` round-trip,
the shared ``ConcurrencyGovernor``, the ``A2AQueue``, and the OwlRegistry
(secretary + scout). FAKED (per the E7-S1 template): the provider (scripted,
owl-aware — branches parent vs child on TraceContext owl_name) and the Telegram
bot transport (captures outbound text in-process). ``A2ADelegator.delegate`` itself
is NOT stubbed — the delegation round-trip is genuinely executed.
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
from stackowl.infra.trace import TraceContext
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 858585

# What the child specialist (scout) answers when its sub-pipeline runs.
CHILD_ANSWER = "Scout's finding: X is foo."


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


class _ScriptedProvider:
    """Owl-aware scripted provider.

    Branches on the owl running the current (sub-)pipeline, read from
    ``TraceContext`` (the backend sets owl_name/delegation_depth before steps run):

    * PARENT (owl=secretary): emit a ``delegate_task`` tool call to scout, then
      return the delegate tool's child-text-plus-provenance as the final answer
      so the delegated result reaches the user.
    * CHILD (owl=scout, depth=1): no tool call — return a plain final answer that
      the delegate round-trip carries back to the parent.

    Records the delegation depth observed on the child turn so the smoke can prove
    the S0 depth increment fired on a REAL delegation (child ran at depth 1).
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.parent_results: list[str] = []
        self.child_depths: list[int] = []
        self.child_ran: bool = False

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001
        ctx = TraceContext.get()
        owl = ctx.get("owl_name")
        depth = int(ctx.get("delegation_depth") or 0)

        if owl == "scout":
            # CHILD specialist sub-pipeline: produce a plain final answer (no tool
            # call). This text is what delegate() returns to the parent.
            self.child_ran = True
            self.child_depths.append(depth)
            return (CHILD_ANSWER, [])

        # PARENT (secretary): delegate to scout, then incorporate the child's text.
        name, args = "delegate_task", {"goal": "research X", "to_owl": "scout"}
        out = await tool_dispatcher(name, args)
        self.parent_results.append(out)
        # The delegate tool's structured record carries the child's text + footer.
        record = json.loads(out).get("record", {})
        final = str(record.get("result", out))
        return (final, [{"name": name, "args": args, "result": out}])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedProvider) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedProvider


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _registry_with_scout() -> OwlRegistry:
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="scout",
            role="research-scout",
            system_prompt="You research things.",
            model_tier="standard",
        )
    )
    return reg


async def _turn(env: _Env, text: str) -> None:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.session_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)


async def test_smoke_delegate_task_real_child_roundtrip_through_telegram(tmp_db: DbPool) -> None:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    provider = _ScriptedProvider()
    owl_registry = _registry_with_scout()
    governor = ConcurrencyGovernor()
    a2a_queue = A2AQueue()

    # The services the parent pipeline runs under. The CHILD pipeline runs the
    # SAME AsyncioBackend with the SAME services (A2ADelegator(services=services)),
    # so the child's provider calls go through this scripted provider too.
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
        a2a_queue=a2a_queue,
        delegation_governor=governor,
        db_pool=tmp_db,  # REAL, fully-migrated DbPool
    )
    # REAL A2ADelegator wired off the same queue/governor/services single source.
    services.a2a_delegator = A2ADelegator(a2a_queue=a2a_queue, services=services)

    env = _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
    )

    # The user asks the secretary to research something → secretary delegates to scout.
    await _turn(env, "research X for me")

    # (1) The parent's delegate_task tool call ran through the REAL pipeline:
    # the scripted provider recorded the tool's structured output (it was reached
    # via execute._dispatch → ToolRegistry → DelegateTaskTool, not a direct call).
    assert provider.parent_results, "parent never reached delegate_task via the pipeline"
    record = json.loads(provider.parent_results[0])["record"]
    assert record["status"] == "ok", record
    assert record["to_owl"] == "scout", record

    # (2) The REAL delegation round-trip ran a CHILD pipeline that produced the
    # answer (not a stub) — and the child's text came back through delegate().
    assert provider.child_ran is True, "child specialist sub-pipeline never ran"
    assert CHILD_ANSWER in str(record["result"]), record

    # (3) The result carries the provenance footer naming the delegate + sub-run.
    assert "scout" in str(record["result"]), record
    assert "delegated" in str(record["result"]).lower(), record
    assert "sub-run" in str(record["result"]).lower(), record

    # (4) delegation_depth: the CHILD ran at depth 1 — proving the S0 depth
    # increment fired on a REAL delegation (parent depth 0 → child depth 1).
    assert provider.child_depths == [1], provider.child_depths

    # (5) The delegated result reached the USER over Telegram. The adapter applies
    # MarkdownV2 escaping on the way out (e.g. '.' → '\.'), so assert on the
    # unescaped core of the child's answer rather than its trailing punctuation.
    assert bot.messages, "no outbound Telegram message"
    delivered = "\n".join(m["text"] for m in bot.messages if m["chat_id"] == USER_ID)
    assert "Scout's finding: X is foo" in delivered, delivered
    assert "delegated" in delivered.lower(), delivered
    assert "scout" in delivered.lower(), delivered
    assert bot.messages[-1]["chat_id"] == USER_ID

    # Governor self-healed: the child's slot was released (back to full budget).
    assert governor.in_flight == 0

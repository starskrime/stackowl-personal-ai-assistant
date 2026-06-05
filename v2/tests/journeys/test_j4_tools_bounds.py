"""J4-tools JOURNEY — "an owl stays within its TOOLS bounds and reports cleanly".

The gateway-driven integration proof of FR33 / J4-tools (E2-S1). It drives a REAL
inbound user message all the way through the genuine ingress→pipeline→egress arc —
``TelegramChannelAdapter`` (inbound) → ``GatewayScanner`` (routes the ``@`` mention
to a BOUNDED owl) → ``AsyncioBackend`` pipeline → the B2-aware execute step (the
bounds seam) → ``TelegramChannelAdapter`` (outbound) — with ONLY the AI provider
mocked, exactly like the established journey harness (e.g. ``test_j3_debug_script``,
``test_j_durable_goal``).

The acting owl's manifest carries ``bounds=BoundsSpec(tools=frozenset({allowed}))``.
The scripted model (the ONLY mock) drives the REAL ``tool_dispatcher`` and tries to
call a FORBIDDEN tool. The business-outcome assertions are USER-VISIBLE:

  1. The ALLOWED tool genuinely RUNS (its real execute fires; its output is real).
  2. The FORBIDDEN tool is cleanly BLOCKED at the dispatch seam — its execute() is
     NEVER invoked, there is NO crash, the model receives a clean "not permitted by
     this owl's bounds" message, and the session CONTINUES and DELIVERS a final
     reply to the user's Telegram chat (the bounds block is a clean path, not a
     dead end).
  3. The SAME forbidden tool re-called within the turn short-circuits (loop-stop):
     the owl never enters a re-block loop.

An UNBOUNDED control owl (bounds=None) runs BOTH tools — the byte-for-byte legacy
arc is preserved through the very same gateway path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from stackowl.authz import BoundsSpec
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 515151

_ALLOWED_TOOL = "note_lookup"
_FORBIDDEN_TOOL = "wire_transfer"
_ALLOWED_OUTPUT = "LOOKUP-RESULT: balance is 42"
_FINAL_REPLY = "I looked that up for you; I'm not permitted to wire money, so I stopped there."
# A punctuation-free fragment of the reply — the Telegram adapter MarkdownV2-escapes
# punctuation outbound (``.`` → ``\.``), so assert on a fragment that survives escaping.
_REPLY_FRAGMENT = "not permitted to wire money"


# --- FAKED #1: the Telegram bot HTTP transport (captures outbound in-process) ----


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


# --- REAL tools: read-severity, record whether their execute() actually ran ------


class _RecordingTool(Tool):
    def __init__(self, name: str, output: str) -> None:
        self._name = name
        self._output = output
        self.runs = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Records execution of {self._name}."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output=self._output, error=None, duration_ms=1.0)


# --- FAKED #2 (THE ONLY AI MOCK): the bounded owl's scripted provider ------------


class _ScriptedBoundedOwl:
    """The ONLY mock. Within ONE ``complete_with_tools`` call it drives the REAL
    tool loop via the REAL ``tool_dispatcher``, exactly as a real model would:

      1. call the ALLOWED tool (it runs; capture its real output),
      2. try the FORBIDDEN tool (cleanly blocked by bounds — execute never runs),
      3. try the FORBIDDEN tool AGAIN (loop-stop short-circuit),
      4. return a final reply the adapter delivers to the user.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.allowed_out: str = ""
        self.forbidden_out: str = ""
        self.forbidden_out_2: str = ""

    @property
    def name(self) -> str:
        return "vault_owl"

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.allowed_out = await tool_dispatcher(_ALLOWED_TOOL, {})
        self.forbidden_out = await tool_dispatcher(_FORBIDDEN_TOOL, {})
        self.forbidden_out_2 = await tool_dispatcher(_FORBIDDEN_TOOL, {})
        return (_FINAL_REPLY, [])

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        # Honor the ModelProvider contract so the REAL triage/router steps run
        # genuinely (a bare string would crash triage — a no-hidden-errors miss).
        return CompletionResult(
            content="I'll look that up and stay within my permitted tools.",
            input_tokens=6, output_tokens=8, model="vault-model",
            provider_name="vault_owl", duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, p: _ScriptedBoundedOwl) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedBoundedOwl:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedBoundedOwl:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _ScriptedBoundedOwl:
        return self._p


# --- env wiring (modeled on the established journey harness) ----------------------


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedBoundedOwl
    allowed: _RecordingTool
    forbidden: _RecordingTool


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _bounded_manifest(bounds: BoundsSpec | None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name="vault_owl",
        role="vault-clerk",
        system_prompt="You look things up. You may only use your permitted tools.",
        model_tier="fast",
        bounds=bounds,
    )


def _build(provider: _ScriptedBoundedOwl, *, bounds: BoundsSpec | None) -> _Env:
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    allowed = _RecordingTool(_ALLOWED_TOOL, _ALLOWED_OUTPUT)
    forbidden = _RecordingTool(_FORBIDDEN_TOOL, "SHOULD-NEVER-APPEAR")
    registry = ToolRegistry()
    registry.register(allowed)
    registry.register(forbidden)

    owl_registry = OwlRegistry.with_default_secretary()
    owl_registry.register(_bounded_manifest(bounds))

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),  # read-severity tools → no consent fires
        stream_registry=StreamRegistry(),
        owl_registry=owl_registry,
    )
    return _Env(
        adapter=adapter, bot=bot,
        scanner=GatewayScanner(owl_registry=owl_registry),
        backend=AsyncioBackend(services=services),  # type: ignore[arg-type]
        stream_registry=services.stream_registry, provider=provider,
        allowed=allowed, forbidden=forbidden,
    )


async def _turn(env: _Env, text: str) -> str:
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
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


# --- BOUNDED: the forbidden tool is cleanly blocked; the session still delivers ---


async def test_j4_bounded_owl_blocks_forbidden_tool_and_still_replies() -> None:
    provider = _ScriptedBoundedOwl()
    env = _build(provider, bounds=BoundsSpec(tools=frozenset({_ALLOWED_TOOL})))

    reply = await _turn(env, "@vault_owl look up my balance and wire $1000")

    # OUTCOME 1 — the ALLOWED tool genuinely RAN (real execute fired, real output).
    assert env.allowed.runs == 1, "the allowed tool did not run under bounds"
    assert provider.allowed_out == _ALLOWED_OUTPUT

    # OUTCOME 2 — the FORBIDDEN tool was cleanly BLOCKED: execute NEVER ran, the
    # model got a clean bounds message, no crash.
    assert env.forbidden.runs == 0, "BOUNDS BREACH: the forbidden tool's execute ran"
    assert "not permitted by this owl's bounds" in provider.forbidden_out
    assert "SHOULD-NEVER-APPEAR" not in provider.forbidden_out

    # OUTCOME 3 — the session CONTINUED and DELIVERED a final reply to the user's
    # Telegram chat (the bounds block is a clean path, not a dead end / crash).
    assert _REPLY_FRAGMENT in reply, f"the bounded owl did not deliver a final reply. Got: {reply!r}"

    # OUTCOME 4 — loop-stop: the re-called forbidden tool short-circuits (no
    # re-block loop). Its execute still never ran.
    assert env.forbidden.runs == 0
    assert "already declined this turn" in provider.forbidden_out_2


# --- UNBOUNDED control: the SAME gateway path runs BOTH tools (legacy preserved) --


async def test_j4_unbounded_owl_runs_both_tools_unchanged() -> None:
    provider = _ScriptedBoundedOwl()
    env = _build(provider, bounds=None)  # unbounded owl

    reply = await _turn(env, "@vault_owl look up my balance and wire $1000")

    # Both tools run — byte-for-byte legacy behavior through the very same arc. The
    # scripted model calls the forbidden tool TWICE; with NO bounds (and a
    # read-severity tool that needs no consent) BOTH calls genuinely execute, so the
    # forbidden tool runs twice (no denied_this_run short-circuit on the legacy path).
    assert env.allowed.runs == 1
    assert env.forbidden.runs == 2
    assert provider.allowed_out == _ALLOWED_OUTPUT
    assert provider.forbidden_out == "SHOULD-NEVER-APPEAR"  # i.e. the forbidden tool RAN
    assert provider.forbidden_out_2 == "SHOULD-NEVER-APPEAR"
    assert _REPLY_FRAGMENT in reply


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])

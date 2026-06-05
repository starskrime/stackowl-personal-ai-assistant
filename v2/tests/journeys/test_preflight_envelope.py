"""E2-S3 GATEWAY JOURNEY — least-privilege presentation + drift-audit end-to-end.

Proves the full S3 arc at the gateway level:

  1. A durable task carries a ``task_envelope`` (from the planner) that permits
     only ``_ALLOWED_TOOL``, even though the owl manifest permits BOTH tools.
  2. The dispatch seam RESTRICTS the presented tool schema to ``envelope.tools``
     (drift prevention) — ``_FORBIDDEN_TOOL`` is hidden from ``complete_with_tools``.
  3. When the scripted model calls ``_FORBIDDEN_TOOL`` directly (off-plan), it
     STILL RUNS (observe-only, never blocked) because the owl ∩ ceiling boundary
     already permitted it.
  4. A single drift WARNING is emitted for the off-plan call (audit signal).
  5. The turn delivers a final reply to the user (full arc, no crash/dead-end).

Scaffolding is copied from ``test_tool_scope_envelope.py`` (the J-journey template)
and ``test_execute_drift_telemetry.py`` (log-capture approach).  The only mock is
the scripted AI provider; all other infrastructure is real.
"""

from __future__ import annotations

import asyncio
import logging
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

# ---------------------------------------------------------------------------
# Constants (mirror J4 / S2 journey pattern)
# ---------------------------------------------------------------------------

USER_ID = 424242

_ALLOWED_TOOL = "note_lookup"
_FORBIDDEN_TOOL = "wire_transfer"
_ALLOWED_OUTPUT = "LOOKUP-RESULT: balance is 42"
_FINAL_REPLY = "I looked that up for you; I'm not permitted to wire money, so I stopped there."
# Punctuation-free fragment — Telegram adapter MarkdownV2-escapes outbound.
_REPLY_FRAGMENT = "not permitted to wire money"


# ---------------------------------------------------------------------------
# FAKED #1: Telegram bot HTTP transport (captures outbound in-process)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# REAL recording tools (read-severity; record whether execute() ran)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# FAKED #2 (THE ONLY AI MOCK): the scripted provider
#
# Captures the presented ``tool_schemas`` so we can assert that
# ``_FORBIDDEN_TOOL`` was hidden from the schema when an envelope is active.
# Calls BOTH tools via the real ``tool_dispatcher`` to prove observe-only.
# ---------------------------------------------------------------------------


class _ScriptedBoundedOwl:
    """The ONLY mock.

    Drives the REAL tool loop via the REAL tool_dispatcher — calls BOTH the
    allowed and the forbidden tool so the drift path is exercised, then returns
    the canonical final reply.  Captures ``tool_schemas`` so the presentation
    assertion can verify ``_FORBIDDEN_TOOL`` was hidden from the schema.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.allowed_out: str = ""
        self.forbidden_out: str = ""
        self.seen_schemas: list[dict[str, object]] | None = None

    @property
    def name(self) -> str:
        return "vault_owl"

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        # Capture the presented schema (drift-prevention assertion).
        self.seen_schemas = list(tool_schemas) if tool_schemas is not None else []
        # Call BOTH tools — allowed runs cleanly; forbidden is off-plan (observe-only).
        self.allowed_out = await tool_dispatcher(_ALLOWED_TOOL, {})
        self.forbidden_out = await tool_dispatcher(_FORBIDDEN_TOOL, {})
        return (_FINAL_REPLY, [])

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
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


# ---------------------------------------------------------------------------
# Env wiring (modeled on the established J4 / S2 journey harness)
# ---------------------------------------------------------------------------


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
        consent_gate=ConsequentialActionGate(),
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


async def _turn(
    env: _Env,
    text: str,
    *,
    ceiling: BoundsSpec | None = None,
    task_envelope: BoundsSpec | None = None,
) -> str:
    """Drive one inbound turn through the full gateway arc.

    ``ceiling`` is forwarded as ``creation_ceiling`` (S2 enforcement seam).
    ``task_envelope`` is forwarded as ``task_envelope`` (S3 least-privilege
    presentation + drift telemetry seam).
    """
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
        creation_ceiling=ceiling,
        task_envelope=task_envelope,
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


# ---------------------------------------------------------------------------
# Helper: extract tool names from the presented schema list
# (handles both anthropic and openai protocol schemas)
# ---------------------------------------------------------------------------

def _schema_names(schemas: list[dict[str, object]] | None) -> set[str]:
    out: set[str] = set()
    for s in schemas or []:
        n = s.get("name")
        if isinstance(n, str):
            out.add(n)
            continue
        fn = s.get("function")
        if isinstance(fn, dict):
            inner = fn.get("name")
            if isinstance(inner, str):
                out.add(inner)
    return out


# ===========================================================================
# JOURNEY — least-privilege presentation + drift-audit end-to-end
# ===========================================================================


async def test_durable_envelope_hides_offplan_audits_on_use(  # noqa: ANN001
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gateway-level proof of the full E2-S3 arc.

    The owl permits {allowed, forbidden}; the task envelope plans only {allowed}.
    The scripted owl calls BOTH via the real dispatcher.  Outcome:

      1. ``_FORBIDDEN_TOOL`` is HIDDEN from the presented schema (drift prevention).
      2. ``_FORBIDDEN_TOOL`` STILL RUNS when called directly (observe-only boundary).
      3. A drift WARNING is logged for the off-plan call (audit signal).
      4. ``_ALLOWED_TOOL`` ran without restriction.
      5. The turn delivered a final reply (no crash, full arc).
    """
    # Owl permits both tools; the ENVELOPE (not the owl) drives hiding + auditing.
    owl_bounds = BoundsSpec(tools=frozenset({_ALLOWED_TOOL, _FORBIDDEN_TOOL}))
    provider = _ScriptedBoundedOwl()
    env = _build(provider, bounds=owl_bounds)
    envelope = BoundsSpec(tools=frozenset({_ALLOWED_TOOL}))

    with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
        reply = await _turn(
            env,
            "@vault_owl do the task",
            task_envelope=envelope,
        )

    # --- OUTCOME 1: allowed tool ran ---
    assert env.allowed.runs == 1, (
        f"The allowed tool should have run once, got runs={env.allowed.runs}"
    )

    # --- OUTCOME 2: forbidden tool STILL RUNS (observe-only, not blocked) ---
    assert env.forbidden.runs == 1, (
        "OBSERVE-ONLY BREACH: the off-plan tool's execute() did not run. "
        "task_envelope is NOT enforcement — it must never block tool execution."
    )

    # --- OUTCOME 3: drift WARNING fired for the off-plan tool ---
    def _is_drift_warning(r: logging.LogRecord) -> bool:
        if r.levelno != logging.WARNING:
            return False
        msg = r.getMessage()
        if "drift" not in msg.lower() and "off-plan" not in msg.lower():
            return False
        fields: dict[str, object] = getattr(r, "_fields", {})
        return fields.get("tool") == _FORBIDDEN_TOOL

    drift_records = [r for r in caplog.records if _is_drift_warning(r)]
    assert drift_records, (
        f"Expected at least one WARNING for off-plan '{_FORBIDDEN_TOOL}'. "
        f"Got records: {[(r.getMessage(), getattr(r, '_fields', {})) for r in caplog.records]}"
    )

    # --- OUTCOME 4: final reply delivered ---
    assert _REPLY_FRAGMENT in reply, (
        f"The turn did not deliver a final reply under the envelope. Got: {reply!r}"
    )

    # --- OUTCOME 5: presentation — forbidden_tool was HIDDEN from the schema ---
    # The provider captured the schema passed to complete_with_tools; the
    # task_envelope restrict_to must have excluded _FORBIDDEN_TOOL.
    presented = _schema_names(provider.seen_schemas)
    assert _FORBIDDEN_TOOL not in presented, (
        f"PRESENTATION BREACH: '{_FORBIDDEN_TOOL}' appeared in the schema presented "
        f"to complete_with_tools even though it is off-plan. presented={presented}"
    )
    assert _ALLOWED_TOOL in presented, (
        f"On-plan '{_ALLOWED_TOOL}' must be in the presented schema. presented={presented}"
    )


# ===========================================================================
# CONTROL — proves the envelope (not the owl) is driving the hiding
# ===========================================================================


async def test_no_envelope_both_tools_presented_and_run() -> None:
    """CONTROL: without a task_envelope, both tools appear in the schema and run.

    This proves that in the companion journey test it is the ENVELOPE (not some
    accidental owl-bounds narrowing) that hides ``_FORBIDDEN_TOOL`` from the schema.
    """
    owl_bounds = BoundsSpec(tools=frozenset({_ALLOWED_TOOL, _FORBIDDEN_TOOL}))
    provider = _ScriptedBoundedOwl()
    env = _build(provider, bounds=owl_bounds)

    # No task_envelope → full schema presented, both tools run.
    reply = await _turn(env, "@vault_owl do the task")

    # CONTROL — both tools ran.
    assert env.allowed.runs == 1, "allowed tool must run (wide owl, no envelope)"
    assert env.forbidden.runs == 1, (
        "CONTROL FAILURE: forbidden tool did not run with no envelope. "
        "Something other than the envelope is blocking it — investigate before "
        "trusting test_durable_envelope_hides_offplan_audits_on_use."
    )

    # CONTROL — both tools appeared in the presented schema.
    presented = _schema_names(provider.seen_schemas)
    assert _FORBIDDEN_TOOL in presented, (
        f"CONTROL FAILURE: '{_FORBIDDEN_TOOL}' was missing from schema with no envelope. "
        f"presented={presented}"
    )
    assert _ALLOWED_TOOL in presented, (
        f"'{_ALLOWED_TOOL}' missing from schema with no envelope. presented={presented}"
    )

    # CONTROL — reply delivered.
    assert _REPLY_FRAGMENT in reply, (
        f"The turn did not deliver a reply (no envelope, wide owl). Got: {reply!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])

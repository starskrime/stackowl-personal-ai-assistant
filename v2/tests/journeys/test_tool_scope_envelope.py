"""E2-S2 GATEWAY JOURNEYS — task-scope deny + kill/resume monotonicity.

Two gateway-level proofs of the authorization-envelope business outcomes:

JOURNEY 1 — task-scope deny end-to-end
  An owl whose manifest bounds permit BOTH tools is handed a TURN under a task
  ``creation_ceiling`` that permits ONLY the allowed tool.  Even though the owl
  itself would allow the forbidden tool, the ceiling at the ``PipelineState``
  level narrows the effective bounds — the forbidden tool is blocked end-to-end
  through the real ingress → pipeline → execute seam, and the session still
  delivers a final reply.  This is the E2-S2 proof of FR33/J4 extended to the
  task-scope envelope axis.

JOURNEY 2 — resume-under-widened-owl stays clamped to the creation ceiling
  (security-critical monotonicity / TOCTOU proof)
  A durable task is persisted into a REAL migrated store with a NARROW
  ``creation_ceiling`` {_ALLOWED}.  The owl registry is then WIDENED to
  {_ALLOWED, _FORBIDDEN}.  ``DurableTaskRecoverer._reconstruct_state`` is
  called to produce the resumed ``PipelineState`` from the real persisted row
  — the ceiling comes back FROM DISK.  The resumed drive runs under the WIDE
  owl manifest but the NARROW persisted ceiling.  The effective bounds are
  ``wide_owl ∩ narrow_ceiling = {_ALLOWED}``, so the newly-granted tool is
  denied even though the live owl would permit it.  This proves the full
  persist → reconstruct → enforce chain in one test.

Scaffolding is adapted from ``test_j4_tools_bounds.py`` (the J4-tools journey
template).  The Telegram adapter doubles (_FakeBot, _FakeBotApp, _ScriptedBoundedOwl,
_FakeProviderRegistry, _RecordingTool, _Env, _build, _turn) are copied here so
this file stands alone, matching the J4 style precisely.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.authz import BoundsSpec
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.recovery import DurableTaskRecoverer
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

# ---------------------------------------------------------------------------
# Constants (mirror J4 pattern)
# ---------------------------------------------------------------------------

USER_ID = 515151

_ALLOWED_TOOL = "note_lookup"
_FORBIDDEN_TOOL = "wire_transfer"
_ALLOWED_OUTPUT = "LOOKUP-RESULT: balance is 42"
_FINAL_REPLY = "I looked that up for you; I'm not permitted to wire money, so I stopped there."
# Punctuation-free fragment — the Telegram adapter MarkdownV2-escapes outbound.
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
# REAL tools: read-severity, record whether execute() actually ran
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
# FAKED #2 (THE ONLY AI MOCK): the bounded owl's scripted provider
# ---------------------------------------------------------------------------


class _ScriptedBoundedOwl:
    """The ONLY mock.  Drives the REAL tool loop via the REAL tool_dispatcher —
    calls BOTH the allowed and the forbidden tool so the ceiling enforcement is
    exercised, then returns the canonical final reply.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.allowed_out: str = ""
        self.forbidden_out: str = ""

    @property
    def name(self) -> str:
        return "vault_owl"

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
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
# Env wiring (modeled on the established J4 journey harness)
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


async def _turn(env: _Env, text: str, *, ceiling: BoundsSpec | None = None) -> str:
    """Drive one inbound turn through the full gateway arc.

    ``ceiling`` is forwarded into the PipelineState as ``creation_ceiling`` — it
    models the task-scope envelope that DurableTaskRunner snapshots at creation
    time and that recovery._reconstruct_state re-threads on resume.
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
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


# ===========================================================================
# JOURNEY 1 — task-scope deny end-to-end
# ===========================================================================


async def test_task_envelope_denies_tool_owl_would_allow() -> None:
    """A task ceiling narrower than the owl's own bounds is enforced end-to-end.

    The owl manifest permits BOTH tools (owl_bounds = {_ALLOWED, _FORBIDDEN}).
    The turn runs under a ceiling that permits ONLY {_ALLOWED}.  The scripted
    model calls both tools.  Outcome assertions:

      * ``_ALLOWED_TOOL`` ran (its real execute() fired — real output captured).
      * ``_FORBIDDEN_TOOL`` was cleanly BLOCKED by the ceiling (execute never ran;
        no crash; the model received a clean "not permitted" reason).
      * The session CONTINUED and DELIVERED a final reply to the user — a ceiling
        block is a clean path, not a dead end.

    This proves effective = owl_bounds ∩ ceiling = {_ALLOWED} through the REAL
    ingress → gateway → pipeline → execute seam.
    """
    owl_bounds = BoundsSpec(tools=frozenset({_ALLOWED_TOOL, _FORBIDDEN_TOOL}))
    provider = _ScriptedBoundedOwl()
    env = _build(provider, bounds=owl_bounds)
    ceiling = BoundsSpec(tools=frozenset({_ALLOWED_TOOL}))

    reply = await _turn(env, "@vault_owl look up my balance and wire $1000", ceiling=ceiling)

    # OUTCOME 1 — the ALLOWED tool genuinely RAN (owl bounds + ceiling both permit it).
    assert env.allowed.runs == 1, "the allowed tool did not run under task ceiling"
    assert provider.allowed_out == _ALLOWED_OUTPUT

    # OUTCOME 2 — the FORBIDDEN tool was cleanly BLOCKED by the ceiling: the owl
    # alone would allow it, but the ceiling says no — execute NEVER ran, no crash.
    assert env.forbidden.runs == 0, (
        "CEILING BREACH: the forbidden tool's execute ran even though the "
        "task ceiling excludes it (owl bounds would have allowed it)"
    )
    assert "not permitted by this owl's bounds" in provider.forbidden_out, (
        f"Expected ceiling-block reason in forbidden_out, got: {provider.forbidden_out!r}"
    )
    assert "SHOULD-NEVER-APPEAR" not in provider.forbidden_out

    # OUTCOME 3 — the session CONTINUED and DELIVERED a final reply (the ceiling
    # block is a clean path, not a dead-end/crash).
    assert _REPLY_FRAGMENT in reply, (
        f"The turn did not deliver a final reply under the ceiling. Got: {reply!r}"
    )


# ===========================================================================
# JOURNEY 2 — resume-under-widened-owl stays clamped to the creation ceiling
#
# End-to-end monotonicity proof through the REAL recovery path:
#   persist → DurableTaskRecoverer._reconstruct_state → enforce.
#
# The test seeds a DurableTask into a REAL migrated store, calls
# _reconstruct_state to pull the narrow ceiling BACK FROM DISK, then drives
# _run_with_tools with a WIDE owl registry — proving the full chain in one shot.
# ===========================================================================

_RECOVERY_OWNER = "principal-default"


class _NullBackend:
    """OrchestratorBackend stub — _reconstruct_state never calls backend.run."""

    async def run(self, state: PipelineState) -> PipelineState:  # pragma: no cover
        return state


class _RecordingDispatchTool(Tool):
    """A read-severity tool that records whether its execute() was called."""

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self.executed = False

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return f"Records execution of {self._tool_name}."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._tool_name, description=self.description,
            parameters=self.parameters, action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.executed = True
        return ToolResult(success=True, output=f"RAN:{self._tool_name}", duration_ms=1.0)


class _WideOwlProvider:
    """Scripted provider that calls BOTH tools — exactly what a real model would
    do if the owl's WIDENED manifest made both tools appear in the schema.

    The ceiling must clamp the effective bounds back to NARROW, so the
    newly-granted (forbidden) tool is DENIED even though the live owl allows it.
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.results: dict[str, str] = {}

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        self.results[_ALLOWED_TOOL] = await tool_dispatcher(_ALLOWED_TOOL, {})
        self.results[_FORBIDDEN_TOOL] = await tool_dispatcher(_FORBIDDEN_TOOL, {})
        return ("done", [])


@pytest.fixture()
async def _recovery_pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    """Real migrated DbPool for the monotonicity journey test."""
    db_path = tmp_path / "envelope_recovery.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def test_resume_under_widened_owl_stays_clamped_to_ceiling(
    _recovery_pool: DbPool,
) -> None:
    """Resume monotonicity: a widened owl cannot gain new tool permissions mid-task.

    TOCTOU / resume-monotonicity guarantee proven through the REAL
    ``DurableTaskRecoverer._reconstruct_state`` reconstruction path.

    Scenario (the security-critical invariant):
      * A durable task is PERSISTED into a real migrated store with
        ``creation_ceiling = {_ALLOWED}`` — the narrow snapshot taken when the
        owl had narrow bounds at task creation time.
      * The owl registry is then WIDENED to {_ALLOWED, _FORBIDDEN}: the live
        owl manifest now permits _FORBIDDEN.
      * ``DurableTaskRecoverer._reconstruct_state`` is called with the real
        persisted task row — it reads the narrow ceiling BACK FROM DISK and
        threads it into the resumed ``PipelineState``.
      * The resumed drive is run through ``_run_with_tools`` with the WIDE owl
        registry in scope and a scripted provider that calls BOTH tools.

    Expected outcome (effective = wide_owl ∩ narrow_ceiling = {_ALLOWED}):
      → _ALLOWED_TOOL: RUNS (within both owl bounds AND the ceiling)
      → _FORBIDDEN_TOOL: BLOCKED (ceiling excludes it even though owl now allows it)

    This proves the full persist → reconstruct → enforce chain in one test,
    closing the TOCTOU window: widening an owl's live manifest between task
    creation and task resume MUST NOT grant additional tool access to that task.
    """
    _ALLOWED = _ALLOWED_TOOL
    _FORBIDDEN = _FORBIDDEN_TOOL

    # --- 1. Persist a real DurableTask carrying the narrow creation ceiling ---
    # Mirrors the seeding pattern from tests/pipeline/durable/test_recovery_ceiling.py.
    narrow_ceiling = BoundsSpec(tools=frozenset({_ALLOWED}))
    task_id = "monotonicity-journey-1"
    now = datetime.now(tz=UTC)
    task = DurableTask(
        task_id=task_id,
        owner_id=_RECOVERY_OWNER,
        goal="continue the goal",
        status="running",
        owl_name="vault_owl",
        channel="cli",
        creation_ceiling=narrow_ceiling,
        created_at=now,
        updated_at=now,
    )
    store = DurableTaskStore(_recovery_pool, _RECOVERY_OWNER)
    await store.create(task)

    # --- 2. Reconstruct the state through the REAL recovery path ---
    # DurableTaskRecoverer._reconstruct_state reads the ceiling FROM DISK and
    # threads it into the returned PipelineState — no checkpoint branch (task
    # crashed before iteration 0 completed; the no-checkpoint branch is the
    # simplest and sufficient to prove the ceiling roundtrip).
    recovery = DurableTaskRecoverer(_recovery_pool, _NullBackend(), owner_id=_RECOVERY_OWNER)
    persisted_task = await store.get(task_id)
    state = await recovery._reconstruct_state(persisted_task)

    # Sanity: the ceiling survived the persist → reconstruct round-trip.
    assert state.creation_ceiling == narrow_ceiling, (
        f"ceiling lost during persist→reconstruct: got {state.creation_ceiling!r}"
    )

    # --- 3. Build the WIDE owl registry (post-widening, live state) ---
    # The owl now permits BOTH tools in the live manifest — this simulates the
    # owl's bounds having been widened between task creation and task resume.
    wide_bounds = BoundsSpec(tools=frozenset({_ALLOWED, _FORBIDDEN}))
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name="vault_owl",
        role="vault-clerk",
        system_prompt="Resumed wide-owl.",
        model_tier="fast",
        bounds=wide_bounds,
    ))

    # --- 4. Set up tools and drive the REAL dispatch seam ---
    allowed = _RecordingDispatchTool(_ALLOWED)
    forbidden = _RecordingDispatchTool(_FORBIDDEN)
    registry = ToolRegistry()
    registry.register(allowed)
    registry.register(forbidden)

    provider = _WideOwlProvider()
    token = set_services(StepServices(
        tool_registry=registry,
        owl_registry=owl_registry,
        db_pool=_recovery_pool,
    ))
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # --- 5. Security assertions: the narrow ceiling held the line ---

    # The allowed tool ran — it is within BOTH wide_owl_bounds AND narrow_ceiling.
    assert allowed.executed is True, (
        "The allowed tool should have run (it is within both owl bounds and the ceiling)"
    )

    # CRITICAL: the newly-granted tool is DENIED because the ceiling (read back
    # from disk by _reconstruct_state) excludes it, even though the live owl
    # manifest now allows it.  runs==0 proves execute() never fired.
    assert forbidden.executed is False, (
        "MONOTONICITY BREACH: the forbidden tool ran under a resumed state "
        "even though the persisted ceiling excludes it.  Widening the owl's "
        "live manifest must NOT grant new tool access to existing durable tasks."
    )
    assert "not permitted by this owl's bounds" in provider.results.get(_FORBIDDEN, ""), (
        f"Expected ceiling-block reason for forbidden tool. "
        f"Got: {provider.results.get(_FORBIDDEN, '<missing>')!r}"
    )


# ===========================================================================
# CONTROL — proves it is the CEILING (not the owl bounds) that blocks the tool
# ===========================================================================


async def test_no_ceiling_owl_runs_both_tools_proving_ceiling_is_the_blocker() -> None:
    """CONTROL for test_task_envelope_denies_tool_owl_would_allow: same WIDE owl
    (permits both tools), but NO task ceiling. Both tools run — proving that in
    the companion test it was the CEILING (not the owl bounds) that blocked the
    forbidden tool. Without this control, an accidentally-narrow owl manifest
    would make the deny test pass vacuously.
    """
    owl_bounds = BoundsSpec(tools=frozenset({_ALLOWED_TOOL, _FORBIDDEN_TOOL}))
    provider = _ScriptedBoundedOwl()
    env = _build(provider, bounds=owl_bounds)
    reply = await _turn(env, "@vault_owl look up my balance and wire $1000")  # no ceiling

    # CONTROL OUTCOME 1 — the allowed tool genuinely RAN (owl permits it, no ceiling).
    assert env.allowed.runs == 1, "the allowed tool did not run (wide owl, no ceiling)"

    # CONTROL OUTCOME 2 — the forbidden tool also RAN (owl permits it, nothing narrows it).
    # If this assertion fails, something other than the ceiling is blocking the tool —
    # stop and investigate before trusting the companion deny test.
    assert env.forbidden.runs == 1, (
        "CONTROL FAILURE: the forbidden tool's execute() did not run under a wide owl "
        "with no task ceiling.  This means something other than the ceiling is blocking "
        "it, which would make test_task_envelope_denies_tool_owl_would_allow vacuous."
    )

    # CONTROL OUTCOME 3 — the session delivered a final reply (sanity check).
    assert _REPLY_FRAGMENT in reply, (
        f"The turn did not deliver a final reply (wide owl, no ceiling). Got: {reply!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])

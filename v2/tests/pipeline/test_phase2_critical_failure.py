"""Phase 2 #2 — surface CRITICAL pipeline-step (execute) failures to the user.

Background: the backends self-heal — a step exception is ERROR-logged, appended
to ``state.errors``, and the loop CONTINUES. For NON-critical steps that is fine
(they degrade gracefully). But when ``execute`` (the answer-producing step) fails
with NO usable response, the user was left with silence. This suite proves the
surfacing helper now injects a user-facing apology BEFORE deliver, while
non-critical failures stay silent (self-healed).

Drives the REAL ``AsyncioBackend.run`` and, for the gateway test, the real
``GatewayScanner.scan`` → state-construction → ``backend.run`` entry path.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


# ---- Fakes ------------------------------------------------------------------


class _FailingExecuteProvider(ModelProvider):
    """Provider whose tool-loop (the execute path) RAISES → execute step fails.

    Resolved under the owl key so ``execute.run`` lands on it on the tool-loop
    branch. ``complete`` ALSO raises so this provider is useless for the apology
    cascade too — the cascade must fall through to a SEPARATE healthy provider.
    """

    def __init__(self) -> None:
        self._name = "failing"

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        raise RuntimeError("provider down (complete)")

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # type: ignore[override]
        raise RuntimeError("provider down (stream)")
        yield ""  # pragma: no cover — unreachable, makes this an async generator

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
    ) -> tuple[str, list]:
        raise RuntimeError("provider down (tool loop)")


class _ApologyProvider(ModelProvider):
    """Healthy fallback used ONLY for the apology cascade. Records its calls."""

    def __init__(self, reply: str = "Lo siento, tu solicitud no pudo completarse.") -> None:
        self._name = "apology"
        self._reply = reply
        self.complete_calls = 0
        self.last_user_text: str | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self.complete_calls += 1
        for m in messages:
            if m.role == "user":
                self.last_user_text = m.content
        return CompletionResult(
            content=self._reply,
            input_tokens=10,
            output_tokens=8,
            model="apology-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # type: ignore[override]
        yield self._reply

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
    ) -> tuple[str, list]:
        return self._reply, []


class _HappyProvider(ModelProvider):
    """Healthy provider whose execute succeeds with a real answer."""

    def __init__(self, reply: str = "The real answer.") -> None:
        self._name = "happy"
        self._reply = reply

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content=self._reply,
            input_tokens=10,
            output_tokens=3,
            model="happy-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # type: ignore[override]
        yield self._reply

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list,
        tool_dispatcher,
        max_iterations: int = 8,
        history: list[Message] | None = None,
    ) -> tuple[str, list]:
        return self._reply, []


# ---- Helpers ----------------------------------------------------------------


def _delivered_text(state: PipelineState) -> str:
    return "\n".join(c.content for c in state.responses if c.content)


def _state(owl_name: str = "secretary", *, session: str = "sess-crit") -> PipelineState:
    return PipelineState(
        trace_id="trace-crit",
        session_id=session,
        input_text="¿Cuál es la capital de Francia?",
        channel="cli",
        owl_name=owl_name,
        pipeline_step="start",
        interactive=True,
    )


# ---- Tests ------------------------------------------------------------------


async def test_execute_failure_surfaces_user_message(tmp_db: DbPool) -> None:
    """execute fails (no response) → a localized apology is delivered, not silence.

    GATEWAY INTEGRATION: drives the real GatewayScanner.scan → state construction
    → backend.run path (the production entry path).
    """
    bridge = SqliteMemoryBridge(db=tmp_db)
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()
    assert tool_registry.all(), "non-empty tool_registry forces the execute tool-loop branch"

    failing = _FailingExecuteProvider()
    apology = _ApologyProvider()

    preg = ProviderRegistry()
    # The owl's provider (execute resolves this) is the FAILING one, registered on
    # the "powerful" tier (execute's tier fallback also lands here).
    preg.register_mock("secretary", failing, tier="powerful")
    preg.register_mock("powerful", failing, tier="powerful")
    # A SEPARATE healthy provider on the "fast" tier — where the apology cascade
    # starts — so the cascade can localize even though execute's provider is down.
    preg.register_mock("apology", apology, tier="fast")

    services = StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )
    backend = AsyncioBackend(services=services)

    # --- Real gateway entry path ---
    scanner = GatewayScanner(owl_registry=owl_registry)
    msg = IngressMessage(
        text="¿Cuál es la capital de Francia?",
        session_id="sess-crit-gw",
        channel="cli",
        trace_id="trace-crit-gw",
    )
    decision = scanner.scan(msg)
    assert decision.route == "owl"
    assert decision.target == "secretary"

    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=decision.stripped_text if decision.stripped_text is not None else msg.text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )
    final = await backend.run(state)

    # The user is NOT left in silence — the localized apology was delivered.
    delivered = _delivered_text(final)
    assert delivered, "user got silence — no response was surfaced for the execute failure"
    assert apology.complete_calls >= 1, "apology cascade did not run"
    assert apology._reply in delivered, f"expected localized apology in delivered text, got {delivered!r}"

    # Telemetry still sees the execute failure (still visible to devs).
    assert any(e.startswith("execute: ") for e in final.errors), (
        f"execute failure must still be recorded in state.errors; got {final.errors}"
    )


async def test_non_critical_failure_does_not_inject_user_error(tmp_db: DbPool) -> None:
    """A NON-critical step (assemble) raises while execute succeeds → real answer
    is delivered and NO apology is injected (non-critical self-heals silently)."""
    import stackowl.pipeline.registry as registry_mod

    bridge = SqliteMemoryBridge(db=tmp_db)
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()

    happy = _HappyProvider()
    apology = _ApologyProvider()
    preg = ProviderRegistry()
    preg.register_mock("secretary", happy, tier="powerful")
    preg.register_mock("powerful", happy, tier="powerful")
    preg.register_mock("apology", apology, tier="fast")

    services = StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )
    backend = AsyncioBackend(services=services)

    async def _boom(state: PipelineState) -> PipelineState:
        raise RuntimeError("assemble blew up")

    # PIPELINE_STEPS binds each step fn by reference at import time, so we swap the
    # tuple entry for "assemble" rather than the module attribute.
    original_steps = list(registry_mod.PIPELINE_STEPS)
    registry_mod.PIPELINE_STEPS[:] = [
        (name, _boom if name == "assemble" else fn) for name, fn in original_steps
    ]
    try:
        final = await backend.run(_state(session="sess-noncrit"))
    finally:
        registry_mod.PIPELINE_STEPS[:] = original_steps

    delivered = _delivered_text(final)
    # The real answer is delivered ...
    assert happy._reply in delivered, f"real answer should be delivered; got {delivered!r}"
    # ... and NO apology was injected (the non-critical failure self-healed silently).
    # NOTE: we assert on the DELIVERED text, not apology.complete_calls — other
    # pipeline machinery (router/critic/entity-extractor) legitimately pulls the
    # "fast" tier provider, so a call count is not a reliable surfacing signal.
    assert apology._reply not in delivered, (
        f"apology text must not appear for a non-critical failure; got {delivered!r}"
    )
    # The non-critical failure is still recorded for telemetry.
    assert any(e.startswith("assemble: ") for e in final.errors), (
        f"assemble failure should be recorded; got {final.errors}"
    )


async def test_apology_falls_back_to_neutral_when_cascade_also_fails(tmp_db: DbPool) -> None:
    """execute fails AND the apology cascade also fails (total outage) → a
    non-empty neutral fallback is still delivered (never silence, never raises)."""
    bridge = SqliteMemoryBridge(db=tmp_db)
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry.with_defaults()

    failing = _FailingExecuteProvider()
    preg = ProviderRegistry()
    # EVERY provider is the failing one — execute fails AND the apology cascade's
    # provider.complete raises → the helper must use its neutral last-resort.
    preg.register_mock("secretary", failing, tier="powerful")
    preg.register_mock("powerful", failing, tier="powerful")
    preg.register_mock("failing-fast", failing, tier="fast")

    services = StepServices(
        memory_bridge=bridge,
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )
    backend = AsyncioBackend(services=services)

    final = await backend.run(_state(session="sess-neutral"))

    delivered = _delivered_text(final)
    assert delivered, "neutral fallback must still deliver a non-empty message — never silence"
    # The neutral marker carries the failure class for debuggability.
    assert "RuntimeError" in delivered, f"neutral marker should carry failure class; got {delivered!r}"
    assert any(e.startswith("execute: ") for e in final.errors)

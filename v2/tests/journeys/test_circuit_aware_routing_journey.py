"""Circuit-Aware Routing Journey — end-to-end proof of provider-fallback recovery.

When the user's tier provider (powerful) has an OPEN circuit, the answer falls
back to a healthy provider, a ``provider_fallback`` recovery is recorded, the
user sees a GENERIC line ("ℹ️ The usual model was unavailable, so a backup
completed this." — NO provider names), and a ``[recovery] turn summary`` log
carries the real names.  All-circuits-open → the turn floors honestly (no
recovery claim).

Drives the REAL AsyncioBackend pipeline + REAL provider registry; mocks ONLY
the AI providers (scripted completions).  Mirrors the harness from
``tests/journeys/test_recovery_explainability_journey.py``.

Harness note — Step 0 bypass:
  ``_select_tool_provider`` Step 0 does ``registry.get(owl_name)`` WITHOUT any
  circuit-breaker check, so even an OPEN-circuit named slot is returned
  directly.  After triage, ``owl_name`` is "secretary".  To force the
  circuit-aware cascade path (Steps 3-4) we deliberately do NOT register a
  "secretary" named provider slot: ``registry.get("secretary")`` raises
  ``ProviderNotFoundError`` and execution falls through to
  ``resolve_tier_with_fallback("powerful")`` — the circuit-aware path.

Provider implementation note:
  We use ``_ScriptedProvider`` (a custom ``ModelProvider`` subclass) rather than
  wrapping ``OpenAIProvider`` around a fake client.  ``OpenAIProvider.complete()``
  accesses ``response.usage`` without a guard, which would fail on a minimal fake
  response.  ``_ScriptedProvider`` returns ``CompletionResult`` directly, bypasses
  the OpenAI SDK entirely, and is compatible with both the ``complete()`` call path
  (used by the persistence judge) and the ``complete_with_tools()`` base-class
  default (which delegates to ``complete()``).

FR coverage:
  FR1 — happy-path: powerful circuit OPEN, healthy backup answers →
         answer delivered and GENERIC backup line present.
  FR2 — no-fallback: all providers healthy → answer delivered, NO backup line.
  FR3 — name-leak guard: backup line must NOT contain the opened provider's name
         NOR the backup provider's name (FR3 = FR1 assertion subset).
  FR5 — all-open floors: every answer-path provider OPEN → floor/honest-failure
         delivered and NO backup line (no false-recovery claim).
  FR6 — broad log: ``[recovery] turn summary`` log record emitted on
         ``stackowl.engine`` logger whose ``events`` carry real provider names.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

# --------------------------------------------------------------------------- #
# _ScriptedProvider — directly implements ModelProvider with canned responses.
#
# Avoids wrapping OpenAIProvider (which calls ``response.usage`` in complete()
# without a guard, failing on minimal fake response objects). Returns
# CompletionResult directly — compatible with complete(), stream(), and the
# base-class complete_with_tools() which delegates to complete().
# --------------------------------------------------------------------------- #


class _ScriptedProvider(ModelProvider):
    """Returns canned responses in sequence; directly implements ModelProvider.

    Each call to ``complete()`` (or ``complete_with_tools()`` via the base-class
    default) consumes the next scripted reply.  If replies are exhausted the last
    one is repeated.  Never makes network calls.
    """

    def __init__(self, name: str, replies: list[str]) -> None:
        self._name = name
        self._replies = replies
        self._i = 0
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.calls.append(list(messages))
        idx = min(self._i, len(self._replies) - 1)
        text = self._replies[idx]
        self._i += 1
        return CompletionResult(
            content=text,
            input_tokens=1,
            output_tokens=1,
            model="scripted-model",
            provider_name=self._name,
            duration_ms=1.0,
        )

    async def stream(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:  # type: ignore[override]
        self.calls.append(list(messages))
        idx = min(self._i, len(self._replies) - 1)
        text = self._replies[idx]
        self._i += 1
        yield text


# --------------------------------------------------------------------------- #
# Router / judge provider — always routes to "secretary" and confirms delivery.
# Identical pattern to sibling journey harnesses.
# --------------------------------------------------------------------------- #


class _RouterJudgeProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "router-judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            '{"delivered": true, "reason": "looks complete"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="router-judge-fake",
            provider_name="router-judge-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield "secretary"


# --------------------------------------------------------------------------- #
# Registry/service builder constants.
#
# Registered slots (NO "secretary" slot — see Step 0 bypass note above):
#   _POWERFUL_NAME  @ tier "powerful"  — primary answer provider (OPEN in FR1/FR5)
#   _FAST_NAME      @ tier "fast"      — healthy cascade fallback (OPEN in FR5)
#   "router"        @ tier "fast"      — SecretaryRouter LLM (OPEN in FR5)
#   "local-judge"   @ tier "local"     — persistence judge fallback (OPEN in FR5)
#
# Cascade from "powerful" when OPEN: powerful → local → fast → standard.
# For FR1: powerful OPEN, local ("local-judge") healthy → recovered via local-judge.
# BUT: we want recovered_via = _FAST_NAME, not "router-judge-fake".
#
# Tier ordering fix: register "local-judge" at "standard" tier so the cascade
# skips "local" (empty) and hits "fast" first, finding _FAST_NAME first.
#
# "router" is also at "fast"; registered BEFORE _FAST_NAME so SecretaryRouter's
# get_with_cascade("fast") picks "router" (correct for routing). The answer cascade
# also walks "fast" and finds "router" first — but we want _FAST_NAME.
#
# FINAL fix: register _FAST_NAME at "fast" tier BEFORE "router". Then:
# - SecretaryRouter's get_with_cascade("fast") picks _FAST_NAME first.
# - But _FAST_NAME.complete() works fine (_ScriptedProvider, no OpenAI client).
# - The router calls _FAST_NAME.complete() which returns the answer text — this is
#   the SecretaryRouter, which parses the result as an owl name.  "The answer to
#   your question is 42." is not a known owl name → collapses to "secretary". ✓
# - Answer cascade picks _FAST_NAME. ✓
# --------------------------------------------------------------------------- #

_POWERFUL_NAME = "powerful-main"
_FAST_NAME = "fast-backup"


def _open_breaker(registry: ProviderRegistry, name: str) -> None:
    """Open the circuit for the named provider (failure_threshold=3 → 3 failures)."""
    for _ in range(3):
        registry._breakers[name]._record_failure()


def _build_registry(
    *,
    powerful_provider: _ScriptedProvider,
    fast_provider: _ScriptedProvider,
) -> ProviderRegistry:
    """Build a ProviderRegistry with two DISTINCT answer providers at different tiers.

    Deliberately omits the "secretary" slot so ``_select_tool_provider`` Step 0
    fails and falls through to tier routing (Steps 3-4).

    Registration order within tier "fast": _FAST_NAME first, "router" second.
    - SecretaryRouter uses get_with_cascade("fast") → picks _FAST_NAME (first).
      Its scripted response (the answer text) is not a known owl name → collapses
      to "secretary" route. ✓
    - Answer cascade from "powerful" when OPEN: powerful → local (empty) → fast.
      Picks _FAST_NAME (first at "fast"). ✓

    "local-judge" at "local" tier stays healthy for FR1/FR2 and is also opened
    in FR5.  In the cascade from "powerful": powerful → local → fast → standard.
    Since we DON'T register anything at "local" for FR1 (local-judge IS at local),
    the cascade hits "local-judge" FIRST. To avoid that, register "local-judge"
    at tier "standard" so "fast" tier is hit before "standard". Wait — the cascade
    order from "powerful" is: powerful, local, fast, standard. If local-judge is at
    "local", it IS first in the cascade after "powerful". We need it NOT to be there.

    RESOLUTION: register "local-judge" at "standard" tier. Cascade from "powerful":
    powerful (OPEN in FR1) → local (empty) → fast (_FAST_NAME first → picked). ✓
    The persistence judge is found via get_with_cascade("local") which falls through
    to "standard" and finds "local-judge". ✓
    """
    preg = ProviderRegistry()
    preg.register_mock(_POWERFUL_NAME, powerful_provider, tier="powerful")
    # _FAST_NAME registered BEFORE "router" at "fast" — picked first in cascade.
    preg.register_mock(_FAST_NAME, fast_provider, tier="fast")
    router = _RouterJudgeProvider()
    preg.register_mock("router", router, tier="fast")
    # "local-judge" at "standard" so it doesn't block the "powerful" → "fast" cascade.
    preg.register_mock("local-judge", router, tier="standard")
    return preg


def _build_services(
    preg: ProviderRegistry,
    tool_registry: ToolRegistry | None = None,
) -> StepServices:
    return StepServices(
        provider_registry=preg,
        owl_registry=OwlRegistry.with_default_secretary(),
        tool_registry=tool_registry or ToolRegistry(),
    )


# --------------------------------------------------------------------------- #
# Turn executor — mirrors test_recovery_explainability_journey.py EXACTLY.
# --------------------------------------------------------------------------- #


async def _execute_turn(
    text: str,
    session: str,
    trace: str,
    backend: AsyncioBackend,
) -> str:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(
        text=text,
        session_id=session,
        channel="cli",
        trace_id=trace,
    )
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )
    final_state = await backend.run(state)
    return "".join(c.content for c in final_state.responses)


# =========================================================================== #
# FR1 / FR3 / FR6 — powerful OPEN, backup answers; generic line; log has names.
# =========================================================================== #


@pytest.mark.asyncio
async def test_backup_answers_when_powerful_circuit_open(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR1/FR3/FR6: When the powerful-tier provider's circuit is OPEN, the cascade
    falls back to the fast-tier backup.

    FR1: The backup produces the real answer and it is delivered to the user.
    FR3: The delivered text contains the GENERIC backup line ("ℹ️"/"backup") and
         does NOT contain the opened provider's registered name (_POWERFUL_NAME) nor
         the backup's registered name (_FAST_NAME) — privacy-preserving, no leak.
    FR6: A ``[recovery] turn summary`` log record is emitted on the
         ``stackowl.engine`` logger; its ``events`` field carries the real provider
         names (``failed=_POWERFUL_NAME``, ``recovered_via=_FAST_NAME``).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # The powerful provider's circuit is OPEN; it should never be called.
    powerful_provider = _ScriptedProvider(_POWERFUL_NAME, ["SHOULD NOT BE REACHED"])
    # The fast backup is healthy; it returns the real answer.
    fast_provider = _ScriptedProvider(_FAST_NAME, ["The answer to your question is 42."])

    preg = _build_registry(
        powerful_provider=powerful_provider,
        fast_provider=fast_provider,
    )
    # Open the powerful-tier breaker so the cascade skips it and picks fast-backup.
    _open_breaker(preg, _POWERFUL_NAME)

    services = _build_services(preg)
    backend = AsyncioBackend(services=services)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        delivered = await _execute_turn(
            "what is the answer?",
            "sess-circuit-routing-fr1",
            "trace-circuit-routing-1",
            backend,
        )

    # =========================================================================
    # FR1 — backup answer delivered.
    # =========================================================================
    assert "42" in delivered, (
        f"FR1 FAIL: backup answer ('42') not found in delivered text. "
        f"Got: {delivered!r}"
    )

    # =========================================================================
    # FR3 — GENERIC backup line present; NO provider name leak.
    # =========================================================================
    assert "ℹ️" in delivered, (
        f"FR3 FAIL: ℹ️ generic recovery marker absent from delivered text. "
        f"Got: {delivered!r}"
    )
    # The English localized string: "ℹ️ The usual model was unavailable, so a backup completed this."
    assert "backup" in delivered.lower(), (
        f"FR3 FAIL: 'backup' keyword absent from recovery line. "
        f"Got: {delivered!r}"
    )
    assert _POWERFUL_NAME not in delivered, (
        f"FR3 LEAK: opened provider name '{_POWERFUL_NAME}' leaked into user-visible "
        f"text. The recovery line must be generic (no provider names). "
        f"Got: {delivered!r}"
    )
    assert _FAST_NAME not in delivered, (
        f"FR3 LEAK: backup provider name '{_FAST_NAME}' leaked into user-visible "
        f"text. The recovery line must be generic (no provider names). "
        f"Got: {delivered!r}"
    )

    # =========================================================================
    # FR6 — [recovery] turn summary log has real provider names in events.
    # =========================================================================
    recovery_records = [
        r for r in caplog.records if "[recovery] turn summary" in r.getMessage()
    ]
    assert recovery_records, (
        f"FR6 FAIL: '[recovery] turn summary' log record not found. "
        f"Records: {[r.getMessage() for r in caplog.records]}"
    )
    rec = recovery_records[0]
    fields: dict[str, Any] = getattr(rec, "_fields", {})
    events: list[dict[str, Any]] = fields.get("events", [])
    assert events, (
        f"FR6 FAIL: 'events' missing or empty in turn summary _fields. "
        f"Got _fields: {fields!r}"
    )
    provider_fallback_events = [e for e in events if e.get("kind") == "provider_fallback"]
    assert provider_fallback_events, (
        f"FR6 FAIL: no provider_fallback event in events. Got: {events!r}"
    )
    ev = provider_fallback_events[0]
    assert ev.get("failed") == _POWERFUL_NAME, (
        f"FR6 FAIL: event 'failed' should be '{_POWERFUL_NAME}', got {ev.get('failed')!r}. "
        f"Full event: {ev!r}"
    )
    assert ev.get("recovered_via") == _FAST_NAME, (
        f"FR6 FAIL: event 'recovered_via' should be '{_FAST_NAME}', "
        f"got {ev.get('recovered_via')!r}. Full event: {ev!r}"
    )


# =========================================================================== #
# FR2 — all healthy, no backup line.
# =========================================================================== #


@pytest.mark.asyncio
async def test_no_recovery_line_when_all_healthy(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR2: When all providers are healthy (no circuit opened), the powerful-tier
    provider answers directly and NO backup/recovery line is appended.

    Asserts:
      - The answer is delivered (sanity).
      - The ℹ️ generic marker is absent.
      - The "backup" keyword is absent.
      - No ``provider_fallback`` recovery was recorded.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # No breaker opened — powerful tier is healthy; it answers directly.
    powerful_provider = _ScriptedProvider(_POWERFUL_NAME, ["The capital of France is Paris."])
    # fast_provider is registered but should not be reached.
    fast_provider = _ScriptedProvider(_FAST_NAME, ["SHOULD NOT BE REACHED"])

    preg = _build_registry(
        powerful_provider=powerful_provider,
        fast_provider=fast_provider,
    )

    services = _build_services(preg)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "what is the capital of France?",
        "sess-circuit-routing-fr2",
        "trace-circuit-routing-2",
        backend,
    )

    # =========================================================================
    # FR2 OUTCOME 1 — answer delivered (sanity).
    # =========================================================================
    assert "Paris" in delivered, (
        f"FR2 FAIL: expected answer ('Paris') missing from delivered text. "
        f"Got: {delivered!r}"
    )

    # =========================================================================
    # FR2 OUTCOME 2 — no recovery line (no fallback occurred).
    # =========================================================================
    assert "ℹ️" not in delivered, (
        f"FR2 HONESTY FAIL: ℹ️ marker appeared even though no circuit was open. "
        f"Got: {delivered!r}"
    )
    assert "backup" not in delivered.lower(), (
        f"FR2 HONESTY FAIL: 'backup' keyword appeared with no fallback. "
        f"Got: {delivered!r}"
    )
    assert "unavailable" not in delivered.lower(), (
        f"FR2 HONESTY FAIL: 'unavailable' keyword appeared with no fallback. "
        f"Got: {delivered!r}"
    )


# =========================================================================== #
# FR5 — all circuits open → floor + NO recovery line.
# =========================================================================== #


@pytest.mark.asyncio
async def test_all_open_floors_without_recovery_line(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR5 (honesty guard): When ALL registered providers have OPEN circuits,
    ``_select_tool_provider`` raises ``AllProvidersUnavailableError``, the execute
    step records an error (no real answer produced), and ``surface_recovery``
    is a no-op because no ``provider_fallback`` event was recorded (selection
    itself failed before any successful fallback could be found).

    Strategy: open ALL four registered provider slots. The critical_failure
    surface then tries to generate a localized apology via get_with_cascade("fast")
    which also fails (all OPEN), so the neutral floor marker ("⚠ [...]") is injected.

    CRITICAL HONESTY GATE: if the backup line ("ℹ️" / the provider template) appears
    on a fully floored turn, surface_recovery's event-guard is broken — BLOCKED.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Neither provider should be called (circuits all OPEN).
    powerful_provider = _ScriptedProvider(_POWERFUL_NAME, ["SHOULD NOT BE REACHED"])
    fast_provider = _ScriptedProvider(_FAST_NAME, ["SHOULD NOT BE REACHED"])

    preg = _build_registry(
        powerful_provider=powerful_provider,
        fast_provider=fast_provider,
    )
    # Open ALL registered provider slots so every tier is unavailable.
    for slot in (_POWERFUL_NAME, _FAST_NAME, "router", "local-judge"):
        _open_breaker(preg, slot)

    services = _build_services(preg)
    backend = AsyncioBackend(services=services)

    delivered = await _execute_turn(
        "what is 2 + 2?",
        "sess-circuit-routing-fr5",
        "trace-circuit-routing-5",
        backend,
    )

    # =========================================================================
    # FR5 HONESTY GATE — recovery line MUST NOT appear on a floored turn.
    # AllProvidersUnavailableError fires BEFORE any record_recovery call (recovery
    # is only recorded on a SUCCESSFUL fallback, which never happened here).
    # If "ℹ️" appears, surface_recovery's event guard is broken — BLOCKED.
    # =========================================================================
    assert "ℹ️" not in delivered, (
        f"FR5 HONESTY DEFECT: ℹ️ recovery marker appeared on a fully floored "
        f"turn (all providers OPEN → AllProvidersUnavailableError). "
        f"The recovery line must only annotate real (non-floor) answers. "
        f"Got: {delivered!r}"
    )
    assert "The usual model was unavailable, so a backup completed this" not in delivered, (
        f"FR5 HONESTY DEFECT: full recovery template text appeared on a floored turn. "
        f"Got: {delivered!r}"
    )

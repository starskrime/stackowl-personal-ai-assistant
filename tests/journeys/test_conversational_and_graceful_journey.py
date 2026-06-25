"""T3 — intent-classification-hardening regressions (FR1 + FR6).

Two end-to-end journeys that prove the T1 + T2 changes reach the user:

JOURNEY 1 — conversational bypass via scan-all-lines parse (FR1)
  The router double returns ``"secretary\\n\\nconversational"`` — the intent
  token is on LINE 3 (not line 2), which is the edge-case that triggered the
  live bug and is exactly what T1's ``_parse_intent_class`` now handles by
  scanning ALL lines after the first.  The turn must:
    * deliver a non-empty reply (plain-stream path), AND
    * NEVER enter the tool loop (no ``"tool_loop entry"`` log record).
  Mirrors ``test_conversational_bypass_journey.py`` exactly; the only
  difference is the router reply shape (line-3 token instead of line-2).

JOURNEY 2 — graceful-timeout floor for a bare default-backstop BudgetBreach
  with an empty partial (FR6 / T2).  A scripted provider emits NO assistant
  text before the BudgetBreach fires.  With no explicit owl caps the default
  backstop activates; the breach partial_text is ``""`` so the execute step
  routes to ``synthesize_floor(goal, error=None, attempts=[], partial=None)``
  which returns ``localize("self_heal_floor_graceful", "en")``.  The user
  OUTCOME:
    * delivered text equals ``localize("self_heal_floor_graceful", "en")``,
    * delivered text does NOT contain ``"budget cap reached"`` or
      ``"capability that failed"``,
    * ``state.errors`` contains a ``budget:stop`` marker (observability).

Boot mirrors ``test_conversational_bypass_journey.py`` (JOURNEY 1) and
``tests/pipeline/test_default_backstop_no_marker.py`` (JOURNEY 2) — both
well-established harness shapes.
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
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.react_callback import ReActIterationState
from stackowl.providers.registry import ProviderRegistry
from stackowl.setup.localize import localize
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# _ScriptedProvider — identical to test_conversational_bypass_journey.py.
# Supports complete() (router/judge calls) and stream() (answer delivery).
# ---------------------------------------------------------------------------


class _ScriptedProvider(ModelProvider):
    """Returns canned responses in sequence; directly implements ModelProvider."""

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


# ---------------------------------------------------------------------------
# LINE-3 router double — "secretary\n\nconversational".
# The blank line 2 forces _parse_intent_class to scan past line 2 to find the
# class token on line 3. This is the exact parse path T1 hardened.
# On persistence-judge calls (prompt contains "AGENT DRAFT REPLY") returns
# {"delivered": true, ...} so the judge is fail-open (does not block the turn).
# ---------------------------------------------------------------------------


class _Line3ConversationalRouterProvider(ModelProvider):
    """Fast-tier router: token on LINE 3 (blank line 2) — exercises T1's scan."""

    @property
    def name(self) -> str:
        return "line3-conv-router-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        # Persistence-judge calls carry "AGENT DRAFT REPLY" — return delivered.
        if "AGENT DRAFT REPLY" in joined:
            content = '{"delivered": true, "reason": "looks complete"}'
        else:
            # Owl name on line 1, BLANK line 2, class token on line 3.
            content = "secretary\n\nconversational"
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="line3-conv-router-fake",
            provider_name="line3-conv-router-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield "secretary\n\nconversational"


# ---------------------------------------------------------------------------
# Service builder for JOURNEY 1 (mirrors _build_services_conversational from
# test_conversational_bypass_journey.py, with the line-3 router instead).
# ---------------------------------------------------------------------------


def _build_services_line3_conversational(
    answer_provider: _ScriptedProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    """StepServices with line-3 conversational router on fast tier."""
    preg = ProviderRegistry()
    preg.register_mock("secretary", answer_provider, tier="powerful")
    preg.register_mock("powerful", answer_provider, tier="powerful")
    router = _Line3ConversationalRouterProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    preg.register_mock("standard-judge", router, tier="standard")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )


# ---------------------------------------------------------------------------
# Shared turn-runner (mirrors _execute_turn from the existing bypass journey).
# ---------------------------------------------------------------------------


async def _execute_turn(
    text: str,
    session: str,
    trace: str,
    backend: AsyncioBackend,
) -> tuple[str, PipelineState]:
    """Run one turn through the backend; return (delivered_text, final_state)."""
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
    delivered = "".join(c.content for c in final_state.responses)
    return delivered, final_state


# ---------------------------------------------------------------------------
# JOURNEY 2 — scripted provider that emits NO partial text before BudgetBreach.
# Mirrors _LoopProvider from test_default_backstop_no_marker.py with
# emit_partial=False, but inlined here to keep the file self-contained.
# ---------------------------------------------------------------------------

_DEFAULT_BACKSTOP_ITERATIONS = 21  # one past DEFAULT_TURN_MAX_STEPS (20)


class _EmptyPartialProvider:
    """Multi-iteration provider with NO partial text — BudgetBreach with empty partial."""

    protocol = "anthropic"

    def __init__(self, iterations: int = _DEFAULT_BACKSTOP_ITERATIONS) -> None:
        self._iterations = iterations
        self.completed_iterations: list[int] = []

    async def complete_with_tools(
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[dict[str, object]],
        tool_dispatcher: Any,
        history: list[Any] | None = None,
        on_iteration_complete: Any = None,
        **_kwargs: object,
    ) -> tuple[str, list[dict[str, Any]]]:
        all_messages: list[dict[str, Any]] = []
        all_calls: list[dict[str, Any]] = []
        for i in range(self._iterations):
            # Deliberately emit NO assistant text — partial_text will be "" at
            # breach time, forcing the empty-partial branch of execute.py.
            if on_iteration_complete is not None:
                await on_iteration_complete(
                    ReActIterationState(
                        iteration=i,
                        messages=list(all_messages),
                        tool_call_records=list(all_calls),
                    )
                )
            self.completed_iterations.append(i)
        return ("done", all_calls)


class _MinimalReadTool(Tool):
    """A read-severity tool — present so execute takes the tool-loop path."""

    @property
    def name(self) -> str:
        return "graceful_probe_tool"

    @property
    def description(self) -> str:
        return "Minimal read probe for the graceful-floor test."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="graceful_probe_tool",
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="probe-ok", duration_ms=1.0)


def _make_graceful_services(owl_name: str) -> StepServices:
    """Wire StepServices for a no-caps owl (default backstop activates)."""
    from stackowl.owls.manifest import OwlAgentManifest

    tool = _MinimalReadTool()
    tool_registry = ToolRegistry()
    tool_registry.register(tool)

    owl_registry = OwlRegistry()
    # owl_name must be <= 16 chars (ManifestValidationError otherwise).
    owl_registry.register(OwlAgentManifest(
        name=owl_name,
        role="tester",
        system_prompt="Test graceful floor.",
        model_tier="fast",
        bounds=None,  # no explicit caps → default backstop
    ))

    return StepServices(
        tool_registry=tool_registry,
        owl_registry=owl_registry,
        cost_tracker=None,
        clarify_gateway=None,
    )  # type: ignore[arg-type]


# ===========================================================================
# JOURNEY 1 — conversational bypass with token on LINE 3 (T1 scan-all-lines).
# ===========================================================================


@pytest.mark.asyncio
async def test_conversational_line3_token_bypasses_tool_loop(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR1: router reply ``"secretary\\n\\nconversational"`` (token on line 3)
    must reach the conversational bypass end-to-end via T1's scan-all-lines
    ``_parse_intent_class``.

    Outcome asserted:
      * A non-empty reply is delivered (plain-stream path worked).
      * The tool loop was NEVER entered (no ``"tool_loop entry"`` log record) —
        the primary load-bearing proof that the bypass fired.
      * The budget log shows ``intent_class == "conversational"`` and
        ``tools_used == False``.
      * ``state.errors`` is empty (no floor-on-error masquerade).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    greeting = "Hey there! What can I do for you?"
    answer_provider = _ScriptedProvider("answer-line3-conv", [greeting])
    owl_registry = OwlRegistry.with_default_secretary()
    tool_registry = ToolRegistry()  # empty — no tools registered for conv path

    services = _build_services_line3_conversational(
        answer_provider, owl_registry, tool_registry
    )
    backend = AsyncioBackend(services=services)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        delivered, final_state = await _execute_turn(
            "hey",
            "sess-line3-conv-t1",
            "trace-line3-conv-1",
            backend,
        )

    # ===================================================================
    # No-error guard — a floor-on-error reply must not silently pass the
    # tool-loop assertion (a floor reply is also non-empty + no tool_loop log).
    # ===================================================================
    assert not final_state.errors, (
        f"JOURNEY-1 FAIL: unexpected errors — a floor-on-error reply would produce "
        f"a false-positive on the tool-loop absence check. errors={final_state.errors}"
    )

    # ===================================================================
    # OUTCOME 1 — a non-empty reply was delivered via the plain-stream path.
    # ===================================================================
    assert delivered.strip(), (
        f"JOURNEY-1 FAIL: delivered text is empty — the conversational turn "
        f"produced no reply. Got: {delivered!r}"
    )
    assert greeting in delivered, (
        f"JOURNEY-1 FAIL: expected greeting {greeting!r} in delivered text. "
        f"Got: {delivered!r}"
    )

    # ===================================================================
    # OUTCOME 2 — the tool loop was NEVER entered (the load-bearing assertion).
    #
    # ``"tool_loop entry"`` is logged ONLY when _run_with_tools is called
    # (execute.py:468). If the conversational bypass fired, this log never
    # appears. If _parse_intent_class did NOT scan line 3, intent_class stays
    # "standard" and the loop IS entered — so this assertion fails, proving
    # the T1 fix is live end-to-end.
    # ===================================================================
    assert not any("tool_loop entry" in r.getMessage() for r in caplog.records), (
        f"JOURNEY-1 FAIL: 'tool_loop entry' log found — the tool loop was entered "
        f"for a line-3 conversational token, proving _parse_intent_class did NOT "
        f"scan past line 2 (T1 fix not live). "
        f"Records: {[r.getMessage() for r in caplog.records if 'tool_loop' in r.getMessage()]}"
    )

    # ===================================================================
    # OUTCOME 3 — budget log corroborates (belt-and-braces).
    # ===================================================================
    budget_records = [
        r for r in caplog.records
        if "[pipeline] execute: context budget" in r.getMessage()
    ]
    assert budget_records, (
        f"JOURNEY-1 FAIL: no '[pipeline] execute: context budget' log record. "
        f"Records: {[r.getMessage() for r in caplog.records]}"
    )
    budget_fields: dict[str, Any] = getattr(budget_records[0], "_fields", {})
    assert budget_fields.get("intent_class") == "conversational", (
        f"JOURNEY-1 FAIL: intent_class in budget log is not 'conversational'. "
        f"Got _fields: {budget_fields!r}"
    )
    assert budget_fields.get("tools_used") is False, (
        f"JOURNEY-1 FAIL: tools_used in budget log is not False. "
        f"Got _fields: {budget_fields!r}"
    )


# ===========================================================================
# JOURNEY 2 — graceful floor for a default-backstop BudgetBreach + empty partial.
# ===========================================================================


@pytest.mark.asyncio
async def test_graceful_floor_on_bare_timeout_breach() -> None:
    """FR6 / T2: default-backstop BudgetBreach with empty partial_text delivers
    ``localize("self_heal_floor_graceful", "en")`` — warm, honest, slot-free.

    Mechanism driven through the REAL ``_run_with_tools`` seam (mirrors
    ``test_default_backstop_no_marker.py`` / Test 2).  The scripted provider
    emits NO assistant text before the breach, so ``exc.partial_text == ""``.
    The default-backstop branch in execute.py routes to ``synthesize_floor``
    with ``attempts=[], partial=None``, which returns the graceful string
    (T2's no-capability-data branch).

    Three user-outcome assertions + one observability assertion:
      * Delivered text equals the graceful string exactly.
      * Delivered text does NOT contain ``"budget cap reached"`` (the raw
        error the old code leaked).
      * Delivered text does NOT contain ``"capability that failed"`` (the
        self_heal_floor template slot that would appear if synthesize_floor
        took the wrong branch).
      * ``state.errors`` contains a ``"budget:stop"`` marker (internal
        observability is preserved; the marker is NOT in the response).
    """
    owl_name = "graceful_owl"
    provider = _EmptyPartialProvider(iterations=_DEFAULT_BACKSTOP_ITERATIONS)
    services = _make_graceful_services(owl_name)

    state = PipelineState(
        trace_id="trace-graceful-floor-1",
        session_id="sess-graceful-floor-1",
        input_text="do something complicated",
        channel="cli",
        owl_name=owl_name,
        pipeline_step="execute",
        interactive=False,
    )

    token = set_services(services)
    try:
        result = await _run_with_tools(state, provider, services.tool_registry)  # type: ignore[arg-type]
    finally:
        reset_services(token)

    # ===================================================================
    # The default backstop must have fired (loop did not complete all
    # iterations — the governor stopped it early).
    # ===================================================================
    assert len(provider.completed_iterations) < _DEFAULT_BACKSTOP_ITERATIONS, (
        f"JOURNEY-2 FAIL: default backstop did NOT fire — all "
        f"{_DEFAULT_BACKSTOP_ITERATIONS} iterations completed. "
        f"completed_iterations={provider.completed_iterations}"
    )

    # ===================================================================
    # At least one response chunk must be present (never-empty invariant).
    # ===================================================================
    assert result.responses, (
        "JOURNEY-2 FAIL: no ResponseChunk delivered — never-empty invariant violated."
    )
    all_content = "".join(c.content for c in result.responses)
    assert all_content.strip(), (
        f"JOURNEY-2 FAIL: all delivered content is blank. Got: {all_content!r}"
    )

    # ===================================================================
    # OUTCOME 1 — delivered text IS the graceful string (T2 in action).
    # ===================================================================
    expected_graceful = localize("self_heal_floor_graceful", "en")
    assert all_content == expected_graceful, (
        f"JOURNEY-2 FAIL: delivered text is not the graceful floor string.\n"
        f"  Expected: {expected_graceful!r}\n"
        f"  Got:      {all_content!r}"
    )

    # ===================================================================
    # OUTCOME 2 — "budget cap reached" must NOT appear in delivered text.
    # This was the T2 bug: synthesize_floor received the raw BudgetBreach
    # message ("budget cap reached") as the ``error`` slot and it leaked
    # into the user-visible string. T2 routes the no-data case to the
    # graceful slot-free message instead.
    # ===================================================================
    assert "budget cap reached" not in all_content.lower(), (
        f"JOURNEY-2 FAIL: 'budget cap reached' leaked into delivered text (T2 bug "
        f"regression). Got: {all_content!r}"
    )

    # ===================================================================
    # OUTCOME 3 — "capability that failed" must NOT appear in delivered text.
    # This would appear if synthesize_floor took the self_heal_floor template
    # branch (with empty slots) instead of the graceful branch.
    # ===================================================================
    assert "capability that failed" not in all_content.lower(), (
        f"JOURNEY-2 FAIL: 'capability that failed' appeared in delivered text — "
        f"synthesize_floor took the wrong branch (slot template, not graceful). "
        f"Got: {all_content!r}"
    )

    # ===================================================================
    # OBSERVABILITY — the budget:stop marker IS in state.errors (the
    # developer/monitoring path is preserved; the marker is NOT in content).
    # ===================================================================
    assert any("budget:stop" in e for e in result.errors), (
        f"JOURNEY-2 FAIL: 'budget:stop' marker missing from state.errors — "
        f"observability path broken. errors={result.errors}"
    )

"""Cross-turn journey — stale apology prose is NOT amplified across turns.

Story #4 — When a weak model returns the same apologetic response 3 turns in a
row, persist → _gather_history re-feeds those turns.  Without the dedup gate
the identical assistant text appears 2× (or 3×) in turn-3's handed context —
the weak model then amplifies the pattern further.

The dedup applied in _gather_history collapses repeated identical assistant
content to its most-recent occurrence, so the apology appears at most once in
the re-fed context regardless of how many turns it repeated.

Assertion anchor: ``provider.calls[-1]`` — the history messages list handed to
the provider on the last (3rd) turn — must contain the APOLOGY string at most
once.

Harness reuses _ScriptedProvider / _execute_turn from
test_conversational_bypass_journey.py and wires a real SqliteMemoryBridge so
history IS persisted between turns and re-fed on subsequent turns.

Teeth check (Step 6 in the plan, recorded in the commit body):
  Temporarily removing the ``_dedup_assistant_history`` wrap from
  ``_gather_history`` causes this assertion to FAIL with n=2 (two copies of the
  apology in turn-3's context).  Restoring the wrap → n≤1 → PASS.
"""
from __future__ import annotations

import uuid

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

from tests.journeys.test_conversational_bypass_journey import (
    _EchoTool,
    _ScriptedProvider,
    _StandardRouterProvider,
    _execute_turn,
)

APOLOGY = "Sorry about that — I've fixed it. Anything else?"


@pytest.mark.asyncio
async def test_apology_not_amplified_across_turns(
    tmp_db: DbPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive 3 turns on the same session; all 3 scripted replies are the same
    apology string.  On turn 3 the re-fed history must contain the apology
    at most once (the dedup gate collapses earlier repeats).

    Step 6 teeth-check (recorded in the commit body):
      - Temporarily removing the _dedup_assistant_history wrap from
        _gather_history causes this assertion to fail (n=2) because the window
        holds 2 persisted turns each with the same assistant apology.
      - Restoring the wrap brings it back to n<=1 (PASS).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # 3 scripted replies — all the same apology.
    answer_provider = _ScriptedProvider("answer-std", [APOLOGY, APOLOGY, APOLOGY])
    owl_registry = OwlRegistry.with_default_secretary()

    echo_tool = _EchoTool()
    tool_registry = ToolRegistry()
    tool_registry.register(echo_tool)
    gate = ConsequentialActionGate(confirm_fn=lambda _name: True)

    # Wire a real SqliteMemoryBridge so turns are persisted between calls and
    # re-fed as history on subsequent turns — this is the actual amplification path.
    bridge = SqliteMemoryBridge(db=tmp_db)

    preg = ProviderRegistry()
    preg.register_mock("secretary", answer_provider, tier="powerful")
    preg.register_mock("powerful", answer_provider, tier="powerful")
    router = _StandardRouterProvider()
    preg.register_mock("router", router, tier="fast")
    preg.register_mock("local-judge", router, tier="local")
    preg.register_mock("standard-judge", router, tier="standard")

    services = StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
        consent_gate=gate,
        memory_bridge=bridge,
    )
    backend = AsyncioBackend(services=services)

    session_id = f"sess-apology-dedup-{uuid.uuid4().hex[:8]}"

    for i, text in enumerate(["please rename the file", "and the other one", "so what?"], 1):
        trace = f"trace-apology-dedup-{i}"
        await _execute_turn(text, session_id, trace, backend)

    # Turn 3's call to complete_with_tools captured the history it was handed.
    # (complete_with_tools now appends to self.calls — fixture gap closed in Step 5)
    assert answer_provider.calls, (
        "provider.calls is empty — complete_with_tools never captured history; "
        "check that _ScriptedProvider.complete_with_tools appends to self.calls"
    )
    turn3_history = answer_provider.calls[-1]

    n = sum(
        1
        for m in turn3_history
        if getattr(m, "role", None) == "assistant" and APOLOGY in getattr(m, "content", "")
    )
    assert n <= 1, (
        f"apology amplified into turn-3 context {n} times "
        f"(expected <= 1 after dedup). "
        f"Turn-3 history roles+content: "
        f"{[(getattr(m,'role','?'), getattr(m,'content','?')[:40]) for m in turn3_history]}"
    )

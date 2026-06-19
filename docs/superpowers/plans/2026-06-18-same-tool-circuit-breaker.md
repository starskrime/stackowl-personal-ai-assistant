# Same-Tool Repeated-Failure Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop offering a tool for the rest of a turn after it fails N consecutive times, so a weak model can't burn the whole budget spiraling on the same broken tool (incident P2: 9 failing `shell` calls to the 120s wall).

**Architecture:** Add a per-turn `fail_streak: dict[str,int]` + `circuit_open: set[str]` closed over inside `_run_with_tools` (alongside the existing `denied_this_run`/`substituted_tags` per-turn sets). Increment the streak at the existing `record_tool_outcome` site on a genuine execution failure; reset it on success; at `THRESHOLD` add the tool name to `circuit_open`. Bounce any call to a circuit-open tool at the **top of `_dispatch`** (mirroring the `denied_this_run` short-circuit) with a stable, model-readable refusal string — a **pre-execution refusal that records no effectful outcome**, so it cannot trip the P0 give-up floor. Enforcement is at the per-call dispatch seam because the provider runs the whole ReAct loop behind one `await` and the offered `tool_schemas` list is built once per turn and cannot be pruned mid-loop.

**Tech Stack:** Python 3.12, `uv`, pytest. Files under `src/stackowl/pipeline/steps/execute.py`; tests under `tests/pipeline/` and `tests/journeys/`.

## Global Constraints

- **Honesty invariant (load-bearing — must not regress P0):** the circuit-open bounce is a pre-execution refusal. It MUST NOT record an effectful failure outcome — mirror `denied_this_run` (records nothing) so `tool_outcome_ledger.consequential_tally()` is unaffected and the give-up floor still decides honestly. A tripped breaker is containment, not a consequential failure.
- **Key by tool NAME only (v1).** `shell`/`execute_code` have no `capability_tag`; name is the only key that catches the incident. Group-by-capability is an explicit follow-up, NOT in v1 (YAGNI).
- **THRESHOLD = 3** consecutive same-tool genuine failures. A named module constant — never a magic number, never tuned to a specific model/host (per `feedback_never_pull_models_local_jetson` / `feedback_all_hardware`: host-agnostic fixed N).
- **Increment predicate (§7.3 resolved):** count a failure toward the streak iff `tr.success is False AND tr.side_effect_committed is True` — a *genuine execution failure*. This excludes pre-execution refusals (bad/missing args, unavailable store, consent-deny) which set `side_effect_committed=False`. It is **severity-agnostic** (does NOT gate on `action_severity in {write,consequential}` the way `is_effectful_failure` does): the incident tools (`shell`=write, `execute_code`=consequential) are covered identically, AND a read-only tool that keeps failing is also contained — at no cost. Rationale recorded in the design's §7.3.
- **A success resets the streak** (the breaker is *consecutive*, not cumulative).
- **Scoped to the tool:** failures of tool X never affect tool Y's streak/circuit.
- **No hardcoded English keyword lists.** The refusal string is a fixed glue string (English is acceptable for dispatch markers, like the existing `denied_this_run`/depth-cap strings) but carries NO case-specifics (no "pictures", no command text).
- **4-point logging on trip;** never a silent catch (CLAUDE.md observability rules). Use `log.engine.*` with `_fields` as the surrounding code does.
- **NOT prefixed with `TOOL_FAILED_MARKER`.** The marker is what the give-up judge counts as a failed action; a bounce is not a failure, so it must not carry the marker (mirrors `denied_this_run`, which also omits it).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/stackowl/pipeline/steps/execute.py` | Module constant `SAME_TOOL_FAILURE_THRESHOLD`; helper `_circuit_open_refusal(name)`; in `_run_with_tools`: the `fail_streak`/`circuit_open` per-turn state, the bounce at the top of `_dispatch`, the streak update at the existing record site. |
| `tests/pipeline/test_circuit_breaker.py` | Unit tests: increments on genuine failure, resets on success, trips at N, scoped to tool, bounce returns refusal, bounce records NO effectful outcome (tally unaffected), pre-exec refusal does not increment. |
| `tests/journeys/test_circuit_breaker_journey.py` | Gateway journey: a tool scripted to fail repeatedly is dropped after N (never executed an (N+1)th time), the turn floors HONESTLY (no overclaim); falsification guards: fail-twice-then-succeed is never bounced and its success delivered; failures of X don't open the breaker for Y. |

We do **not** widen `tool_outcome_ledger.py` — its signature is the floor's single source of truth (per incident memory). The increment predicate is inlined in `_dispatch` with a clear comment.

---

### Task 1: Threshold constant + circuit-open refusal helper

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (module-level constant near the other tuning constants; a module-level `_circuit_open_refusal` helper near other dispatch-string helpers)
- Test: `tests/pipeline/test_circuit_breaker.py`

**Interfaces:**
- Produces:
  - `SAME_TOOL_FAILURE_THRESHOLD: int` (module constant, value `3`)
  - `_circuit_open_refusal(name: str) -> str` — a stable refusal string mentioning `name`, steering the model to switch approach or stop; contains NO `TOOL_FAILED_MARKER`.

- [ ] **Step 1: Write the failing test**

Add to a new file `tests/pipeline/test_circuit_breaker.py`:

```python
"""Unit tests for the same-tool repeated-failure circuit breaker (incident P2)."""

from __future__ import annotations

from stackowl.pipeline.steps.execute import (
    SAME_TOOL_FAILURE_THRESHOLD,
    _circuit_open_refusal,
)


def test_threshold_is_three() -> None:
    # Host-agnostic fixed N; one below LoopGuard's identical-args break_at=4.
    assert SAME_TOOL_FAILURE_THRESHOLD == 3


def test_circuit_open_refusal_mentions_tool_and_steers_to_stop() -> None:
    msg = _circuit_open_refusal("shell")
    assert "shell" in msg
    # Steers the model to change approach or stop — no case-specifics.
    lower = msg.lower()
    assert "different" in lower or "another" in lower or "stop" in lower


def test_circuit_open_refusal_is_not_a_tool_failure_marker() -> None:
    # A bounce is containment, not a tool failure: it must NOT carry the marker
    # the give-up judge counts as a failed action (mirrors denied_this_run).
    from stackowl.pipeline.steps.execute import TOOL_FAILED_MARKER

    assert TOOL_FAILED_MARKER not in _circuit_open_refusal("shell")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_circuit_breaker.py -v`
Expected: FAIL — `ImportError: cannot import name 'SAME_TOOL_FAILURE_THRESHOLD'` (and `_circuit_open_refusal`).

- [ ] **Step 3: Write minimal implementation**

In `src/stackowl/pipeline/steps/execute.py`, add the constant near the other module-level tuning constants (e.g. next to `_TOOL_DEADLINE_S` / `RESPONSE_RESERVE_TOKENS`):

```python
# Incident P2 — same-tool repeated-failure circuit breaker. After this many
# CONSECUTIVE genuine execution failures of the SAME tool within one turn, the
# tool is bounced for the rest of the turn so a weak model cannot spiral on it
# (the pictures-overclaim incident: 9 failing `shell` calls burned budget to the
# 120s wall). One below LoopGuard's identical-args break_at=4 because this
# breaker's scope is broader (any args, by tool name). Host-agnostic fixed N —
# never tuned to a model/box (see feedback_never_pull_models_local_jetson).
SAME_TOOL_FAILURE_THRESHOLD = 3
```

Add the helper near other module-level helpers (above `_run_with_tools`):

```python
def _circuit_open_refusal(name: str) -> str:
    """Stable, model-readable refusal for a tool whose same-tool failure breaker
    tripped this turn (incident P2). Steers the model to change approach or stop;
    carries NO case-specifics. NOT prefixed with TOOL_FAILED_MARKER — a bounce is
    containment, not a tool failure, so the give-up judge must not read it as a
    failed consequential action (mirrors the denied_this_run bounce)."""
    return (
        f"The action '{name}' has failed repeatedly this turn and is no longer "
        f"available. Do not call it again — try a different approach, or if no "
        f"alternative remains, stop and tell the user what you could not do."
    )
```

Verify `TOOL_FAILED_MARKER` is importable from this module (it is referenced inside `_dispatch` already — confirm it is a module-level import/constant, not a local). If it is imported locally inside the function, the test imports it from the module; in that case move the import to module level, or have the test import it from its true source. Grep first: `grep -n "TOOL_FAILED_MARKER" src/stackowl/pipeline/steps/execute.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_circuit_breaker.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_circuit_breaker.py
git commit -m "feat(p2): circuit-breaker threshold constant + refusal helper"
```

---

### Task 2: Per-turn streak tracking + trip + dispatch bounce

This is the core wiring. It is one task because the three edits (per-turn state, the bounce at the top of `_dispatch`, the streak update at the record site) are meaningless individually — a reviewer accepts or rejects them together.

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` — inside `_run_with_tools`
  - add `fail_streak`/`circuit_open` next to `denied_this_run`/`substituted_tags` (~execute.py:647-653)
  - add the bounce at the top of `_dispatch` (~execute.py:660, right after the `denied_this_run` block)
  - add the streak update right after the existing `record_tool_outcome(...)` call (~execute.py:884-890)
- Test: `tests/pipeline/test_circuit_breaker.py` (extend)

**Interfaces:**
- Consumes: `SAME_TOOL_FAILURE_THRESHOLD`, `_circuit_open_refusal` (Task 1); `tool_outcome_ledger` (already imported in execute.py).
- Produces: the observable behavior at the `_dispatch` seam — driven through the real `_run_with_tools` in the journey (Task 3). This unit task drives the seam through a minimal real-tool + scripted-provider harness (mirroring the P0 journey's `_drive`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/pipeline/test_circuit_breaker.py`. This harness mirrors `tests/journeys/test_budget_cap_overclaim_floor_journey.py` (`_ScriptedProvider` dispatches scripted calls through the REAL `tool_dispatcher`, i.e. `_dispatch`) but is unit-scoped: assert on the rendered dispatch results and the tool's call count.

```python
import pytest
from typing import Any

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _run_with_tools
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "breaker_owl"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _CountingTool(Tool):
    """A tool whose success/failure per call is scripted; counts its executions."""

    def __init__(self, name: str, results: list[bool], severity: str = "write") -> None:
        self._name = name
        self._results = results
        self._severity = severity
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity=self._severity,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        idx = self.calls
        self.calls += 1
        ok = self._results[idx] if idx < len(self._results) else self._results[-1]
        if ok:
            return ToolResult(success=True, output="ok", duration_ms=1.0)
        # Genuine execution failure: ran and failed, boundary crossed (default True).
        return ToolResult(success=False, output="", error="boom", duration_ms=1.0)


class _SeqProvider:
    """Dispatch a fixed sequence of (name, args) through the real _dispatch and
    record each rendered result for assertions."""

    protocol = "anthropic"

    def __init__(self, calls: list[tuple[str, dict[str, object]]]) -> None:
        self._calls = calls
        self.rendered: list[str] = []

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for name, args in self._calls:
            out = await tool_dispatcher(name, args)
            self.rendered.append(out)
            records.append({"name": name, "args": args, "result": out})
        return ("done", records)

    async def complete(self, messages, model, **kwargs):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _Reg:
    def __init__(self, p): self._p = p  # noqa: E704
    def get(self, name): return self._p  # noqa: E704
    def get_by_tier(self, tier): return self._p  # noqa: E704
    def get_with_cascade(self, t): return self._p  # noqa: E704


async def _run(tools: list[Tool], calls: list[tuple[str, dict[str, object]]]) -> _SeqProvider:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset(t.name for t in tools), caps=ResourceCaps(max_steps=50)),
    ))
    provider = _SeqProvider(calls)
    state = PipelineState(
        trace_id="t", session_id="s", input_text="x", channel="telegram",
        owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        return provider
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


async def test_trips_after_threshold_and_bounces_further_calls() -> None:
    # 3 failures, then a 4th attempt → bounced (tool NOT executed a 4th time).
    tool = _CountingTool("shell", results=[False, False, False, False])
    calls = [("shell", {"x": str(i)}) for i in range(4)]
    provider = await _run([tool], calls)
    # Only 3 real executions; the 4th was bounced at dispatch.
    assert tool.calls == 3, f"expected 3 executions, got {tool.calls}"
    # The 4th rendered result is the circuit-open refusal, not a tool result.
    assert "no longer available" in provider.rendered[3]
    assert "shell" in provider.rendered[3]


async def test_success_resets_streak() -> None:
    # fail, fail, success, fail → streak is 1 after the last → NOT open.
    tool = _CountingTool("shell", results=[False, False, True, False])
    calls = [("shell", {"x": str(i)}) for i in range(4)]
    provider = await _run([tool], calls)
    assert tool.calls == 4, "a success between failures must reset; all 4 run"
    assert "no longer available" not in provider.rendered[3]


async def test_breaker_scoped_to_tool() -> None:
    # shell fails 3x (opens), but a different tool 'http' still runs.
    shell = _CountingTool("shell", results=[False, False, False])
    http = _CountingTool("http", results=[True])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "2"}), ("shell", {"x": "3"}),
             ("http", {"x": "1"})]
    provider = await _run([shell, http], calls)
    assert shell.calls == 3
    assert http.calls == 1, "failures of shell must not open the breaker for http"
    assert "no longer available" not in provider.rendered[3]


async def test_bounce_records_no_effectful_failure() -> None:
    # The bounce must NOT increment the consequential failure tally (P0 honesty).
    tool = _CountingTool("shell", results=[False, False, False, False])
    calls = [("shell", {"x": str(i)}) for i in range(4)]
    # We need to inspect the ledger AFTER the run but BEFORE reset — drive inline.
    registry = ToolRegistry()
    registry.register(tool)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset({"shell"}), caps=ResourceCaps(max_steps=50)),
    ))
    provider = _SeqProvider(calls)
    state = PipelineState(
        trace_id="t", session_id="s", input_text="x", channel="telegram",
        owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        cons_f, cons_s = tool_outcome_ledger.consequential_tally()
        # 3 genuine write failures recorded; the bounce recorded NOTHING extra.
        assert cons_f == 3, f"expected exactly 3 recorded failures, got {cons_f}"
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_circuit_breaker.py -v`
Expected: the four new tests FAIL — `test_trips_after_threshold...` fails because `tool.calls == 4` (no bounce yet); `test_bounce_records_no_effectful_failure` fails because `cons_f == 4` (the 4th call ran and recorded a failure).

- [ ] **Step 3: Write the implementation**

In `_run_with_tools`, after the `substituted_tags: set[str] = set()` line (~execute.py:653), add:

```python
    # Incident P2 — same-tool repeated-failure circuit breaker. Per-turn, keyed by
    # tool NAME (v1: shell/execute_code have no capability_tag, so name is the only
    # key that catches the incident). A genuine execution failure increments the
    # streak; a success resets it; at SAME_TOOL_FAILURE_THRESHOLD the tool is added
    # to circuit_open and bounced at the top of _dispatch for the rest of the turn.
    # A pre-execution refusal/deny (side_effect_committed=False) is NOT counted.
    fail_streak: dict[str, int] = {}
    circuit_open: set[str] = set()
```

At the top of `_dispatch`, immediately after the `denied_this_run` block (after ~execute.py:668), add:

```python
        # Incident P2 — circuit-open bounce. A tool that failed
        # SAME_TOOL_FAILURE_THRESHOLD times in a row this turn is unavailable for
        # the rest of the turn. This is a PRE-EXECUTION REFUSAL (like
        # denied_this_run): it records NOTHING in the outcome ledger, so it cannot
        # trip the consequential give-up floor (P0 honesty invariant). Steer the
        # model to change approach or stop; the string carries no case-specifics.
        if name in circuit_open:
            log.engine.warning(
                "[pipeline] execute: circuit open — tool bounced for remainder of turn",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id,
                                   "threshold": SAME_TOOL_FAILURE_THRESHOLD}},
            )
            return _circuit_open_refusal(name)
```

Right after the existing `record_tool_outcome(...)` call (the block ending ~execute.py:890), add:

```python
        # Incident P2 — update the same-tool circuit-breaker streak from this REAL
        # tool run (a completed dispatch, not a pre-exec refusal/bounce). A success
        # resets; a GENUINE execution failure (ran and failed, side-effect boundary
        # crossed-or-maybe — i.e. NOT a validation refusal, which sets
        # side_effect_committed=False) advances toward the cutoff. Severity-agnostic
        # on purpose: the breaker contains spirals on ANY tool, not only write/
        # consequential ones (shell=write, execute_code=consequential are both
        # covered; a read-only tool that keeps failing is contained too).
        if tr.success:
            fail_streak[name] = 0
        elif tr.side_effect_committed:
            fail_streak[name] = fail_streak.get(name, 0) + 1
            if fail_streak[name] >= SAME_TOOL_FAILURE_THRESHOLD:
                circuit_open.add(name)
                log.engine.warning(
                    "[pipeline] execute: same-tool failure threshold reached — circuit open",
                    extra={"_fields": {"tool": name, "trace_id": state.trace_id,
                                       "streak": fail_streak[name],
                                       "threshold": SAME_TOOL_FAILURE_THRESHOLD}},
                )
```

Note placement: this update must run on the normal completed-call path (after `record_tool_outcome` at ~884). It is intentionally NOT added on the timeout early-return path (~877-883) — timeouts are already separately bounded by the per-tool deadline, and the incident was exit-1 completed failures. This is a documented v1 scope choice.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_circuit_breaker.py -v`
Expected: PASS (all 7: 3 from Task 1 + 4 new).

- [ ] **Step 5: Run the broader pipeline suite for regressions**

Run: `uv run pytest tests/pipeline/ -q`
Expected: no NEW failures vs. the pre-change baseline. (Known pre-existing unrelated failure noted in incident memory: `test_conversational_bypass_journey::test_standard_turn_enters_tool_loop` — confirm it is the ONLY pre-existing red and is identical on the merge-base before attributing anything to this change.)

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_circuit_breaker.py
git commit -m "feat(p2): same-tool circuit breaker — trip at N, bounce at dispatch, honesty-safe"
```

---

### Task 3: Gateway integration journey (mandatory)

Drives the REAL `_run_with_tools`/`_dispatch` path with the AI provider as the ONLY mock, asserting OUTCOMES: a repeatedly-failing tool is dropped after N and the turn floors honestly; falsification guards prove transient failure is not bounced and the breaker is per-tool.

**Files:**
- Create: `tests/journeys/test_circuit_breaker_journey.py`

**Interfaces:**
- Consumes: `_run_with_tools` (real), `surface_consequential_giveup_floor` (real pre-delivery floor step), the Task-2 behavior at `_dispatch`.

- [ ] **Step 1: Write the failing journey tests**

Create `tests/journeys/test_circuit_breaker_journey.py`. Reuse the P0 journey's harness shape (`_ScriptedProvider` + `_drive` running `_run_with_tools` then `surface_consequential_giveup_floor`). Crucially, the fake tool counts executions so we can assert it was NOT run an (N+1)th time, and the scripted provider stops issuing tool calls once it receives the circuit-open refusal (a real weak model would stop or switch — here we model "switch to a final answer").

```python
"""GATEWAY JOURNEY — the same-tool circuit breaker contains a spiral (incident P2).

Reproduces the pictures-overclaim spiral shape: a tool fails repeatedly. After
SAME_TOOL_FAILURE_THRESHOLD consecutive failures the tool is BOUNCED at dispatch
(not executed again), the budget is not burned to the wall, and a turn that
delivered nothing real floors HONESTLY (no overclaim). The AI provider is the
ONLY mock; the real _run_with_tools/_dispatch path runs.

Falsification guards: a tool that fails twice then SUCCEEDS is never bounced and
its success is delivered; failures of tool X do not open the breaker for tool Y.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.authz.bounds import BoundsSpec, ResourceCaps
from stackowl.config.test_mode import TestModeGuard
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import (
    SAME_TOOL_FAILURE_THRESHOLD,
    _run_with_tools,
)
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_OWL = "breaker_journey_owl"
_OVERCLAIM = "All set — your files are ready and will look great! 🎨"
_REFUSAL_MARK = "no longer available"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _ScriptedFailTool(Tool):
    """A tool whose per-call outcome is scripted; counts executions."""

    def __init__(self, name: str, results: list[bool], severity: str = "write") -> None:
        self._name = name
        self._results = results
        self._severity = severity
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity=self._severity,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        idx = self.calls
        self.calls += 1
        ok = self._results[idx] if idx < len(self._results) else self._results[-1]
        if ok:
            return ToolResult(success=True, output="ok", duration_ms=1.0)
        return ToolResult(success=False, output="", error="boom", duration_ms=1.0)


class _SpiralProvider:
    """Keep calling `spiral_tool` until a dispatch returns the circuit-open refusal,
    then emit a final partial (the would-be overclaim) and stop. Models a weak model
    that keeps retrying a broken tool until it is cut off."""

    protocol = "anthropic"

    def __init__(self, tool_name: str, partial: str, max_attempts: int = 10) -> None:
        self._tool = tool_name
        self._partial = partial
        self._max = max_attempts

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for i in range(self._max):
            out = await tool_dispatcher(self._tool, {"x": str(i)})
            records.append({"name": self._tool, "args": {"x": str(i)}, "result": out})
            if _REFUSAL_MARK in out:
                break  # bounced — the model stops retrying
        return (self._partial, records)

    async def complete(self, messages, model, **kwargs):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _SeqProvider:
    """Dispatch a fixed (name, args) sequence; record rendered results."""

    protocol = "anthropic"

    def __init__(self, calls: list[tuple[str, dict[str, object]]], partial: str) -> None:
        self._calls = calls
        self._partial = partial
        self.rendered: list[str] = []

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, on_iteration_complete=None, **_kwargs,
    ):
        records: list[dict[str, Any]] = []
        for name, args in self._calls:
            out = await tool_dispatcher(name, args)
            self.rendered.append(out)
            records.append({"name": name, "args": args, "result": out})
        return (self._partial, records)

    async def complete(self, messages, model, **kwargs):  # noqa: ANN201
        from stackowl.providers.base import CompletionResult
        return CompletionResult(
            content="x", input_tokens=1, output_tokens=1,
            model="m", provider_name=_OWL, duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover
        if False:  # noqa: SIM210
            yield ""


class _Reg:
    def __init__(self, p): self._p = p  # noqa: E704
    def get(self, name): return self._p  # noqa: E704
    def get_by_tier(self, tier): return self._p  # noqa: E704
    def get_with_cascade(self, t): return self._p  # noqa: E704


async def _drive(tools: list[Tool], provider: Any) -> PipelineState:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name=_OWL, role="t", system_prompt="t", model_tier="fast",
        bounds=BoundsSpec(tools=frozenset(t.name for t in tools), caps=ResourceCaps(max_steps=50)),
    ))
    state = PipelineState(
        trace_id="t", session_id="s", input_text="can you help me with pictures",
        channel="telegram", owl_name=_OWL, pipeline_step="execute", interactive=False,
    )
    token = set_services(StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=registry, owl_registry=owl_registry,
        consent_gate=ConsequentialActionGate(confirm_fn=lambda _n: True),
        stream_registry=StreamRegistry(), cost_tracker=None,
    ))
    ledger_token = tool_outcome_ledger.bind()
    recovery_token = recovery_context.bind()
    try:
        out = await _run_with_tools(state, provider, registry)  # type: ignore[arg-type]
        return await surface_consequential_giveup_floor(out)
    finally:
        recovery_context.reset(recovery_token)
        tool_outcome_ledger.reset(ledger_token)
        reset_services(token)


# ---------------------------------------------------------------------------
# THE INCIDENT — a spiraling tool is contained, the turn floors honestly.
# ---------------------------------------------------------------------------


async def test_spiral_is_contained_and_turn_floors_honestly() -> None:
    tool = _ScriptedFailTool("shell", results=[False])  # always fails
    provider = _SpiralProvider("shell", _OVERCLAIM, max_attempts=10)
    out = await _drive([tool], provider)

    # CONTAINED: the tool executed exactly THRESHOLD times, then was bounced —
    # NOT run a (THRESHOLD+1)th time, and nowhere near the 10 attempts the model
    # would otherwise have made (the incident's 9 shells).
    assert tool.calls == SAME_TOOL_FAILURE_THRESHOLD, (
        f"expected exactly {SAME_TOOL_FAILURE_THRESHOLD} executions, got {tool.calls}"
    )

    delivered = "".join(c.content for c in out.responses)
    # HONEST: the would-be overclaim did NOT ship; an honest floor did.
    assert "look great" not in delivered, f"OVERCLAIM SHIPPED: {delivered!r}"
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an honest is_floor chunk; got {out.responses!r}"
    )
    assert delivered.strip(), "floor must be non-empty"


# ---------------------------------------------------------------------------
# FALSIFICATION (a) — fail twice then succeed is NEVER bounced; success delivered.
# ---------------------------------------------------------------------------


async def test_transient_failure_then_success_is_not_bounced() -> None:
    tool = _ScriptedFailTool("shell", results=[False, False, True])
    provider = _SeqProvider(
        [("shell", {"x": "1"}), ("shell", {"x": "2"}), ("shell", {"x": "3"})],
        partial="Done.",
    )
    out = await _drive([tool], provider)
    assert tool.calls == 3, "fail-fail-success must run all three (never bounced)"
    assert _REFUSAL_MARK not in provider.rendered[2], "the 3rd call must not be bounced"
    assert "ok" in provider.rendered[2], "the successful 3rd call's output is delivered"
    # A succeeding final consequential/write outcome → no honest floor.
    delivered = "".join(c.content for c in out.responses)
    assert "Done." in delivered or not any(
        getattr(c, "is_floor", False) for c in out.responses
    )


# ---------------------------------------------------------------------------
# FALSIFICATION (b) — failures of X do not open the breaker for Y.
# ---------------------------------------------------------------------------


async def test_breaker_is_per_tool() -> None:
    shell = _ScriptedFailTool("shell", results=[False])
    http = _ScriptedFailTool("http", results=[True])
    calls = [("shell", {"x": "1"}), ("shell", {"x": "2"}), ("shell", {"x": "3"}),
             ("http", {"x": "1"})]
    provider = _SeqProvider(calls, partial="ok")
    await _drive([shell, http], provider)
    assert shell.calls == 3
    assert http.calls == 1, "http must run — shell's failures must not open its breaker"
    assert _REFUSAL_MARK not in provider.rendered[3]
```

- [ ] **Step 2: Run the journey to verify it fails (pre-implementation guard)**

If Task 2 is already implemented this should PASS. To confirm the journey actually exercises the breaker, temporarily assert the WRONG count to see it red, or run against the merge-base. Otherwise:

Run: `uv run pytest tests/journeys/test_circuit_breaker_journey.py -v`
Expected: PASS (3 tests). If `test_spiral_is_contained...` shows `tool.calls == 10`, the breaker is NOT wired — STOP and fix Task 2 (do not weaken the test).

- [ ] **Step 3: (no new impl)** — the journey validates Task 2. If it fails for a real reason, fix the implementation, not the test.

- [ ] **Step 4: Run the full journeys suite for regressions**

Run: `uv run pytest tests/journeys/ -q`
Expected: only the known pre-existing unrelated failure (`test_conversational_bypass_journey::test_standard_turn_enters_tool_loop`); no new reds.

- [ ] **Step 5: Commit**

```bash
git add tests/journeys/test_circuit_breaker_journey.py
git commit -m "test(p2): gateway journey — spiral contained, floors honestly, falsification guards"
```

---

### Task 4: Lint, type-check, whole-branch review, merge

**Files:** none (verification + merge).

- [ ] **Step 1: Lint + type-check the touched files**

Run:
```bash
uv run ruff check src/stackowl/pipeline/steps/execute.py tests/pipeline/test_circuit_breaker.py tests/journeys/test_circuit_breaker_journey.py
uv run mypy src/stackowl/pipeline/steps/execute.py
```
Expected: clean (fix any findings before proceeding).

- [ ] **Step 2: Full regression batch**

Run the relevant suites in batches (Jetson can't run unbounded — see incident memory):
```bash
uv run pytest tests/pipeline/ tests/journeys/ -q
```
Expected: green except the one documented pre-existing unrelated failure. Confirm that failure exists on `main` too before attributing it here.

- [ ] **Step 3: Opus whole-branch adversarial review**

Dispatch an opus review of the entire branch diff vs `main` with the explicit mandate: trace whether the bounce can (a) trip the give-up floor (it must not — records no effectful outcome), (b) manufacture an overclaim, (c) suppress a legitimate retry-after-transient-failure, (d) leak across tools. Fix all findings before merge.

- [ ] **Step 4: Merge to main + push (standing rule), keep the branch**

```bash
git checkout main
git merge --no-ff feat/p2-same-tool-circuit-breaker -m "merge: same-tool circuit breaker (incident P2 — contain the spiral)"
git push origin main
git checkout feat/p2-same-tool-circuit-breaker   # keep the branch
```

- [ ] **Step 5: Update incident memory**

Update `project_pictures_overclaim_incident.md`: P2 (or its circuit-breaker component) SHIPPED with commit hash; note the §7.3 decision (severity-agnostic genuine-execution-failure predicate), the v1 name-keying, threshold=3, and that timeout-path increment was a documented v1 cut.

---

## Self-Review

**1. Spec coverage** (design §1–§7):
- §2 (why existing guards don't cover it) — informs the name+failure keying (Task 2). ✓
- §3 (enforce at `_dispatch`, not schema pruning) — bounce at top of `_dispatch` (Task 2). ✓
- §4.1 (per-turn streak/circuit, key by name, update at record site, exclude pre-exec refusals) — Task 2. ✓
- §4.2 (bounce returns a refusal string) — Task 1 helper + Task 2 bounce. ✓
- §4.3 (honesty: bounce records no effectful outcome; turn still floors honestly) — Task 2 `test_bounce_records_no_effectful_failure` + Task 3 journey. ✓
- §4.4 (THRESHOLD=3 named constant, host-agnostic) — Task 1. ✓
- §6 falsification guards (trips / resets / scoped / honesty / transient) — Tasks 2+3. ✓
- §7 open decisions (name-only v1; refusal wording; predicate = genuine-execution-failure) — resolved in Global Constraints. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**3. Type consistency:** `SAME_TOOL_FAILURE_THRESHOLD: int`, `_circuit_open_refusal(name: str) -> str`, `fail_streak: dict[str,int]`, `circuit_open: set[str]` used consistently across Tasks 1–3. Refusal marker string `"no longer available"` matches between helper, unit tests, and journey. ✓

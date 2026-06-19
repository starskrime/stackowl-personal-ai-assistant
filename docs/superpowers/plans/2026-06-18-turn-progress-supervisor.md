# Turn Progress Supervisor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the pile of overlapping "model is stuck" point-detectors with one per-turn progress model that closes the proven gaps (G1 timeout-spiral, G2 refusal-spiral), wire it to one honest-floor path, add an overclaim delivery-gate, and make degradation capability-honest on weak models.

**Architecture:** A turn-scoped `TurnProgressTracker` lives in `_run_with_tools` and is updated at the `_dispatch` outcome sites; it subsumes the P2 circuit breaker's `fail_streak`/`circuit_open`. Its `no_progress_streak` counter is INDEPENDENT of `tool_outcome_ledger` (the floor's `side_effect_committed` semantics stay clean). A turn summary is stamped onto `state`; the honest floor and a new overclaim delivery-gate consume it. Thresholds scale with `model_window` (capability-honest, never box-pinned).

**Tech Stack:** Python 3.12, `uv`, pytest. Spec: `docs/superpowers/specs/2026-06-18-turn-progress-supervisor.md`.

## Global Constraints

- **Two counters, NEVER conflated:** `tool_outcome_ledger` (`side_effect_committed` → P0 floor) stays byte-clean. `TurnProgressTracker.no_progress_streak` (any zero-progress dispatch → containment) is separate; the floor never reads it, the tracker never writes the ledger. Name them so no one conflates them.
- **Circuit-open bounce stays a pre-execution refusal:** records nothing in the ledger, no `TOOL_FAILED_MARKER` (preserves the P2 honesty invariant).
- **No new overclaim path:** a no-progress turn that delivers nothing real FLOORS; the overclaim gate only ever REPLACES a response with the honest floor, never manufactures a claim.
- **Byte-identical on healthy paths:** a turn with any PROGRESS dispatch, or a 0-tool conversational/clarify turn, must be unchanged — no false floor, no false bounce, no false overclaim-block. `turn_made_progress` defaults to `True` so any path that never enters the tracker is never floored by it.
- **Host-agnostic:** a strong/normal window is byte-identical to today; only a lean window (`model_window <= LEAN_WINDOW_THRESHOLD=8192`) changes behavior, and only toward MORE honesty. Never pin a threshold to the box.
- **THRESHOLD a named constant** (`NO_PROGRESS_THRESHOLD = 3`), scaled by a function not a magic number. 4-point logging on every trip/bounce/floor/gate; never a silent catch.
- **Merge-gate journeys drive the REAL path** (`_run_with_tools` + the real post-execute floor/gate band), mocking ONLY the provider's scripted tool-call sequence; assert OUTCOMES (mechanical AND honest-message).

## File Structure

| File | Responsibility |
|---|---|
| `src/stackowl/pipeline/progress_tracker.py` | NEW — `TurnProgressTracker` (no_progress_streak + circuit_open + made_progress), `NO_PROGRESS_THRESHOLD`, `resolve_no_progress_threshold(model_window)`. |
| `src/stackowl/pipeline/steps/execute.py` | Replace `fail_streak`/`circuit_open` with the tracker; update at all 5 dispatch outcome sites incl. timeout (G1) + committed=False refusal (G2); stamp `state.turn_made_progress`/`state.no_progress_tools` at the end of `_run_with_tools`; size the threshold from `model_window`. |
| `src/stackowl/pipeline/state.py` | NEW fields `turn_made_progress: bool = True`, `no_progress_tools: tuple[str,...] = ()`. |
| `src/stackowl/pipeline/giveup_floor.py` | Add the no-progress floor trigger (Phase 1) consuming the state stamp. |
| `src/stackowl/pipeline/overclaim_gate.py` | NEW (Phase 2) — `surface_overclaim_gate(state)` structural delivery-gate + `overclaim.detected/cleared` events. |
| `src/stackowl/pipeline/backends/asyncio_backend.py`, `.../langgraph_backend.py` | Wire `surface_overclaim_gate` after the floor (Phase 2); add the overclaim outcome field (Phase 2). |
| `src/stackowl/memory/outcome_store.py` | Add an `overclaim` outcome field (Phase 2). |
| `tests/pipeline/`, `tests/journeys/` | Unit + property + gateway journeys per phase. |

---

# PHASE 1 — Unified tracker + close G1/G2 + no-progress floor

### Task 1: `TurnProgressTracker` + threshold resolver (unit)

**Files:** Create `src/stackowl/pipeline/progress_tracker.py`; Test `tests/pipeline/test_progress_tracker.py`.

**Interfaces — Produces:**
- `NO_PROGRESS_THRESHOLD: int = 3`
- `resolve_no_progress_threshold(model_window: int | None) -> int` — lean window → 2, else 3.
- `class TurnProgressTracker` with:
  - `__init__(self, threshold: int = NO_PROGRESS_THRESHOLD)`
  - `record_progress(self, name: str) -> None` — resets streak, marks made_progress.
  - `record_no_progress(self, name: str) -> bool` — increments; returns True iff the circuit JUST opened (streak reached threshold this call).
  - `is_open(self, name: str) -> bool`
  - `made_progress: bool` (property) — True iff any `record_progress` happened.
  - `opened_tools: tuple[str, ...]` (property) — names whose circuit opened, insertion order.

- [ ] **Step 1: Write failing tests**

```python
from stackowl.pipeline.progress_tracker import (
    NO_PROGRESS_THRESHOLD, TurnProgressTracker, resolve_no_progress_threshold,
)


def test_threshold_default_is_three():
    assert NO_PROGRESS_THRESHOLD == 3


def test_resolve_threshold_scales_with_window():
    assert resolve_no_progress_threshold(8192) == 2      # lean → contain faster
    assert resolve_no_progress_threshold(4096) == 2
    assert resolve_no_progress_threshold(16384) == 3     # normal → default
    assert resolve_no_progress_threshold(None) == 3      # unknown → safe default


def test_no_progress_trips_at_threshold_and_bounces():
    t = TurnProgressTracker(threshold=3)
    assert t.record_no_progress("shell") is False  # 1
    assert t.record_no_progress("shell") is False  # 2
    assert t.record_no_progress("shell") is True   # 3 → opens NOW
    assert t.is_open("shell") is True
    assert t.opened_tools == ("shell",)


def test_success_resets_streak():
    t = TurnProgressTracker(threshold=3)
    t.record_no_progress("shell"); t.record_no_progress("shell")
    t.record_progress("shell")                      # reset
    assert t.record_no_progress("shell") is False   # streak now 1
    assert t.is_open("shell") is False


def test_made_progress_flag():
    t = TurnProgressTracker(threshold=3)
    assert t.made_progress is False
    t.record_no_progress("shell")
    assert t.made_progress is False
    t.record_progress("http")
    assert t.made_progress is True


def test_scoped_per_tool():
    t = TurnProgressTracker(threshold=3)
    for _ in range(3):
        t.record_no_progress("shell")
    assert t.is_open("shell") is True
    assert t.is_open("http") is False
```

- [ ] **Step 2: Run → FAIL** (`uv run pytest tests/pipeline/test_progress_tracker.py -v` → ImportError).
- [ ] **Step 3: Implement** `progress_tracker.py`:

```python
"""TurnProgressTracker — the per-turn "is this turn advancing?" model.

Unifies the same-tool circuit breaker (P2) and closes the timeout (G1) and
no-op-refusal (G2) spiral gaps. INDEPENDENT of tool_outcome_ledger: this counter
drives CONTAINMENT (bounce a tool that makes no progress); the ledger's
side_effect_committed semantics drive the HONEST FLOOR. The two never read each
other (see the spec's honesty invariants). Turn-scoped; one per _run_with_tools.
"""

from __future__ import annotations

from stackowl.owls.base_prompt import LEAN_WINDOW_THRESHOLD

# Consecutive zero-progress dispatches of the SAME tool before it is bounced for
# the rest of the turn. Host-agnostic fixed default; scaled by window below.
NO_PROGRESS_THRESHOLD = 3


def resolve_no_progress_threshold(model_window: int | None) -> int:
    """A weak/lean-window model spirals faster and reasons worse about failure —
    contain it sooner. Capability-probed (reads the resolved window), never pinned
    to a host. A normal/strong or unknown window keeps the default."""
    if model_window is not None and model_window <= LEAN_WINDOW_THRESHOLD:
        return 2
    return NO_PROGRESS_THRESHOLD


class TurnProgressTracker:
    def __init__(self, threshold: int = NO_PROGRESS_THRESHOLD) -> None:
        self._threshold = threshold
        self._streak: dict[str, int] = {}
        self._open: list[str] = []
        self._made_progress = False

    def record_progress(self, name: str) -> None:
        self._streak[name] = 0
        self._made_progress = True

    def record_no_progress(self, name: str) -> bool:
        self._streak[name] = self._streak.get(name, 0) + 1
        if self._streak[name] >= self._threshold and name not in self._open:
            self._open.append(name)
            return True
        return False

    def is_open(self, name: str) -> bool:
        return name in self._open

    @property
    def made_progress(self) -> bool:
        return self._made_progress

    @property
    def opened_tools(self) -> tuple[str, ...]:
        return tuple(self._open)
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(tps): TurnProgressTracker + window-scaled threshold`.

---

### Task 2: PipelineState stamp fields

**Files:** Modify `src/stackowl/pipeline/state.py`; Test `tests/pipeline/test_progress_tracker.py` (extend).

**Interfaces — Produces:** `PipelineState.turn_made_progress: bool = True`, `PipelineState.no_progress_tools: tuple[str,...] = ()`.

- [ ] **Step 1: Failing test**

```python
def test_state_progress_defaults_are_byte_identical():
    from stackowl.pipeline.state import PipelineState
    s = PipelineState(trace_id="t", session_id="s", input_text="x", channel="cli",
                      owl_name="o", pipeline_step="execute")
    # Default True ⇒ a turn that never entered the tracker is NEVER floored by it.
    assert s.turn_made_progress is True
    assert s.no_progress_tools == ()
    s2 = s.evolve(turn_made_progress=False, no_progress_tools=("shell",))
    assert s2.turn_made_progress is False
    assert s2.no_progress_tools == ("shell",)
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — add the two fields near `budget_capped`/`delivered_successes` (state.py:~204), with a comment: *"Turn-progress supervisor (TPS). `turn_made_progress` defaults True so any non-tool path is byte-identical (never floored as no-progress). execute stamps False + `no_progress_tools` when the tracker saw no PROGRESS dispatch. INDEPENDENT of the consequential ledger."* Confirm `has_honesty_data`/`evolve` handle them (frozen dataclass — just add fields).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(tps): state stamp fields for turn progress`.

---

### Task 3: Wire the tracker into `_dispatch` (subsume breaker + close G1/G2) + stamp state

**Files:** Modify `src/stackowl/pipeline/steps/execute.py` (`_run_with_tools` + `_dispatch`); Test `tests/pipeline/test_progress_tracker.py` (extend with the dispatch-driven harness from `tests/pipeline/test_circuit_breaker.py`).

**Interfaces — Consumes:** Task 1 + 2. **Produces:** the live behavior at `_dispatch`.

- [ ] **Step 1: Failing tests** — reuse the `_SeqProvider`/`_CountingTool` harness already in `tests/pipeline/test_circuit_breaker.py` (import or duplicate the minimal harness). Add:
  - `test_timeout_failures_advance_streak_and_bounce` (G1): a tool whose `execute` raises `asyncio.TimeoutError`-equivalent (sleep beyond a squeezed `_TOOL_DEADLINE_S`, OR — simpler — a tool that the harness drives through the timeout path; if hard to trigger a real timeout, assert via a tool returning a timeout-shaped failure is NOT enough — must drive the real `except TimeoutError`). Assert the tool is bounced after `threshold` timeouts (`tool.calls == threshold`, the next is the circuit refusal).
  - `test_refusal_failures_advance_streak_and_bounce` (G2): a tool returning `ToolResult(success=False, side_effect_committed=False, ...)` every call → bounced after `threshold`; AND `tool_outcome_ledger.consequential_tally()` failures == 0 (the ledger stays clean — committed=False not counted).
  - `test_state_stamped_no_progress`: after a run where a tool only ever failed, `out.turn_made_progress is False` and the tool name in `out.no_progress_tools`.
  - `test_state_stamped_made_progress`: a run with a successful tool → `out.turn_made_progress is True`, `out.no_progress_tools == ()`.

> NOTE for the implementer: triggering the REAL `except TimeoutError` deterministically — set the tool's `execute` to `await asyncio.sleep(...)` longer than a monkeypatched-small `_TOOL_DEADLINE_S`, OR patch `asyncio.wait_for` in the test to raise `TimeoutError` for the target tool. Prefer monkeypatching `execute._TOOL_DEADLINE_S` to a tiny value + a tool that sleeps. The journey (Task 5) covers the same path end-to-end.

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**
  - In `_run_with_tools`, replace the `fail_streak: dict ...` / `circuit_open: set ...` declarations (added by P2) with:
    ```python
    from stackowl.pipeline.progress_tracker import TurnProgressTracker, resolve_no_progress_threshold
    progress = TurnProgressTracker(threshold=resolve_no_progress_threshold(state.model_window))
    ```
  - Circuit-open bounce at the top of `_dispatch`: `if progress.is_open(name):` → log + `return _circuit_open_refusal(name)` (unchanged string/log).
  - Timeout `except TimeoutError` block (execute.py:908-925): after the existing `record_tool_outcome(...)`, add `progress.record_no_progress(name)` (G1). Keep the early return.
  - Missing-param refusal block (execute.py:880-897): after its `record_tool_outcome(..., side_effect_committed=False)`, add `progress.record_no_progress(name)` (G2). (This is the no-op/validation refusal shape.)
  - Stop pre-check block (execute.py:847-860): do NOT count toward progress (turn is stopping — not a spiral). Leave as-is.
  - Normal record site (execute.py:926-962): replace the P2 `if tr.success: fail_streak... elif tr.side_effect_committed: ...` block with:
    ```python
    if tr.success:
        progress.record_no_progress  # NO — see below
    ```
    Correct form:
    ```python
    if tr.success:
        progress.record_progress(name)
    else:
        # Any genuine non-success dispatch is zero-progress regardless of
        # side_effect_committed — that's the G2 fix. The ledger above already
        # recorded the committed-aware outcome for the FLOOR; this counter is
        # the INDEPENDENT containment signal.
        progress.record_no_progress(name)
    ```
  - At the END of `_run_with_tools`, before returning state (find the return/`state.evolve(...)` site), stamp:
    ```python
    state = state.evolve(
        turn_made_progress=progress.made_progress,
        no_progress_tools=progress.opened_tools,
    )
    ```
    Ensure this also happens on the BudgetBreach return paths (mirror how `budget_capped`/snapshot are stamped) so a capped spiral still carries the stamp. Place the stamp in a helper or at each return site consistently.
- [ ] **Step 4: Run → PASS** (`tests/pipeline/test_progress_tracker.py` + the existing `tests/pipeline/test_circuit_breaker.py` must STILL pass — the breaker behavior is subsumed, not broken).
- [ ] **Step 5: Run** `uv run pytest tests/pipeline/ -q` → no new reds.
- [ ] **Step 6: Commit** `feat(tps): unify breaker into tracker at _dispatch — close G1/G2, stamp state`.

---

### Task 4: No-progress honest-floor trigger

**Files:** Modify `src/stackowl/pipeline/giveup_floor.py`; Test `tests/pipeline/test_no_progress_floor.py`.

**Interfaces — Produces:** an extended `surface_consequential_giveup_floor` that ALSO floors a no-progress-and-delivered-nothing turn; a predicate `is_no_progress_giveup(state) -> bool`.

- [ ] **Step 1: Failing tests**

```python
import pytest
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor, is_no_progress_giveup
from stackowl.pipeline.streaming import ResponseChunk


def _state(**kw):
    base = dict(trace_id="t", session_id="s", input_text="make me a chart", channel="cli",
                owl_name="o", pipeline_step="execute")
    base.update(kw)
    return PipelineState(**base)


def test_no_progress_turn_delivering_nothing_floors():
    # Tool spiraled (no progress), bounced, nothing delivered, draft is a confident non-floor.
    draft = ResponseChunk(content="All done — your chart is ready!", is_final=False,
                          chunk_index=0, trace_id="t", owl_name="o", is_floor=False)
    s = _state(responses=(draft,), turn_made_progress=False, no_progress_tools=("execute_code",),
               delivered_successes=())
    assert is_no_progress_giveup(s) is True
    out = await_floor(s)
    delivered = "".join(c.content for c in out.responses)
    assert "your chart is ready" not in delivered
    assert any(getattr(c, "is_floor", False) for c in out.responses)


def test_progressing_turn_not_floored():
    draft = ResponseChunk(content="Here is your answer.", is_final=False, chunk_index=0,
                          trace_id="t", owl_name="o", is_floor=False)
    s = _state(responses=(draft,), turn_made_progress=True, no_progress_tools=())
    assert is_no_progress_giveup(s) is False


def test_conversational_zero_tool_turn_not_floored():
    # Default turn_made_progress=True (never entered tracker) ⇒ never floored.
    draft = ResponseChunk(content="Hi there!", is_final=False, chunk_index=0,
                          trace_id="t", owl_name="o", is_floor=False)
    s = _state(responses=(draft,))  # defaults: made_progress True, no_progress_tools ()
    assert is_no_progress_giveup(s) is False
```
(Provide an `await_floor` helper using `asyncio.run`/pytest-asyncio as the repo's tests do; mirror `tests/journeys/test_budget_cap_overclaim_floor_journey.py` async style.)

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — add to `giveup_floor.py`:
  ```python
  def is_no_progress_giveup(state: PipelineState) -> bool:
      """True iff the turn made NO forward progress, delivered nothing to the user,
      and at least one tool was bounced for no-progress — i.e. the model spiraled and
      its draft cannot be trusted. INDEPENDENT of the consequential ledger (covers the
      G2 pure-refusal shape the consequential floor misses). turn_made_progress
      defaults True, so a non-tool / progressing / conversational turn is never caught."""
      try:
          if state.turn_made_progress:
              return False
          if not state.no_progress_tools:
              return False
          if state.delivered_successes:   # something crossed the boundary OUT → not a give-up
              return False
          # Don't double-floor: if the existing responses are already a floor, no-op.
          if any(getattr(c, "is_floor", False) for c in state.responses):
              return False
          return True
      except Exception as exc:
          log.engine.error("[giveup_floor] is_no_progress_giveup failed", exc_info=exc)
          return False
  ```
  In `surface_consequential_giveup_floor`, after the consequential `is_consequential_giveup_now(state)` no-op check returns the state, add a SECOND branch: if `is_no_progress_giveup(state)`, build the honest floor naming `state.no_progress_tools[0]` (via `synthesize_floor(..., failed_capability=state.no_progress_tools[0])`), log `"[giveup_floor] no forward progress — replacing draft with honest floor"`, and return `state.evolve(responses=(chunk,))`. Reuse the existing chunk-building code (factor a small `_floor_chunk(state, failed_name)` helper to avoid duplication).
- [ ] **Step 4: Run → PASS.** Also run `tests/journeys/test_budget_cap_overclaim_floor_journey.py` → still green (no regression to the consequential floor).
- [ ] **Step 5: Commit** `feat(tps): no-progress honest-floor trigger (closes the G2 honesty gap)`.

---

### Task 5: Phase-1 gateway journeys (falsification twin + G1 + G2 + liveness property)

**Files:** Create `tests/journeys/test_progress_supervisor_journey.py`; Create `tests/pipeline/test_progress_liveness_property.py`.

- [ ] **Step 1: Write the journeys** (mock ONLY the provider; drive REAL `_run_with_tools` + `surface_consequential_giveup_floor`, mirroring `tests/journeys/test_circuit_breaker_journey.py`):
  - `test_slow_diverse_success_not_tripped`: provider scripts 6 DIFFERENT tools, each succeeds (optionally each `await asyncio.sleep(0)` to model latency without real delay). Assert: NO floor (`not any is_floor`), all 6 executed, delivery contains results, `out.turn_made_progress is True`.
  - `test_same_tool_failure_spiral_contained`: one tool always fails → executed exactly `threshold` times, then bounced; turn floors honestly (overclaim absent, is_floor present), floor names the tool.
  - `test_timeout_spiral_contained_and_floors` (G1): a tool that times out every call (monkeypatch `execute._TOOL_DEADLINE_S` tiny + a sleeping tool) → bounced after `threshold` timeouts; honest floor names the tool.
  - `test_refusal_spiral_contained_and_floors` (G2): a tool returning `success=False, side_effect_committed=False` every call → bounced after `threshold`; honest floor names the tool; AND assert the consequential ledger tally was clean (no effectful failures recorded) — honesty separation holds.
  - `test_transient_failure_then_success_delivered`: fail,fail,success → never bounced, success delivered, no floor.
- [ ] **Step 2: Write the property/liveness test** (`tests/pipeline/test_progress_liveness_property.py`) over `TurnProgressTracker` directly (no provider needed): generate random sequences of `("progress"|"no_progress", tool)` ops; assert the invariant: for any tool that receives ≥`threshold` CONSECUTIVE `no_progress` ops with no intervening `progress`, `is_open(tool)` becomes True at exactly the `threshold`-th; and a tool that never reaches `threshold` consecutive no-progress is never open. Use a fixed seed list (no `random` at import — pass seeds via parametrize) per the repo's no-`Math.random`/`Date.now` discipline; enumerate ~20 hand-built + deterministically-generated sequences.
- [ ] **Step 3: Run** both files → PASS. If `test_*_spiral_*` shows the tool running past `threshold`, the wiring is broken — STOP, fix Task 3 (don't weaken the test).
- [ ] **Step 4: Run** `uv run pytest tests/journeys/ -q` → only the documented pre-existing red.
- [ ] **Step 5: Commit** `test(tps): falsification twin + G1/G2 journeys + liveness property`.

---

# PHASE 2 — Overclaim delivery-gate

### Task 6: `surface_overclaim_gate` + outcome field + wiring (both backends)

**Files:** Create `src/stackowl/pipeline/overclaim_gate.py`; Modify both backends; Modify `src/stackowl/memory/outcome_store.py` (+ `_capture_outcome` call sites); Test `tests/pipeline/test_overclaim_gate.py` + `tests/journeys/test_overclaim_gate_journey.py`.

**Interfaces — Produces:** `async def surface_overclaim_gate(state) -> PipelineState`.

- [ ] **Step 1: Failing tests**
  - Unit (`test_overclaim_gate.py`):
    - `test_overclaim_blocked`: response `is_floor=False`, non-empty; `delivered_successes=()`; an effectful failure present (set `consequential_failures=("send_image",)`, `consequential_snapshot_taken=True`) → gate REPLACES with honest floor (`is_floor=True`); overclaim text gone.
    - `test_no_progress_overclaim_blocked`: `turn_made_progress=False`, `no_progress_tools=("execute_code",)`, `delivered_successes=()`, non-floor draft → blocked.
    - `test_delivered_success_not_blocked`: `delivered_successes=("send_image",)` + non-floor draft → NOT blocked (kept).
    - `test_conversational_zero_tool_not_blocked`: defaults (made_progress True, no failures, no no_progress_tools) → NOT blocked.
    - `test_already_floor_not_double_processed`: `is_floor=True` draft → returned untouched.
  - Journey (`test_overclaim_gate_journey.py`), FAILING-FIRST: script provider to genuinely fail a tool then emit "I've successfully completed the task!" partial; run REAL `_run_with_tools` → floor → `surface_overclaim_gate`. Assert: WITHOUT the gate the overclaim would ship (demonstrate by asserting the gate's effect — see note), WITH it the honest floor ships and names the failed tool.
    > Note: to prove failing-first, first add the journey with the gate NOT yet wired and watch the overclaim reach `delivered`; then wire and watch it go green. Capture this in the report.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `overclaim_gate.py`:
  ```python
  """surface_overclaim_gate — block a confident non-floor response that delivered
  nothing real while tools failed/bounced. STRUCTURAL (no fragile text analysis):
  reuses delivered_successes (P0) + the TPS no_progress stamp. Runs AFTER the
  give-up floor, BEFORE deliver, in both backends. Never raises. Emits structured
  overclaim.detected / overclaim.cleared so a dead gate is visible."""
  from __future__ import annotations
  from stackowl.infra import tool_outcome_ledger
  from stackowl.infra.observability import log
  from stackowl.pipeline.giveup_floor import _unrecovered_consequential_failures, _floor_chunk
  from stackowl.pipeline.state import PipelineState

  def _is_overclaim(state: PipelineState) -> tuple[bool, str | None]:
      # Already honest, or empty → nothing to gate.
      if not state.responses or all(not c.content.strip() for c in state.responses):
          return (False, None)
      if any(getattr(c, "is_floor", False) for c in state.responses):
          return (False, None)
      if state.delivered_successes:           # something crossed OUT → legitimate
          return (False, None)
      # A failed/bounced tool with nothing delivered + a confident draft = overclaim.
      unrecovered = _unrecovered_consequential_failures(state)
      stuck = set(state.no_progress_tools)
      culprit = next(iter(unrecovered), None) or next(iter(stuck), None)
      if culprit is None:                      # no tool failed/bounced → not an overclaim
          return (False, None)
      return (True, culprit)

  async def surface_overclaim_gate(state: PipelineState) -> PipelineState:
      try:
          is_oc, culprit = _is_overclaim(state)
          if not is_oc:
              log.engine.info("overclaim.cleared", extra={"_fields": {"trace_id": state.trace_id}})
              return state
          log.engine.warning("overclaim.detected", extra={"_fields": {
              "trace_id": state.trace_id, "failed_capability": culprit}})
          return state.evolve(responses=(_floor_chunk(state, culprit),))
      except Exception as exc:
          log.engine.error("[overclaim_gate] failed — leaving response untouched", exc_info=exc,
                           extra={"_fields": {"trace_id": state.trace_id}})
          return state
  ```
  (Requires `_floor_chunk(state, failed_name)` helper extracted in Task 4 — if not, extract it now in giveup_floor.py and import it.)
  - Wire into `asyncio_backend.py` (after `surface_consequential_giveup_floor`, before `persist_turn`/`deliver`) and the langgraph backend's `_deliver_with_surfacing` at the same point.
  - Add `overclaim_blocked: bool` to `TaskOutcomeStore.record(...)` + migration for an `overclaim_blocked` column (new migration `00NN_task_outcome_overclaim.sql`), and set it in `_capture_outcome` from a state flag the gate stamps (`state.evolve(overclaim_blocked=True)` — add a PipelineState field `overclaim_blocked: bool = False`). Keep the column nullable/defaulted so old rows are fine.
- [ ] **Step 4: Run → PASS** (unit + journey). Run both backends' existing delivery journeys → no regression.
- [ ] **Step 5: Commit** `feat(tps): overclaim delivery-gate (structural, failing-first, wired both backends)`.

---

# PHASE 3 — Capability-honest degradation

### Task 7: Window-scaled threshold (already in Task 1) — verification + honest-degradation floor message

**Files:** Modify `src/stackowl/pipeline/supervisor.py` (`synthesize_floor`) for a lean-window-aware honest message; Test `tests/pipeline/test_capability_honest.py`.

**Interfaces — Consumes:** `resolve_no_progress_threshold` (Task 1, already wired in Task 3 via `state.model_window`). **Produces:** an honest-degradation phrasing when the floor fires on a lean window.

- [ ] **Step 1: Failing tests**
  - `test_lean_window_lowers_threshold_end_to_end` (journey): same always-failing tool, but the owl/state has a lean `model_window` (≤8192) → bounced after **2** failures (not 3). Drives the real path with `state.model_window` set.
  - `test_normal_window_keeps_default` (journey): `model_window=16384` → bounced after 3 (byte-identical to Phase 1).
  - `test_floor_message_acknowledges_capability_limit_on_lean`: `synthesize_floor(..., failed_capability="execute_code", lean=True)` (add a `lean: bool=False` kwarg) returns a message that honestly notes a limitation; `lean=False` is byte-identical to today.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**
  - Confirm Task 3 already constructs the tracker with `resolve_no_progress_threshold(state.model_window)` — if so, the threshold scaling is live; the journey just verifies it. If `state.model_window` isn't reliably set on the test path, set it in the test owl/state.
  - Add a `lean: bool = False` kwarg to `synthesize_floor` (`supervisor.py`); when True, append/select an honest-degradation clause (no case-specifics, language-neutral via the existing localization path). Thread `lean = (state.model_window is not None and state.model_window <= LEAN_WINDOW_THRESHOLD)` from `surface_consequential_giveup_floor` / `_floor_chunk` into `synthesize_floor`. `lean=False` MUST be byte-identical to current output (assert in the test).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(tps): capability-honest degradation — lean window contains faster + honest floor phrasing`.

**Scope boundary (documented, deliberate):** the deeper "lean window → router readier to clarify/decline BEFORE the loop" prevention bias is NOT implemented here — P1's clarify verdict already does up-front prevention, and stacking a second pre-loop decline risks over-clarifying / regressing P1. Phase 3 delivers capability-honest degradation via (a) faster containment on weak windows and (b) honest floor phrasing. A router-bias follow-up is a separate, P1-coordinated change.

---

### Task 8: Lint, type-check, full regression, whole-branch review, merge

- [ ] **Step 1:** `uv run ruff check` + `uv run mypy src/stackowl/...` on all touched files → clean.
- [ ] **Step 2:** `uv run pytest tests/pipeline/ tests/journeys/ -q` → green except the documented pre-existing red; confirm it's on the merge-base too.
- [ ] **Step 3:** Opus whole-branch review with the mandate: trace the 6 honesty invariants (spec §6); confirm two counters never conflated; confirm no false-floor/false-bounce/false-overclaim on healthy + conversational turns; confirm the overclaim gate is on the real delivery path for BOTH backends and is provably not a no-op; confirm host-agnostic (strong window byte-identical). Fix all Critical/Important.
- [ ] **Step 4:** Merge to main + push (standing rule), keep the branch.
- [ ] **Step 5:** Update `project_pictures_overclaim_incident.md` + a new memory for the supervisor arc.

---

## Self-Review
- Spec §1 gaps G1/G2 → Tasks 3,5 (close + journey). ✓
- §2 unification (tracker subsumes breaker, two counters) → Tasks 1,3 + Global Constraints. ✓
- §3 honesty composition (no-progress floor, overclaim gate) → Tasks 4,6. ✓
- §3 capability-honest degradation → Tasks 1,7. ✓
- §4 test strategy (falsification twin, G1/G2 red→green, liveness property, overclaim failing-first) → Tasks 5,6. ✓
- §6 invariants → Task 8 review mandate + per-task assertions. ✓
- §7 decisions: floor EXTEND (Task 4), overclaim STANDALONE (Task 6), threshold FUNCTION (Task 1), prevention-knob deferred-with-rationale (Task 7 scope boundary). ✓

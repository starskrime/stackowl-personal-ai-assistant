# Boundary-Honesty Arc Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four boundary-condition leaks the platform self-diagnosed — failure-history bleeding into greetings, cross-turn over-apology amplification, the missing durable-task lookup tool, and broken Telegram/Slack tables — without touching the (solid) deliver-time honesty floor.

**Architecture:** The deliver-time honesty floor is a *runtime* invariant and stays untouched. These four fixes install *boundary* invariants on the two edges of a turn: an **inbound context-admission gate** (Stories #2, #4 — govern what historical/reflective content enters the next turn's prompt, fail-closed) and an **outbound channel-rendering contract** (Story #1 — deterministically flatten structures the channel can't render). Story #3 is an orthogonal missing-capability fix (expose an existing owner-scoped store method as a read tool). Sequenced trust-first: **#2 → #4 → #3 → #1.**

**Tech Stack:** Python ≥3.12, pydantic frozen models, pytest (`uv run pytest`), mypy strict, ruff. SQLite-backed stores via `DbPool`. Channel adapters: grammY-style Telegram, Slack mrkdwn.

## Global Constraints

- Run from repo root: `uv run pytest <path>`, `uv run ruff check src/`, `uv run mypy src/`.
- **Subagent-driven TDD**: failing test first, verify it fails, minimal implementation, verify pass, commit. One logical change per commit; keep the tree green/bisectable. Stage only the files for that task.
- **Gateway journeys assert on the ASSEMBLED CONTEXT handed to the provider mock (`system_text` / message list), never on model output text.** The previous 4 production-breakers shipped green because tests asserted returns, not wiring.
- **Absence tests must arm the gun + carry a positive control** (seed the data, prove it CAN appear, then prove it's absent on the protected path). No vacuous passes.
- **No hidden errors**: every `except` logs via `log.<module>.error(...)`; no silent degraded fallback.
- **Global, not example-specific**: no fix may key on the specific phrasing that surfaced it. No "tell the model to behave" prompt patches.
- **Check existing before writing new**: reuse `OwnedRepository`, `TraceContext`, the scripted-provider fixtures, the stash machinery. Don't recreate.
- Registry classmethod is **`ToolRegistry.with_defaults()`** (not `build_default`).
- `PipelineState` is `frozen=True`; mutate via `state.evolve(**kwargs)`.

---

## Story #2 — Failure-history is never injected on an unclassified/non-work turn

**Why:** `classify.py` builds `## Recent Reflections` (`_gather_recent_reflections`, owl-scoped across ALL sessions — `reflection_store.recent_for_owl`, no `session_id` column exists) and `## What You Did Recently` (`_gather_recent_actions`, session-scoped) and suppresses them ONLY when `_lean = state.intent_class in TOOL_FREE_CLASSES`. A direct-address turn (`@owl hi`) returns from `triage.py:75` **without running the router**, so `intent_class` keeps its `PipelineState` default `"standard"` → `_lean` is False → cross-session failure summaries are injected into a greeting. This is the "references task failures that didn't happen in this session" symptom.

**Fix:** Distinguish "the router positively classified this as a work turn" from "intent_class is the untouched default." Inject failure-history blocks only on a positively-classified standard turn (`_should_surface_failure_history`), failing closed everywhere else (direct-address, router error, conversational/clarify).

**Files:**
- Modify: `src/stackowl/pipeline/state.py` (add field, ~line 58 region)
- Modify: `src/stackowl/pipeline/steps/triage.py` (stamp the flag on the real-classification path, ~line 117)
- Modify: `src/stackowl/pipeline/steps/classify.py` (new gate helper + use it at ~lines 478, 481)
- Test: `tests/pipeline/steps/test_classify_failure_history_gate.py` (new, unit)
- Test: `tests/pipeline/steps/test_triage_intent_classified.py` (new, unit)
- Test: `tests/journeys/test_no_false_history_journey.py` (new, gateway merge-gate)

**Interfaces:**
- Produces: `PipelineState.intent_classified: bool` (default `False`); `classify._should_surface_failure_history(state: PipelineState) -> bool`.
- Consumes: existing `state.intent_class`, `TOOL_FREE_CLASSES`, `_gather_recent_reflections`, `_gather_recent_actions`.

- [ ] **Step 1 (state field) — write the failing test**

`tests/pipeline/steps/test_triage_intent_classified.py`:
```python
from stackowl.pipeline.state import PipelineState


def _state(**kw) -> PipelineState:
    base = dict(trace_id="t", session_id="s", input_text="hi", owl_name="secretary")
    base.update(kw)
    return PipelineState(**base)


def test_intent_classified_defaults_false() -> None:
    # A freshly minted turn has NOT been positively classified yet.
    assert _state().intent_classified is False
```

- [ ] **Step 2 — run, verify it fails**

Run: `uv run pytest tests/pipeline/steps/test_triage_intent_classified.py::test_intent_classified_defaults_false -v`
Expected: FAIL — `PipelineState` has no field `intent_classified`.

- [ ] **Step 3 — add the field**

In `src/stackowl/pipeline/state.py`, immediately after the `intent_class` field (the `intent_class: Literal[...] = "standard"` line ~58):
```python
    #: True only once the SecretaryRouter has POSITIVELY classified this turn
    #: (work/standard/conversational/clarify). Stays False on the direct-address
    #: path (triage returns before the router runs) and on any router error —
    #: so failure-history admission can fail CLOSED rather than treating the
    #: untouched ``intent_class="standard"`` default as a confirmed work turn.
    intent_classified: bool = False
```

- [ ] **Step 4 — verify pass**

Run: `uv run pytest tests/pipeline/steps/test_triage_intent_classified.py::test_intent_classified_defaults_false -v`
Expected: PASS.

- [ ] **Step 5 — write the failing triage test**

Append to `tests/pipeline/steps/test_triage_intent_classified.py` a test that drives the real `triage` step on the secretary path and asserts `intent_classified is True`, and on the direct-address path asserts it stays `False`. Mirror the triage-step harness used in the nearest existing triage test (`tests/pipeline/steps/test_triage*.py`) — reuse its `StepServices`/owl-registry/scripted-router fixtures; do NOT hand-roll new provider doubles. Assertions:
```python
async def test_secretary_path_marks_classified(secretary_env) -> None:
    out = await run_triage(secretary_env, owl_name="secretary", text="summarize my tasks")
    assert out.intent_classified is True

async def test_direct_address_leaves_unclassified(secretary_env) -> None:
    out = await run_triage(secretary_env, owl_name="scout", text="hi")
    assert out.intent_classified is False
    assert out.intent_class == "standard"  # untouched default — the bug's entry condition
```

- [ ] **Step 6 — run, verify it fails**

Run: `uv run pytest tests/pipeline/steps/test_triage_intent_classified.py -v`
Expected: the two new tests FAIL (`intent_classified` never set True).

- [ ] **Step 7 — stamp the flag where the router result is applied**

In `src/stackowl/pipeline/steps/triage.py`, at the secretary-path `state.evolve(...)` that stamps `intent_class=result.intent_class` (~line 117-122), add `intent_classified=True` to the same `evolve` call. Do NOT add it to any direct-address `return state` (lines 55, 75) or the unknown-owl demotion (line 64).

- [ ] **Step 8 — verify pass**

Run: `uv run pytest tests/pipeline/steps/test_triage_intent_classified.py -v`
Expected: all PASS.

- [ ] **Step 9 — write the failing classify-gate test**

`tests/pipeline/steps/test_classify_failure_history_gate.py`:
```python
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.classify import _should_surface_failure_history


def _s(**kw) -> PipelineState:
    base = dict(trace_id="t", session_id="s", input_text="x", owl_name="secretary")
    base.update(kw)
    return PipelineState(**base)


def test_surfaces_on_classified_standard_turn() -> None:
    assert _should_surface_failure_history(_s(intent_class="standard", intent_classified=True)) is True


def test_suppressed_on_direct_address_default_standard() -> None:
    # The bug: standard-by-default + never classified must NOT surface failures.
    assert _should_surface_failure_history(_s(intent_class="standard", intent_classified=False)) is False


def test_suppressed_on_conversational() -> None:
    assert _should_surface_failure_history(_s(intent_class="conversational", intent_classified=True)) is False
```

- [ ] **Step 10 — run, verify it fails**

Run: `uv run pytest tests/pipeline/steps/test_classify_failure_history_gate.py -v`
Expected: FAIL — `_should_surface_failure_history` not defined.

- [ ] **Step 11 — implement the gate and route the blocks through it**

In `src/stackowl/pipeline/steps/classify.py`, add the helper near the other module-level gather helpers:
```python
def _should_surface_failure_history(state: PipelineState) -> bool:
    """Fail-closed admission gate for failure-history prompt blocks.

    Past-failure context (``## Recent Reflections`` / ``## What You Did
    Recently``) is admitted ONLY on a turn the router POSITIVELY classified as
    standard/work. A greeting that lands as ``standard`` by default — the
    direct-address bypass, or any router error — is NOT a confirmed work turn,
    so its failure history is withheld. Trade chosen deliberately: omitting a
    reflection on a true work turn is mild degradation; injecting phantom
    failure-history into a greeting is trust-destroying. Fail toward silence.
    """
    return state.intent_class == "standard" and state.intent_classified
```
Then replace the `_lean`-only gating at lines ~478 and ~481. Currently:
```python
    reflections_block = "" if _lean else await _gather_recent_reflections(state.owl_name, limit=3)
    actions_block = "" if _lean else await _gather_recent_actions(
        state.session_id, state.trace_id, limit=3,
    )
```
becomes:
```python
    _surface_failures = _should_surface_failure_history(state)
    reflections_block = await _gather_recent_reflections(state.owl_name, limit=3) if _surface_failures else ""
    actions_block = await _gather_recent_actions(
        state.session_id, state.trace_id, limit=3,
    ) if _surface_failures else ""
```
(Leave the other `_lean` suppressions — graph_context, skills, lessons — exactly as they are.)

- [ ] **Step 12 — verify pass + no regressions in classify**

Run: `uv run pytest tests/pipeline/steps/test_classify_failure_history_gate.py tests/pipeline/steps/ -v`
Expected: new tests PASS; existing classify tests still green. If an existing test asserted reflections appear on an unclassified `standard` state, update it to set `intent_classified=True` (that was the latent bug, not a behavior we keep).

- [ ] **Step 13 — write the failing gateway merge-gate journey**

`tests/journeys/test_no_false_history_journey.py`. Mirror `tests/journeys/test_skill_injection_journey.py` for the fixture/driver shape: a `_ScriptedSpecialist` whose `complete_with_tools` captures `system_text`, a real `DbPool`, `StepServices`, `_turn(env, text)` driving `GatewayScanner` → `AsyncioBackend.run`. Three prongs in one test class:
```python
# Arm the gun: seed a real cross-session reflection for the owl.
from stackowl.memory.reflection_store import ReflectionStore
store = ReflectionStore(db)  # owner-scoped, owl="scout"
await store.append(...)      # use the store's real writer; failure_class set, summary distinctive

MARKER = "<the distinctive reflection summary text>"

# (a) POSITIVE CONTROL — a classified standard work turn surfaces the block.
env_secretary = ...  # secretary path, router classifies "standard" → intent_classified=True
await _turn(env_secretary, "what went wrong on my last task?")
assert MARKER in provider.system_text          # proves fixture armed + path live

# (b) NEGATIVE — direct-address greeting must NOT surface failure history.
provider.system_text = ""
await _turn(env_direct, "@scout hi")           # direct-address bypass → intent_classified=False
assert MARKER not in provider.system_text
assert "## Recent Reflections" not in provider.system_text
```
If driving the router to a deterministic "standard" verdict in-journey is fragile, make the positive control a classify-step integration assertion (seed reflection, run `classify.run` on a `state` with `intent_classified=True`, assert the block in `state.memory_context`) and keep the gateway journey focused on the direct-address ABSENCE assertion — that is the path the lean gate never protected.

- [ ] **Step 14 — run, verify the negative prong fails before, passes after**

Run: `uv run pytest tests/journeys/test_no_false_history_journey.py -v`
Expected: with Steps 1-12 already applied it PASSES; to prove the journey has teeth, temporarily revert Step 11 and confirm the negative prong FAILS (then restore). Note this in the commit body.

- [ ] **Step 15 — lint, type, commit**

```bash
uv run ruff check src/ && uv run mypy src/stackowl/pipeline
git add src/stackowl/pipeline/state.py src/stackowl/pipeline/steps/triage.py src/stackowl/pipeline/steps/classify.py tests/pipeline/steps/test_triage_intent_classified.py tests/pipeline/steps/test_classify_failure_history_gate.py tests/journeys/test_no_false_history_journey.py
git commit -m "fix(classify): fail-closed admission gate for failure-history blocks (no phantom history on greetings)"
```

---

## Story #4 — Stale apology prose is not re-fed/amplified across turns

**Why:** The system's critical-failure apology is already excluded from persistence (it trips `_critical_failure_classes` → `_turn_floored` → `User:`-only row). The real amplifier is the **model's own apologetic prose** on a normal turn: `is_floor=False`, no step error → `_turn_floored` is False → `turn_persist` writes `User: X\n\nAssistant: <apology>` (line 102) → `_parse_turns_to_messages` re-feeds it verbatim as `Message(role="assistant")` next turn (window 6) with **no dedup**. Repeated/near-identical corrections stack and the weak model keeps the pattern going.

**Fix (deterministic, no NL apology-classifier):** dedup the re-fed history at the `_gather_history` seam — when the same assistant message content recurs in the window, keep only its most-recent occurrence. This contains the amplification loop (the actual cross-turn defect) without trying to judge "is this an apology" on a weak model.

**Files:**
- Modify: `src/stackowl/pipeline/steps/classify.py` (`_parse_turns_to_messages` ~line 405 / `_gather_history` ~line 424)
- Test: `tests/pipeline/steps/test_history_dedup.py` (new, unit)
- Test: `tests/journeys/test_apology_no_amplify_journey.py` (new, gateway, cross-turn)

**Interfaces:**
- Produces: `classify._dedup_assistant_history(messages: list[Message]) -> list[Message]`.
- Consumes: existing `Message`, `_parse_turns_to_messages`, `_gather_history`.

- [ ] **Step 1 — write the failing unit test**

`tests/pipeline/steps/test_history_dedup.py`:
```python
from stackowl.providers.base import Message  # adjust import to the real Message location
from stackowl.pipeline.steps.classify import _dedup_assistant_history


def test_repeated_assistant_message_collapses_to_latest() -> None:
    apology = "Sorry — I've corrected that. Let me know if you need anything else."
    msgs = [
        Message(role="user", content="do X"),
        Message(role="assistant", content=apology),
        Message(role="user", content="ok and Y"),
        Message(role="assistant", content=apology),
        Message(role="user", content="so what?"),
        Message(role="assistant", content=apology),
    ]
    out = _dedup_assistant_history(msgs)
    # The apology survives exactly once (Murat's count-ceiling: <= 1).
    assert sum(1 for m in out if m.role == "assistant" and m.content == apology) == 1
    # User turns are never dropped.
    assert [m.content for m in out if m.role == "user"] == ["do X", "ok and Y", "so what?"]


def test_distinct_assistant_messages_all_kept() -> None:
    msgs = [
        Message(role="assistant", content="A"),
        Message(role="assistant", content="B"),
    ]
    assert [m.content for m in _dedup_assistant_history(msgs)] == ["A", "B"]
```

- [ ] **Step 2 — run, verify it fails**

Run: `uv run pytest tests/pipeline/steps/test_history_dedup.py -v`
Expected: FAIL — `_dedup_assistant_history` not defined.

- [ ] **Step 3 — implement the dedup and apply it in `_gather_history`**

In `src/stackowl/pipeline/steps/classify.py`:
```python
def _dedup_assistant_history(messages: list[Message]) -> list[Message]:
    """Collapse repeated assistant turns to their most-recent occurrence.

    A weak model that re-sends the same correction/apology turn after turn gets
    that prose persisted and re-fed (window 6), which reinforces the loop. We
    keep USER turns verbatim (the real conversation) and, for assistant turns,
    drop every earlier occurrence of a content that recurs later in the window
    — deterministic, content-keyed, no natural-language apology detection.
    """
    seen_later: dict[str, int] = {}
    for i, m in enumerate(messages):
        if m.role == "assistant":
            seen_later[m.content.strip()] = i  # last index wins
    out: list[Message] = []
    for i, m in enumerate(messages):
        if m.role == "assistant" and seen_later.get(m.content.strip(), i) != i:
            continue  # an identical assistant turn appears later — drop this earlier one
        out.append(m)
    return out
```
Then in `_gather_history` (~line 442), wrap the parsed result:
```python
    return _dedup_assistant_history(_parse_turns_to_messages([t.content for t in turns]))
```

- [ ] **Step 4 — verify pass**

Run: `uv run pytest tests/pipeline/steps/test_history_dedup.py -v`
Expected: PASS.

- [ ] **Step 5 — write the failing cross-turn journey**

`tests/journeys/test_apology_no_amplify_journey.py`. Reuse the tool-capable scripted provider from `tests/journeys/test_conversational_bypass_journey.py` (`_ScriptedProvider`). **Fixture gap to close first:** `complete_with_tools` (lines ~114-133) does NOT append to `self.calls`; add `self.calls.append(list(messages))` at the top of that method (test-fixture-only change) so the standard-path handed context is captured. Then script the SAME apology reply for 3 sequential turns and assert the count ceiling on turn 3's handed history:
```python
APOLOGY = "Sorry about that — I've fixed it. Anything else?"
provider = _ScriptedProvider("answer-std", [APOLOGY, APOLOGY, APOLOGY])
# drive 3 turns, same session_id
for text in ["please rename the file", "and the other one", "so what?"]:
    await _execute_turn(text, session, trace_next(), backend)
turn3_history = provider.calls[-1]  # messages handed to the provider on turn 3
n = sum(1 for m in turn3_history if getattr(m, "role", None) == "assistant" and APOLOGY in getattr(m, "content", ""))
assert n <= 1, f"apology amplified into turn-3 context {n} times"
```

- [ ] **Step 6 — run, verify it fails before / passes after**

Run: `uv run pytest tests/journeys/test_apology_no_amplify_journey.py -v`
Expected: PASS with Step 3 applied; temporarily revert the `_gather_history` wrap to confirm the assertion FAILS (apology appears 2×), then restore. Note in commit body.

- [ ] **Step 7 — lint, type, commit**

```bash
uv run ruff check src/ && uv run mypy src/stackowl/pipeline
git add src/stackowl/pipeline/steps/classify.py tests/pipeline/steps/test_history_dedup.py tests/journeys/test_apology_no_amplify_journey.py tests/journeys/test_conversational_bypass_journey.py
git commit -m "fix(classify): dedup repeated assistant turns in re-fed history (contain cross-turn over-apology)"
```

---

## Story #3 — Read-only `task_status` tool (owner-scoped, fail-closed)

**Why:** `DurableTaskStore.get(task_id)` exists and is owner-scoped + fail-loud, but it is NOT exposed as an agent tool. The agent's only primitives are `shell`/`read_file`/`search_files`, so "status of t1?" forces a filesystem scan. Expose a direct lookup.

**Fix:** A new `read`-severity `TaskStatusTool` mirroring `SessionSearchTool`, backed by `DurableTaskStore` scoped via `TraceContext.durable_owner_id()` (fall back to `DEFAULT_PRINCIPAL_ID`, matching `delegate_task`). `get` only — defer `list` (broad scan = scope-leak risk). Honest not-found, never an empty success.

**Files:**
- Create: `src/stackowl/tools/tasks/task_status.py`
- Modify: `src/stackowl/tools/registry.py` (register in `with_defaults`, ~line 506 near `DelegateTaskTool`)
- Test: `tests/tools/tasks/test_task_status.py` (new)
- Test: `tests/journeys/test_task_status_journey.py` (new)

**Interfaces:**
- Produces: `TaskStatusTool` (name `"task_status"`, params `{"task_id": str}`, manifest `action_severity="read"`, `toolset_group="tasks"`), registered in `ToolRegistry.with_defaults()`.
- Consumes: `DurableTaskStore.get`, `DurableTaskNotFoundError`, `TraceContext.durable_owner_id`, `DEFAULT_PRINCIPAL_ID`, `Tool`/`ToolResult`/`ToolManifest` from `tools/base.py`, `get_services().db_pool`.

- [ ] **Step 1 — write the failing unit test**

`tests/tools/tasks/test_task_status.py`. Mirror `tests/tools/knowledge/test_session_search.py` for service wiring. Cases:
```python
async def test_returns_status_for_known_task(task_env) -> None:
    # task_env seeds DurableTaskStore (default owner) with task_id="t1", status="running", current_step=2
    tool = TaskStatusTool()
    res = await tool.execute(task_id="t1")
    assert res.success is True
    assert "running" in res.output and "t1" in res.output

async def test_unknown_task_is_honest_not_found(task_env) -> None:
    tool = TaskStatusTool()
    res = await tool.execute(task_id="nope")
    assert res.success is False
    assert "not found" in res.error.lower()
    assert res.output == ""               # never an empty success the model narrates as "no tasks"

async def test_missing_db_degrades_structured(no_db_env) -> None:
    res = await TaskStatusTool().execute(task_id="t1")
    assert res.success is False and "unavailable" in res.error.lower()

async def test_missing_task_id_is_rejected() -> None:
    res = await TaskStatusTool().execute()
    assert res.success is False
```

- [ ] **Step 2 — run, verify it fails**

Run: `uv run pytest tests/tools/tasks/test_task_status.py -v`
Expected: FAIL — module/`TaskStatusTool` does not exist.

- [ ] **Step 3 — implement the tool**

`src/stackowl/tools/tasks/task_status.py` (model on `session_search.py`; reuse its `_ok`/`_err`/`_unavailable` shape and the 4-point logging standard):
```python
"""Read-only durable-task status lookup tool."""
from __future__ import annotations

import time

from stackowl.di import get_services
from stackowl.infra.trace import TraceContext
from stackowl.logger import log
from stackowl.pipeline.durable.store import DurableTaskStore, DurableTaskNotFoundError
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.base import Tool, ToolManifest, ToolResult


class TaskStatusTool(Tool):
    @property
    def name(self) -> str:
        return "task_status"

    @property
    def description(self) -> str:
        return (
            "Look up the status of a durable task by its exact id "
            "(e.g. 't1'). Returns status, current step, and goal. "
            "Use this instead of searching the filesystem when you have a task id."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Exact task id."}},
            "required": ["task_id"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="tasks",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        task_id = str(kwargs.get("task_id", "")).strip()
        log.tool.info("task_status.execute: entry", extra={"_fields": {"task_id": task_id}})
        if not task_id:
            return self._err("task_id is required.", t0)
        db = get_services().db_pool
        if db is None:
            return self._unavailable("no database pool is configured", t0)
        owner = TraceContext.durable_owner_id() or DEFAULT_PRINCIPAL_ID
        try:
            task = await DurableTaskStore(db, owner).get(task_id)
        except DurableTaskNotFoundError:
            return self._err(f"task {task_id!r} not found", t0)
        except Exception as exc:  # self-heal: degrade, never raise
            log.tool.error("task_status.execute: failed", exc_info=exc,
                           extra={"_fields": {"task_id": task_id}})
            return self._unavailable(f"{type(exc).__name__}: {exc}", t0)
        out = (f"Task {task.task_id}: status={task.status}, "
               f"step={task.current_step}, goal={task.goal}")
        return self._ok(out, t0)

    def _ok(self, output: str, t0: float) -> ToolResult:
        dt = (time.monotonic() - t0) * 1000
        log.tool.info("task_status.execute: exit", extra={"_fields": {"success": True, "duration_ms": dt}})
        return ToolResult(success=True, output=output, duration_ms=dt)

    def _err(self, msg: str, t0: float) -> ToolResult:
        dt = (time.monotonic() - t0) * 1000
        log.tool.info("task_status.execute: exit", extra={"_fields": {"success": False, "error": msg, "duration_ms": dt}})
        # read tool that did not act → no side effect committed
        return ToolResult(success=False, output="", error=msg, duration_ms=dt, side_effect_committed=False)

    def _unavailable(self, reason: str, t0: float) -> ToolResult:
        dt = (time.monotonic() - t0) * 1000
        msg = f"task status unavailable: {reason}"
        log.tool.warning("task_status.execute: store unavailable", extra={"_fields": {"reason": reason, "duration_ms": dt}})
        return ToolResult(success=False, output="", error=msg, duration_ms=dt, side_effect_committed=False)
```
Add `tests/tools/tasks/__init__.py` and `src/stackowl/tools/tasks/__init__.py` if those dirs are new. Verify the exact import paths against the grounding (`DurableTaskNotFoundError` lives in `pipeline/durable/store.py`; `get_services` import path; `TraceContext` at `infra/trace.py`).

- [ ] **Step 4 — verify pass**

Run: `uv run pytest tests/tools/tasks/test_task_status.py -v`
Expected: PASS.

- [ ] **Step 5 — write the failing registration test**

Append:
```python
def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry
    tool = ToolRegistry.with_defaults().get("task_status")
    assert isinstance(tool, TaskStatusTool)
    assert tool.manifest.action_severity == "read"
    assert tool.manifest.toolset_group == "tasks"
```

- [ ] **Step 6 — run, verify it fails**

Run: `uv run pytest tests/tools/tasks/test_task_status.py::test_registered_in_with_defaults -v`
Expected: FAIL — not registered.

- [ ] **Step 7 — register it**

In `src/stackowl/tools/registry.py`, near the `DelegateTaskTool` registration (~line 506), add:
```python
        # task_status — read-only durable-task lookup by id (owner-scoped via the
        # DurableTaskStore the tool builds from TraceContext.durable_owner_id() at
        # execute time). Lets the agent answer "status of t1?" with a direct
        # lookup instead of a filesystem scan. No constructor wiring.
        registry.register(TaskStatusTool())
```
Add the import at the top with the other tool imports.

- [ ] **Step 8 — verify pass**

Run: `uv run pytest tests/tools/tasks/test_task_status.py -v`
Expected: all PASS.

- [ ] **Step 9 — write the failing journey (offering + resolution)**

`tests/journeys/test_task_status_journey.py`. Mirror `test_skill_injection_journey.py`'s tool-catalog capture (`provider.presented_tool_names`). Seed a durable task; script the provider to call `task_status(task_id="t1")`; assert (a) `"task_status" in provider.presented_tool_names` (the tool is OFFERED), and (b) the turn's result reflects the real seeded status (resolved via the store, not a hallucination/shell-scan).

- [ ] **Step 10 — run, verify pass**

Run: `uv run pytest tests/journeys/test_task_status_journey.py -v`
Expected: PASS.

- [ ] **Step 11 — lint, type, commit**

```bash
uv run ruff check src/ && uv run mypy src/stackowl/tools
git add src/stackowl/tools/tasks/ tests/tools/tasks/ src/stackowl/tools/registry.py tests/journeys/test_task_status_journey.py
git commit -m "feat(tools): add read-only owner-scoped task_status lookup tool"
```

---

## Story #1 — Flatten GFM tables for Telegram & Slack (shared helper)

**Why:** Neither `to_telegram_markdownv2` nor `to_slack_mrkdwn` handles GFM tables; `|`/`-` get backslash-escaped (Telegram) or passed through (Slack), so tables render broken regardless of any stored "no tables" preference (which is only soft prompt text). No shared channel-format util exists today.

**Fix:** A deterministic `flatten_gfm_tables(text)` that converts a GFM pipe-table (header row + `---` delimiter row + body) into a fenced, column-aligned text block (both converters stash ``` fences verbatim, so it renders cleanly on both). Anchor detection on the **header+delimiter line pair**, never a lone `-` (don't eat horizontal rules / list dashes). Call it at the top of both converters, before their stash/escape phases.

**Files:**
- Create: `src/stackowl/channels/_format.py`
- Modify: `src/stackowl/channels/telegram/formatter.py` (call in `to_telegram_markdownv2`, after sentinel-strip ~line 118, before code-protect)
- Modify: `src/stackowl/channels/slack/helpers.py` (call in `to_slack_mrkdwn`, after sentinel-strip ~line 145, before stash)
- Test: `tests/channels/test_format_tables.py` (new, helper unit)
- Test: extend `tests/channels/telegram/test_telegram_gfm.py` and `tests/channels/slack/test_slack_mrkdwn.py`

**Interfaces:**
- Produces: `channels._format.flatten_gfm_tables(text: str) -> str`.

- [ ] **Step 1 — write the failing helper test**

`tests/channels/test_format_tables.py`:
```python
from stackowl.channels._format import flatten_gfm_tables


def test_pipe_table_flattened_to_fence_with_cells() -> None:
    src = "| Name | Age |\n| --- | --- |\n| Ann | 30 |\n| Bob | 25 |"
    out = flatten_gfm_tables(src)
    assert "```" in out                      # rendered as a verbatim fenced block
    for cell in ("Name", "Age", "Ann", "30", "Bob", "25"):
        assert cell in out
    # the raw GFM delimiter row must not survive as a stray pipe line
    assert "| --- |" not in out


def test_lone_dash_and_hr_untouched() -> None:
    assert flatten_gfm_tables("- a list item") == "- a list item"
    assert flatten_gfm_tables("text\n\n---\n\nmore") == "text\n\n---\n\nmore"


def test_non_table_text_unchanged() -> None:
    assert flatten_gfm_tables("just a sentence") == "just a sentence"
```

- [ ] **Step 2 — run, verify it fails**

Run: `uv run pytest tests/channels/test_format_tables.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3 — implement the helper**

`src/stackowl/channels/_format.py`:
```python
"""Shared, channel-agnostic markdown normalisation.

GFM pipe-tables are not representable in Telegram MarkdownV2 or Slack mrkdwn;
left alone their ``|``/``-`` chars render as broken escaped text. We flatten a
detected table into a fenced, column-aligned block, which BOTH channel
converters pass through verbatim via their code-fence stash phase. Detection
anchors on a header row immediately followed by a delimiter row (cells of only
``-``/``:``/spaces/pipes) — a lone ``-`` or a horizontal rule is never a table.
"""
from __future__ import annotations

import re

_DELIM_CELL = re.compile(r"^\s*:?-{1,}:?\s*$")


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") or "|" in line.strip()


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_delimiter_row(line: str) -> bool:
    cells = _cells(line)
    return len(cells) >= 1 and all(_DELIM_CELL.match(c) for c in cells)


def flatten_gfm_tables(text: str) -> str:
    if "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        # A table = header row, then a delimiter row, then >=0 body rows.
        if (
            i + 1 < n
            and _is_table_row(lines[i])
            and not _is_delimiter_row(lines[i])
            and _is_table_row(lines[i + 1])
            and _is_delimiter_row(lines[i + 1])
        ):
            header = _cells(lines[i])
            body: list[list[str]] = []
            j = i + 2
            while j < n and _is_table_row(lines[j]) and not _is_delimiter_row(lines[j]):
                body.append(_cells(lines[j]))
                j += 1
            out.append(_render_block(header, body))
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _render_block(header: list[str], body: list[list[str]]) -> str:
    rows = [header, *body]
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    cols = [max(len(norm[r][c]) for r in range(len(norm))) for c in range(width)]
    def fmt(r: list[str]) -> str:
        return "  ".join(r[c].ljust(cols[c]) for c in range(width)).rstrip()
    lines = [fmt(header), "  ".join("-" * cols[c] for c in range(width)).rstrip()]
    lines += [fmt(r) for r in body]
    return "```\n" + "\n".join(lines) + "\n```"
```

- [ ] **Step 4 — verify pass**

Run: `uv run pytest tests/channels/test_format_tables.py -v`
Expected: PASS.

- [ ] **Step 5 — write failing channel-integration tests**

Add to `tests/channels/telegram/test_telegram_gfm.py`:
```python
def test_table_does_not_leak_raw_pipes() -> None:
    src = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    out = to_telegram_markdownv2(src)
    assert "1" in out and "2" in out and "A" in out
    assert r"\|" not in out          # no escaped-pipe table wreckage
```
Add the mirror to `tests/channels/slack/test_slack_mrkdwn.py` against `to_slack_mrkdwn`.

- [ ] **Step 6 — run, verify they fail**

Run: `uv run pytest tests/channels/telegram/test_telegram_gfm.py tests/channels/slack/test_slack_mrkdwn.py -v`
Expected: the two new tests FAIL (raw escaped pipes present).

- [ ] **Step 7 — wire the helper into both converters**

In `src/stackowl/channels/telegram/formatter.py` `to_telegram_markdownv2`, right after the sentinel-strip (~line 118) and before the code-protect step (~line 145):
```python
    from stackowl.channels._format import flatten_gfm_tables
    text = flatten_gfm_tables(text)
```
In `src/stackowl/channels/slack/helpers.py` `to_slack_mrkdwn`, after its sentinel-strip (~line 145) and before the stash phase:
```python
    from stackowl.channels._format import flatten_gfm_tables
    text = flatten_gfm_tables(text)
```
(Use the actual local variable name each function carries at that point — `text` in Telegram; confirm Slack's pre-stash variable name.)

- [ ] **Step 8 — verify pass + no formatter regressions**

Run: `uv run pytest tests/channels/ -v`
Expected: new tests PASS; all existing formatter tests green.

- [ ] **Step 9 — lint, type, commit**

```bash
uv run ruff check src/ && uv run mypy src/stackowl/channels
git add src/stackowl/channels/_format.py src/stackowl/channels/telegram/formatter.py src/stackowl/channels/slack/helpers.py tests/channels/test_format_tables.py tests/channels/telegram/test_telegram_gfm.py tests/channels/slack/test_slack_mrkdwn.py
git commit -m "fix(channels): flatten GFM tables to fenced blocks for Telegram & Slack"
```

- [ ] **Step 10 (optional hardening) — Telegram plain-text fallback on MarkdownV2 parse error**

In `src/stackowl/channels/telegram/adapter.py` `send_text` (~lines 259-263), wrap the `send_message(..., parse_mode="MarkdownV2")` call so a Telegram 400 (bad MarkdownV2) is caught, **logged** (`log.telegram.error`), and retried once with `parse_mode=None`. No silent swallow. Add a unit test with a fake bot that raises on the first MarkdownV2 send and succeeds on the plain retry. Commit separately:
```bash
git commit -m "fix(telegram): plain-text fallback (logged) when MarkdownV2 send is rejected"
```

---

## Self-Review

**Spec coverage:** Each of the four self-diagnosed problems maps to a story — #2 (false history) → Story #2; #4 (over-apology) → Story #4; #3 (broad scans) → Story #3; #1 (tables) → Story #1. Trust-first order #2 → #4 → #3 → #1 preserved. ✔

**Grounding corrections folded in:** reflections have no `session_id` (so #2 is a call-site admission gate, not a store change); the critical-failure apology is already floored (so #4 targets the model's own prose via deterministic history-dedup, not an NL classifier); registry classmethod is `with_defaults`; `_ScriptedProvider.complete_with_tools` doesn't capture `calls` (Story #4 Step 5 closes that fixture gap). ✔

**Type consistency:** `intent_classified` (bool) defined in Story #2 Step 3, used in `_should_surface_failure_history` (Step 11). `_dedup_assistant_history` signature consistent between unit test and `_gather_history` use. `TaskStatusTool` name `"task_status"` consistent across tool, registration, and journey. `flatten_gfm_tables` signature consistent across helper, both wirings, and tests. ✔

**Open verification for the implementer (confirm against live code, don't assume):** exact import path of `Message`; the precise `state.evolve(...)` line in triage that applies the router verdict; Slack's local variable name at the pre-stash point; whether `ReflectionStore` exposes a public `append`/writer to seed the journey (else seed via the writer handler). Each is a 1-line lookup; the verbatim grounding in this session's transcript has the surrounding code.

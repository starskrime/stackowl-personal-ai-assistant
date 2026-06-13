# No Dressed-Up Give-Up (Severity-Aware Honesty Floor) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee a turn never ships a give-up dressed as delivery: detect (structurally, severity-aware) when a consequential action was attempted-and-failed with no consequential success, steer the model to self-extend or escalate honestly, and replace any dressed-up draft with an honest floor when it won't recover.

**Architecture:** A turn-scoped tool-outcome ledger (ContextVar, like the shipped `recovery_context`) records `(name, action_severity, success)` per dispatched tool. A new severity-aware signal `is_unachieved_consequential_giveup` feeds the structural veto (→ a `CAPABILITY_GAP_DIRECTIVE` nudge) and a pre-delivery terminal step that replaces the draft with the honest floor on exhaustion. `decide_nudge`/`apply_structural_veto` stay pure (counts passed in); the impure ledger read happens at the provider call site and the delivery chokepoint.

**Tech Stack:** Python 3.13, contextvars, the shipped supervisor veto + `synthesize_floor`/`synthesize_from_calls` + `tool_build` + both backends, pytest.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/infra/tool_outcome_ledger.py` | **Create** | turn-scoped ledger: `record_tool_outcome` + `consequential_tally` + bind/reset |
| `src/stackowl/pipeline/steps/execute.py` | Modify (`_dispatch`, `_try_substitute`) | record each tool outcome into the ledger |
| `src/stackowl/pipeline/backends/asyncio_backend.py` | Modify | bind/reset ledger per turn |
| `src/stackowl/pipeline/backends/langgraph_backend.py` | Modify | bind/reset ledger per turn |
| `src/stackowl/pipeline/persistence.py` | Modify | `is_unachieved_consequential_giveup` + `CAPABILITY_GAP_DIRECTIVE` + hardened judge prompt |
| `src/stackowl/pipeline/supervisor.py` | Modify (`apply_structural_veto`, `decide_nudge`) | accept consequential counts; return capability-gap directive |
| `src/stackowl/providers/openai_provider.py` + `anthropic_provider.py` | Modify (enforce loop) | read ledger, pass consequential counts to `decide_nudge` |
| `src/stackowl/pipeline/giveup_floor.py` | **Create** | `surface_consequential_giveup_floor(state)` terminal replace step |
| backends (again) | Modify | call the terminal floor step at the pre-delivery chokepoint |
| tests (units + journey) | **Create** | per task below |

---

## Task 1: Turn-scoped tool-outcome ledger

**Files:**
- Create: `src/stackowl/infra/tool_outcome_ledger.py`
- Test: `tests/infra/test_tool_outcome_ledger.py`

**Context:** Mirror the shipped `src/stackowl/infra/recovery_context.py` ContextVar idiom. Records each dispatched tool's `(name, action_severity, success)`; exposes a consequential tally. Lives in `infra/` so execute (records) and supervisor (tally) both reach it without a layer inversion. `action_severity` values: `"read"|"write"|"consequential"`.

- [ ] **Step 1: Write the failing test** `tests/infra/test_tool_outcome_ledger.py`:
```python
from stackowl.infra import tool_outcome_ledger as tol


def test_consequential_tally_counts_only_consequential_and_write():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="read_file", action_severity="read", success=False)   # read failure ignored
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
        tol.record_tool_outcome(name="write_file", action_severity="write", success=False)
        cons_f, cons_s = tol.consequential_tally()
        assert cons_f == 2   # consequential + write failures
        assert cons_s == 0
    finally:
        tol.reset(token)


def test_consequential_success_counts():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=True)
        cons_f, cons_s = tol.consequential_tally()
        assert cons_f == 1 and cons_s == 1
    finally:
        tol.reset(token)


def test_unbound_is_noop():
    assert tol.record_tool_outcome(name="x", action_severity="consequential", success=False) is None
    assert tol.consequential_tally() == (0, 0)


def test_reset_clears():
    token = tol.bind()
    tol.record_tool_outcome(name="x", action_severity="consequential", success=False)
    tol.reset(token)
    assert tol.consequential_tally() == (0, 0)
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/infra/test_tool_outcome_ledger.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement** `src/stackowl/infra/tool_outcome_ledger.py`:
```python
"""Turn-scoped ledger of dispatched tool outcomes (name, severity, success).

Mirrors the ``recovery_context`` ContextVar idiom. Lets the give-up detection be
SEVERITY-AWARE: a failed CONSEQUENTIAL/WRITE action with no consequential success
means the user's effect was not achieved — a give-up no matter how confident the
draft. The backend binds a fresh ledger per turn and resets it in a finally.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from stackowl.infra.observability import log

_EFFECTFUL = {"write", "consequential"}


@dataclass(frozen=True)
class ToolOutcome:
    name: str
    action_severity: str
    success: bool


_outcomes: ContextVar[tuple[ToolOutcome, ...] | None] = ContextVar(
    "tool_outcomes", default=None,
)


def bind() -> Token[tuple[ToolOutcome, ...] | None]:
    return _outcomes.set(())


def reset(token: Token[tuple[ToolOutcome, ...] | None]) -> None:
    _outcomes.reset(token)


def record_tool_outcome(*, name: str, action_severity: str, success: bool) -> None:
    """Record one dispatched tool's outcome. No-op (logged) when unbound; never raises."""
    current = _outcomes.get()
    if current is None:
        log.engine.debug(
            "[tool_outcome_ledger] record: unbound turn — ignoring",
            extra={"_fields": {"name": name}},
        )
        return
    _outcomes.set((*current, ToolOutcome(name=name, action_severity=action_severity, success=success)))


def get_outcomes() -> tuple[ToolOutcome, ...]:
    """Non-consuming read of this turn's recorded outcomes (empty if none/unbound)."""
    return _outcomes.get() or ()


def consequential_tally() -> tuple[int, int]:
    """Return (consequential_failures, consequential_successes) over write+consequential outcomes."""
    outcomes = get_outcomes()
    cons_f = sum(1 for o in outcomes if o.action_severity in _EFFECTFUL and not o.success)
    cons_s = sum(1 for o in outcomes if o.action_severity in _EFFECTFUL and o.success)
    return cons_f, cons_s
```
(Task 4's `surface_consequential_giveup_floor` calls `tool_outcome_ledger.get_outcomes()` directly — no `hasattr` guard needed.)

- [ ] **Step 4: Run/verify** — `uv run pytest tests/infra/test_tool_outcome_ledger.py -q` (4 passed); `uv run mypy src/stackowl/infra/tool_outcome_ledger.py` (clean); `uv run ruff check` both. (Check `tests/infra/__init__.py` convention — `recovery_context`'s test dir already has one.)

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/infra/tool_outcome_ledger.py tests/infra/test_tool_outcome_ledger.py
git commit -m "feat(v2): turn-scoped tool-outcome ledger (severity-aware) for give-up detection"
```

---

## Task 2: Record tool outcomes at dispatch + bind/reset in backends

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (`_dispatch` after `ledger_guard`; `_try_substitute` on sibling success)
- Modify: `src/stackowl/pipeline/backends/asyncio_backend.py`, `langgraph_backend.py`
- Test: covered by Task 6 journey; plus a focused dispatch test.

**Context:** In `_dispatch` (execute.py ~line 618), `tr = await ledger_guard(name, args, t.manifest.action_severity, ...)` gives `tr.success`, and `t.manifest.action_severity` is in hand. Record there. In `_try_substitute`, on sibling success record the sibling's outcome. Backends bind/reset the ledger per turn exactly like `recovery_context` (which they already do — add alongside).

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_dispatch_records_outcome.py` — mirror `tests/pipeline/test_substitution_records_recovery.py`'s harness (it already drives `_dispatch` via `_run_with_tools`). Drive a turn where the model calls a consequential tool that fails; assert `tool_outcome_ledger.consequential_tally()` shows `cons_f >= 1`.
```python
import pytest
from stackowl.infra import tool_outcome_ledger as tol
# reuse the _build_real_dispatch / capability-tool harness from test_substitution_records_recovery.py
# register a CONSEQUENTIAL tool that fails; drive _dispatch; then:
@pytest.mark.asyncio
async def test_failed_consequential_recorded(dispatch_env):
    token = tol.bind()
    try:
        await dispatch_env.dispatch("failing_consequential_tool", {})
        cons_f, cons_s = tol.consequential_tally()
        assert cons_f >= 1 and cons_s == 0
    finally:
        tol.reset(token)
```
> Build `dispatch_env` from the existing substitution-records test harness; register a tool whose `manifest.action_severity == "consequential"` and whose `execute` returns `success=False`. If wiring is heavy, the Task 6 journey covers this end-to-end — but TRY the focused test.

- [ ] **Step 2: Run to verify it fails** — `cons_f` is 0 (not recorded yet).

- [ ] **Step 3: Implement.**
(a) execute.py — add import `from stackowl.infra import tool_outcome_ledger`. After `tr = await ledger_guard(name, args, t.manifest.action_severity, lambda: t(**args))` in `_dispatch`, record:
```python
        tool_outcome_ledger.record_tool_outcome(
            name=name, action_severity=t.manifest.action_severity, success=tr.success,
        )
```
In `_try_substitute`, after `sib_result = await ledger_guard(...)`, record the sibling outcome:
```python
        tool_outcome_ledger.record_tool_outcome(
            name=sibling_name, action_severity=sib.manifest.action_severity, success=sib_result.success,
        )
```
(b) Both backends — add `from stackowl.infra import tool_outcome_ledger` and bind/reset alongside the existing `recovery_context.bind()/reset()`:
```python
        ledger_token = tool_outcome_ledger.bind()   # after recovery_context.bind()
        ...
        finally:
            tool_outcome_ledger.reset(ledger_token)   # alongside recovery_context.reset(...)
```

- [ ] **Step 4: Run/verify** — focused test passes; `uv run pytest tests/journeys/test_self_heal_substitution.py -q` (no regression); mypy on the 3 files; ruff.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/pipeline/steps/execute.py src/stackowl/pipeline/backends/asyncio_backend.py src/stackowl/pipeline/backends/langgraph_backend.py tests/pipeline/test_dispatch_records_outcome.py
git commit -m "feat(v2): record per-tool (name,severity,success) into the turn ledger; bind/reset in backends"
```

---

## Task 3: Severity-aware veto + capability-gap directive (nudge side)

**Files:**
- Modify: `src/stackowl/pipeline/persistence.py` (`is_unachieved_consequential_giveup`, `CAPABILITY_GAP_DIRECTIVE`)
- Modify: `src/stackowl/pipeline/supervisor.py` (`apply_structural_veto`, `decide_nudge` — accept counts)
- Modify: `src/stackowl/providers/openai_provider.py` + `anthropic_provider.py` (read ledger, pass counts)
- Test: `tests/pipeline/test_consequential_giveup_veto.py`

**Context:** `apply_structural_veto(*, judge_directive, all_calls, draft)` returns a directive on a give-up. `decide_nudge` wraps it (both pure). Add severity-aware counts as params (keeping purity), and a new directive. The provider's `_enforce` (which already calls `decide_nudge`) reads the ledger and passes the counts.

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_consequential_giveup_veto.py`:
```python
from stackowl.pipeline.supervisor import apply_structural_veto
from stackowl.pipeline.persistence import CAPABILITY_GAP_DIRECTIVE, is_unachieved_consequential_giveup


def test_signal_fires_on_unachieved_consequential():
    assert is_unachieved_consequential_giveup(cons_failures=1, cons_successes=0) is True
    assert is_unachieved_consequential_giveup(cons_failures=1, cons_successes=1) is False
    assert is_unachieved_consequential_giveup(cons_failures=0, cons_successes=0) is False


def test_veto_returns_capability_gap_directive_when_consequential_unachieved():
    # The dressed-up case: a trivial tool "succeeded" + a SUBSTANTIVE draft → the
    # OLD zombie signal does NOT fire (successes>0, draft substantive). The NEW
    # consequential signal must fire.
    directive = apply_structural_veto(
        judge_directive=None,
        all_calls=[{"name": "write_file", "failed": False}],   # a trivial success
        draft="I have built the full agentic bridge for you. Here are the steps...",
        cons_failures=1, cons_successes=0,
    )
    assert directive == CAPABILITY_GAP_DIRECTIVE


def test_veto_silent_when_consequential_succeeded():
    directive = apply_structural_veto(
        judge_directive=None,
        all_calls=[{"name": "send_email", "failed": False}],
        draft="Sent it.",
        cons_failures=1, cons_successes=1,
    )
    assert directive is None


def test_explicit_judge_directive_still_wins():
    from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE
    directive = apply_structural_veto(
        judge_directive=PERSISTENCE_DIRECTIVE, all_calls=[], draft="x",
        cons_failures=1, cons_successes=0,
    )
    assert directive == PERSISTENCE_DIRECTIVE
```

- [ ] **Step 2: Run to verify it fails** — ImportError / unexpected kwarg.

- [ ] **Step 3: Implement.**
(a) `persistence.py` — add the signal + directive:
```python
def is_unachieved_consequential_giveup(*, cons_failures: int, cons_successes: int) -> bool:
    """Severity-aware give-up: a consequential/write action failed and NONE succeeded.

    The user's consequential outcome was not achieved — a give-up regardless of
    trivial successes or how confident/substantive the draft reads. Catches the
    dressed-up case the zombie signal misses.
    """
    return cons_failures >= 1 and cons_successes == 0


CAPABILITY_GAP_DIRECTIVE = (
    "A consequential action you attempted FAILED and is NOT done. Do ONE of: "
    "(a) build the missing capability with the tool_build tool and use it to "
    "actually perform the action; (b) achieve the outcome via a different working "
    "capability; or (c) tell the user plainly that you could NOT do it and the "
    "exact blocker. Do NOT give the user manual steps to do it themselves, and do "
    "NOT claim it is done or that you 'built' it when the action did not complete."
)
```
(b) `supervisor.py` — extend `apply_structural_veto` and `decide_nudge` with `cons_failures: int = 0, cons_successes: int = 0` (keyword, defaults preserve old behavior). In `apply_structural_veto`, after the existing zombie check returns nothing, add:
```python
    if is_unachieved_consequential_giveup(cons_failures=cons_failures, cons_successes=cons_successes):
        log.engine.info(
            "supervisor.veto: consequential outcome not achieved — capability-gap directive",
            extra={"_fields": {"cons_failures": cons_failures, "cons_successes": cons_successes}},
        )
        return CAPABILITY_GAP_DIRECTIVE
    return None
```
(import `CAPABILITY_GAP_DIRECTIVE, is_unachieved_consequential_giveup` from persistence). `decide_nudge` passes the two counts through to `apply_structural_veto`. Keep both functions pure.
(c) Both providers' `_enforce` — read the ledger and pass counts:
```python
            from stackowl.infra import tool_outcome_ledger
            _cf, _cs = tool_outcome_ledger.consequential_tally()
            directive, nudge_budget, calls_at_last_nudge = decide_nudge(
                ..., cons_failures=_cf, cons_successes=_cs,
            )
```

- [ ] **Step 4: Run/verify** — `uv run pytest tests/pipeline/test_consequential_giveup_veto.py -q` (4 passed); existing supervisor + provider tests: `uv run pytest tests/ -q -k "supervisor or decide_nudge or veto or enforce"` (no regression — defaults preserve behavior); mypy on the 4 files; ruff.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/pipeline/persistence.py src/stackowl/pipeline/supervisor.py src/stackowl/providers/openai_provider.py src/stackowl/providers/anthropic_provider.py tests/pipeline/test_consequential_giveup_veto.py
git commit -m "feat(v2): severity-aware consequential-give-up veto + CAPABILITY_GAP_DIRECTIVE"
```

---

## Task 4: Honest floor replaces the draft on a consequential give-up (terminal side)

**Files:**
- Create: `src/stackowl/pipeline/giveup_floor.py` (`surface_consequential_giveup_floor`)
- Modify: both backends (call it at the pre-delivery chokepoint)
- Test: `tests/pipeline/test_giveup_floor_replace.py`

**Context:** A persistent dresser-upper's draft is non-empty, so the existing never-empty floor does NOT replace it. At the pre-delivery chokepoint (where `surface_recovery`/`surface_critical_failure` run), if the ledger shows an unachieved consequential outcome, REPLACE the responses with the honest floor built from the failed consequential tool. Read `synthesize_from_calls`/`synthesize_floor` (supervisor.py) for the floor builder; it takes the failed capability + error.

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_giveup_floor_replace.py`:
```python
import pytest
from stackowl.infra import tool_outcome_ledger as tol
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(text):
    return PipelineState(trace_id="t", session_id="s", input_text="send the email", channel="cli",
                         owl_name="secretary", pipeline_step="deliver",
                         responses=(ResponseChunk(content=text, is_final=False, chunk_index=0,
                                                  trace_id="t", owl_name="secretary"),))


@pytest.mark.asyncio
async def test_replaces_dressed_up_draft_with_honest_floor():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
        s = _state("I have built the full agentic bridge for you. Here are the steps...")
        out = await surface_consequential_giveup_floor(s)
        delivered = "".join(c.content for c in out.responses)
        assert "built the full agentic bridge" not in delivered   # the excuse is GONE
        assert "could not" in delivered.lower() or "couldn" in delivered.lower()  # honest floor
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_no_replace_when_consequential_succeeded():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=True)
        s = _state("Done — sent the email.")
        out = await surface_consequential_giveup_floor(s)
        assert out.responses == s.responses   # untouched
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_no_replace_when_no_consequential_attempt():
    token = tol.bind()
    try:
        s = _state("Here's the answer to your question.")   # no consequential tool recorded
        out = await surface_consequential_giveup_floor(s)
        assert out.responses == s.responses
    finally:
        tol.reset(token)
```

- [ ] **Step 2: Run to verify it fails** — ModuleNotFoundError.

- [ ] **Step 3: Implement** `src/stackowl/pipeline/giveup_floor.py`:
```python
"""surface_consequential_giveup_floor — replace a dressed-up give-up with an honest floor.

When the turn-ledger shows a consequential/write action was attempted and FAILED
with NO consequential success (the outcome was not achieved), the model's draft
cannot be trusted to be honest about it — so REPLACE the responses with the
deterministic honest floor naming the failed capability. Runs pre-delivery in both
backends. Never raises.
"""

from __future__ import annotations

from stackowl.infra import tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.pipeline.persistence import is_unachieved_consequential_giveup
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.supervisor import synthesize_floor


async def surface_consequential_giveup_floor(state: PipelineState) -> PipelineState:
    try:
        cf, cs = tool_outcome_ledger.consequential_tally()
        if not is_unachieved_consequential_giveup(cons_failures=cf, cons_successes=cs):
            return state
        # The outcome was not achieved; the draft may dress that up. Replace with the
        # honest floor naming the failed consequential capability.
        outcomes = tool_outcome_ledger.get_outcomes()
        failed_name = next(
            (o.name for o in outcomes if o.action_severity in {"write", "consequential"} and not o.success),
            None,
        )
        floor_text = synthesize_floor(
            goal=state.input_text, error=None, attempts=None, partial=None,
            failed_capability=failed_name,
        )
        log.engine.info(
            "[giveup_floor] replacing draft with honest floor (consequential outcome not achieved)",
            extra={"_fields": {"trace_id": state.trace_id, "failed_capability": failed_name}},
        )
        chunk = ResponseChunk(content=floor_text, is_final=False, chunk_index=0,
                              trace_id=state.trace_id, owl_name=state.owl_name)
        return state.evolve(responses=(chunk,))   # REPLACE (not append) — the draft is untrusted
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[giveup_floor] failed — leaving response untouched",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
```
> Add a `get_outcomes() -> tuple[ToolOutcome, ...]` reader to `tool_outcome_ledger` (returns `_outcomes.get() or ()`) so the floor can name the precise failed capability. (Add it in Task 1's module + a quick test, OR add here — simplest: add to the ledger module now.)

Wire into both backends at the chokepoint, BEFORE `surface_critical_failure` (so the honest floor is the response the critical-failure cascade then sees as usable):
```python
    current = await surface_applied_lessons(current)
    current = await surface_recovery(current)
    current = await surface_consequential_giveup_floor(current)   # ADD
    current = await surface_critical_failure(current, self._services)
```
(and the langgraph `_deliver_with_surfacing` equivalent.)

- [ ] **Step 4: Run/verify** — `uv run pytest tests/pipeline/test_giveup_floor_replace.py -q` (3 passed); `uv run pytest tests/journeys/test_self_heal_invariant.py tests/journeys/test_recovery_explainability_journey.py -q` (no regression); mypy on the new file + backends; ruff.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/pipeline/giveup_floor.py src/stackowl/infra/tool_outcome_ledger.py src/stackowl/pipeline/backends/asyncio_backend.py src/stackowl/pipeline/backends/langgraph_backend.py tests/pipeline/test_giveup_floor_replace.py
git commit -m "feat(v2): honest floor replaces a dressed-up give-up when consequential outcome unachieved"
```

---

## Task 5: Harden the give-up judge prompt (variant ii)

**Files:**
- Modify: `src/stackowl/pipeline/persistence.py` (`_build_messages` GAVE-UP section)
- Test: `tests/pipeline/test_giveup_judge_prompt.py`

**Context:** `_build_messages` lists the GAVE-UP (delivered=false) shapes. Add the hand-back shape. This is best-effort (weak judge); the structural floor (Tasks 3-4) is the guarantee.

- [ ] **Step 1: Write the failing test** — assert the built judge prompt text includes the hand-back shape:
```python
from stackowl.pipeline.persistence import _build_messages

def test_judge_prompt_flags_handback_shape():
    msgs = _build_messages(parent_ask="send an email", draft="I built the bridge for you; here are the steps", tools_tried=[])
    prompt = " ".join(m.content for m in msgs)
    low = prompt.lower()
    assert "manual steps" in low or "do it themselves" in low or "built" in low
```
> Confirm `_build_messages`'s real signature first (params may differ — read it) and adapt the call.

- [ ] **Step 2: Run to verify it fails** — the phrase isn't in the prompt.

- [ ] **Step 3: Implement** — in the GAVE-UP (delivered=false) bullet list inside `_build_messages`, add a line:
```python
            "- HANDS THE TASK BACK: gives the user manual steps/instructions to do "
            "it themselves, or claims to have 'built' or 'set up' something for the "
            "user INSTEAD OF performing the requested action.\n"
```
(Insert into the existing GAVE-UP enumeration string; match the surrounding format.)

- [ ] **Step 4: Run/verify** — test passes; existing persistence/judge tests green (`uv run pytest tests/ -q -k persistence`); mypy; ruff.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/pipeline/persistence.py tests/pipeline/test_giveup_judge_prompt.py
git commit -m "feat(v2): give-up judge prompt flags the hand-back / built-it-for-you shape"
```

---

## Task 6: Gateway journey (the live-bug regression) + full regression

**Files:**
- Create: `tests/journeys/test_no_dressed_up_giveup_journey.py`

**Context:** End-to-end through the real `AsyncioBackend`. STUDY `tests/journeys/test_self_heal_substitution.py` + `test_recovery_explainability_journey.py` for the harness. Script a provider that calls a CONSEQUENTIAL tool which FAILS, then drafts a dressed-up "I built the bridge for you / here are the steps" reply, and never achieves a consequential success.

- [ ] **Step 1: Write the journey (FR1/FR3/FR4).** Register a consequential tool (`action_severity="consequential"`) that returns `success=False`. The scripted provider calls it via `tool_dispatcher`, then returns the dressed-up draft. Assert the delivered user text:
  - does NOT contain the dressed-up claim ("built the full agentic bridge" / "here are the steps"),
  - IS the honest floor (contains "could not"/"couldn't" + the failed capability name),
  - and (FR3) a capability-gap nudge was issued (caplog: `"capability-gap directive"` or the `CAPABILITY_GAP_DIRECTIVE` text appeared in the loop).
- [ ] **Step 2: Run; confirm PASS.** If the dressed-up claim still ships, the floor-replace isn't wired — STOP/BLOCKED (don't weaken).
- [ ] **Step 3: Control (FR2).** A consequential tool that SUCCEEDS + a normal "done" draft → assert the draft is delivered unchanged (no floor replace, no false positive).
- [ ] **Step 4: Full regression (FR7).** `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/` — report counts; baseline 93 passed / 1 skipped; ZERO regressions. If any prior self-heal/floor/substitution journey regresses, STOP/BLOCKED.
- [ ] **Step 5: Lint + commit.** `uv run ruff check` the journey file.
```bash
git add tests/journeys/test_no_dressed_up_giveup_journey.py
git commit -m "test(v2): no-dressed-up-giveup journey — consequential fail → honest floor, not the excuse"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1 detect→T1+T3+T6; FR2 no-false-positive→T1+T3+T6 control; FR3 directive→T3; FR4 honest floor→T4+T6; FR5 judge hand-back→T5; FR6 severity threaded→T1+T2; FR7 regression→T6. All covered.
- **Placeholder scan:** the `<harness>`/`dispatch_env` notes point at named existing test files to mirror — not deferred logic. The `get_outcomes()` reader is specified (add to Task 1 module). No TBD/TODO.
- **Type consistency:** `record_tool_outcome(*, name, action_severity, success)`, `consequential_tally() -> (int,int)`, `get_outcomes() -> tuple[ToolOutcome,...]`, `is_unachieved_consequential_giveup(*, cons_failures, cons_successes)`, `apply_structural_veto(..., cons_failures=0, cons_successes=0)`, `decide_nudge(..., cons_failures=0, cons_successes=0)`, `CAPABILITY_GAP_DIRECTIVE`, `surface_consequential_giveup_floor(state)`. Consistent across tasks. NOTE: Task 4 uses `get_outcomes` — fold its definition + a line in Task 1's test.

## Risk & containment
- **Floor replaces an honest draft (accepted tradeoff):** documented in spec; the floor is honest. Contained: only fires on unachieved-consequential (a consequential success defeats it).
- **decide_nudge/veto purity:** counts passed as params (defaults preserve behavior); the impure ledger read is at the provider call site + the chokepoint. Existing supervisor/decide_nudge tests stay green (T3 regression).
- **Severity availability:** recorded at `_dispatch` where `manifest.action_severity` is in hand (T2) — no provider-loop threading.
- **Composition:** pairs with the paused bounded-turn slice (prompt loop termination) — without it the floor still fires at the iteration cap. Flag at merge.
- **Rollback:** additive (ledger + signal + directive + terminal step + judge line); revert leaves the existing zombie veto + floor intact.

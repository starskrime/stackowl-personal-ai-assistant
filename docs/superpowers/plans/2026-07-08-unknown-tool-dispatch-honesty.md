# Unknown-Tool Dispatch Honesty Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a call to a nonexistent tool name flow through the exact same failure-marker/ledger/circuit-breaker machinery every other tool failure already uses, so the platform's existing self-heal/persistence-nudge system can actually engage instead of silently treating "capability doesn't exist" as an ordinary non-failed result.

**Architecture:** One-line-shaped fix at the single chokepoint (`_dispatch` in `execute.py`) where an unknown tool name is detected. No new subsystem — reuse the exact `TOOL_FAILED_MARKER` / `tool_outcome_ledger.record_tool_outcome` / `TurnProgressTracker.record_no_progress` pattern already used two branches below for the "missing required parameter" pre-execution refusal (`execute.py:1296-1315`).

**Tech Stack:** Python, pytest, existing pipeline modules (`stackowl.pipeline.steps.execute`, `stackowl.pipeline.persistence`, `stackowl.infra.tool_outcome_ledger`, `stackowl.pipeline.progress_tracker`).

## Global Constraints

- Root-cause fix only, minimal diff — change only the exact lines needed (`feedback_minimal_code_changes`).
- Never remove or weaken an existing capability while fixing this (tonyStyle gate).
- `uv run pytest <affected test path>` (never the full suite), `uv run ruff check src/`, `uv run mypy src/` must stay green on touched files.
- No hardcoded English/domain phrase matching — this bug is structural (a missing marker), the fix must stay structural too.

---

### Task 1: Mark unknown-tool dispatch as a real, ledger'd failure

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py:1207-1210`
- Test: `tests/pipeline/test_dispatch_unknown_tool.py` (new)

**Interfaces:**
- Consumes: `tool_registry.get(name)` (existing, returns `Tool | None`), `tool_outcome_ledger.record_tool_outcome(*, name, action_severity, success, side_effect_committed=..., verified=..., effect_class=..., error=...)` (existing, `src/stackowl/infra/tool_outcome_ledger.py:84`), `progress.record_no_progress(name)` (existing `TurnProgressTracker` method, already used at `execute.py:1309`, `1356`, `1393`), `TOOL_FAILED_MARKER` (existing constant, already imported at `execute.py:39`).
- Produces: nothing new — this task only changes what one existing branch returns/records. Every downstream consumer (`tally_tool_outcomes`, `is_structural_giveup`, `summarize_tool_outcomes`, `TurnProgressTracker.is_open`) already exists and needs no change.

- [ ] **Step 1: Write the failing test**

Create `tests/pipeline/test_dispatch_unknown_tool.py`:

```python
"""Unknown-tool dispatch must be a REAL, ledger'd failure — not a silent
non-failed string — so the existing structural give-up / circuit-breaker /
persistence-nudge machinery can see it and steer the model toward building
the missing capability instead of looping or giving up silently.
"""
from __future__ import annotations

import pytest

from stackowl.infra import tool_outcome_ledger
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
from tests.pipeline.test_dispatch_substitution import _FakeRegistry, _build_real_dispatch


@pytest.mark.asyncio
async def test_unknown_tool_dispatch_carries_failed_marker(monkeypatch):
    """The dispatcher's honest failure marker must be present so the provider
    layer marks this call ``failed=True`` (closing the LLM-judge blind spot:
    without the marker, ``summarize_tool_outcomes`` renders this as ``(ok)``)."""
    reg = _FakeRegistry([])
    dispatch = await _build_real_dispatch(monkeypatch, __import__(
        "stackowl.pipeline.steps.execute", fromlist=["_run_with_tools"],
    ), reg)

    out = await dispatch("nonexistent_tool_xyz", {})

    assert out.startswith(TOOL_FAILED_MARKER)


@pytest.mark.asyncio
async def test_unknown_tool_dispatch_records_ledger_outcome(monkeypatch):
    """The unknown-tool call must land in the turn-scoped ledger as a
    non-effectful failure (side_effect_committed=False — nothing ran) so it is
    visible for observability without being misread as a failed consequential
    effect."""
    reg = _FakeRegistry([])
    dispatch = await _build_real_dispatch(monkeypatch, __import__(
        "stackowl.pipeline.steps.execute", fromlist=["_run_with_tools"],
    ), reg)

    token = tool_outcome_ledger.bind()
    try:
        await dispatch("nonexistent_tool_xyz", {})
        outcomes = tool_outcome_ledger.get_outcomes()
    finally:
        tool_outcome_ledger.reset(token)

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.name == "nonexistent_tool_xyz"
    assert o.success is False
    assert o.side_effect_committed is False
    # Not effectful (side_effect_committed=False) — must not falsely trip the
    # consequential-give-up path meant for real write/consequential attempts.
    cf, cs = tool_outcome_ledger.consequential_tally()
    assert cf == 0 and cs == 0


@pytest.mark.asyncio
async def test_unknown_tool_repeat_trips_circuit_breaker(monkeypatch):
    """Calling the SAME nonexistent tool repeatedly must trip the existing
    TurnProgressTracker circuit breaker (same containment as any other
    repeatedly-failing tool) instead of looping unbounded."""
    reg = _FakeRegistry([])
    dispatch = await _build_real_dispatch(monkeypatch, __import__(
        "stackowl.pipeline.steps.execute", fromlist=["_run_with_tools"],
    ), reg)

    for _ in range(4):
        await dispatch("nonexistent_tool_xyz", {})

    # By the 5th call the circuit-open bounce (a different, shorter refusal
    # string) has replaced the raw "not found" marker path — structural proof
    # the tracker's own state opened, not just "it kept failing".
    out5 = await dispatch("nonexistent_tool_xyz", {})
    assert "not found" not in out5.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_dispatch_unknown_tool.py -v`
Expected: FAIL — `test_unknown_tool_dispatch_carries_failed_marker` fails because `out == "Tool not found: nonexistent_tool_xyz"` (no marker); `test_unknown_tool_dispatch_records_ledger_outcome` fails because `outcomes` is empty (`len(outcomes) == 0`); `test_unknown_tool_repeat_trips_circuit_breaker` fails because the 5th call is still the bare "Tool not found" string (circuit never opens — no progress-tracker signal was ever recorded).

- [ ] **Step 3: Implement the minimal fix**

In `src/stackowl/pipeline/steps/execute.py`, replace lines 1207-1210:

```python
        t = tool_registry.get(name)
        if t is None:
            log.engine.warning("[pipeline] execute: unknown tool in dispatch", extra={"_fields": {"tool": name}})
            return f"Tool not found: {name}"
```

with:

```python
        t = tool_registry.get(name)
        if t is None:
            log.engine.warning("[pipeline] execute: unknown tool in dispatch", extra={"_fields": {"tool": name}})
            # RC1 self-extension fix (2026-07-08) — an unknown tool name is the
            # clearest possible "capability doesn't exist" signal. Previously this
            # returned a bare, un-marked string that bypassed the ledger, the
            # TurnProgressTracker circuit breaker, and the LLM delivery-judge's
            # failed/ok signal (summarize_tool_outcomes) alike — nothing in the
            # platform could tell this apart from an ordinary successful call.
            # Route it through the SAME pre-execution-refusal shape already used
            # for a missing required parameter a few lines below: ledger'd as a
            # non-effectful failure (nothing ran — side_effect_committed=False),
            # counted by the same-tool circuit breaker, and marked with the
            # structural TOOL_FAILED_MARKER so both is_structural_giveup and the
            # LLM judge see a real failure instead of a silent no-op.
            tool_outcome_ledger.record_tool_outcome(
                name=name, action_severity="read", success=False, side_effect_committed=False,
            )
            progress.record_no_progress(name)
            return (
                f"{TOOL_FAILED_MARKER}Tool '{name}' does not exist. Do not call it again — "
                "if this capability is missing, build it with tool_build (or author a "
                "skill) and use the new tool, or use a different existing capability."
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_dispatch_unknown_tool.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Run the full existing dispatch/substitution/persistence suites to confirm no regression**

Run: `uv run pytest tests/pipeline/test_dispatch_substitution.py tests/pipeline/test_substitution_records_recovery.py tests/pipeline/test_execute_floor_invariant.py tests/pipeline/test_lying_success_gate.py -v`
Expected: PASS — no existing test asserts the OLD bare `"Tool not found: {name}"` string (confirmed via grep: the literal only appears in `execute.py` itself), so no existing test should need updating.

- [ ] **Step 6: Lint + type-check touched files**

Run: `uv run ruff check src/stackowl/pipeline/steps/execute.py tests/pipeline/test_dispatch_unknown_tool.py`
Run: `uv run mypy src/stackowl/pipeline/steps/execute.py`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_dispatch_unknown_tool.py docs/superpowers/plans/2026-07-08-unknown-tool-dispatch-honesty.md
git commit -m "fix(pipeline): mark unknown-tool dispatch as a real ledger'd failure"
```

---

## Self-Review

**Spec coverage:** The user's ask ("root cause why platform doesn't build tool/skill instead of redoing itself or giving up with excuses") maps to RC1 from the earlier investigation (`project_self_extend_capability_gap_rootcause` memory): the unknown-tool dispatch bypass. This plan closes RC1 directly. RC4 (loop/repetition containment) is closed as a side effect (the circuit breaker now sees these calls). RC3 (`CAPABILITY_GAP_DIRECTIVE` gating) becomes reachable for the zombie-turn shape via `is_structural_giveup`/`PERSISTENCE_DIRECTIVE`, which already names "build what you need" — no separate change needed there. RC2 (substitution never offers `tool_build`) is deliberately NOT touched: substitution is a distinct, correctly-scoped mechanism for routing among existing capability siblings; the immediate per-call message plus `PERSISTENCE_DIRECTIVE`/`CAPABILITY_GAP_DIRECTIVE` already cover "go build it" without inventing a new rung. RC5 and the follow-up persistence-verification memory confirmed tool_build/skill_build/owl_build reachability and cross-session persistence are NOT broken — out of scope for this plan.

**Placeholder scan:** none found — every step has real code.

**Type consistency:** `record_tool_outcome`'s keyword args (`name`, `action_severity`, `success`, `side_effect_committed`) match the real signature at `tool_outcome_ledger.py:84-86`. `progress.record_no_progress(name)` matches its existing call sites in the same file (`execute.py:1309`, `1356`, `1393`). `TOOL_FAILED_MARKER` is already imported in `execute.py:39`.

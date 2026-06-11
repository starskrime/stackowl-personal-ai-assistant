# Recovery Explainability (Substitution) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministically tell the user when a capability-substitution recovered their turn ("'X' was unavailable, so I used 'Y' to complete this"), and emit one structured per-turn recovery log record for observability.

**Architecture:** A turn-scoped ContextVar carrier in `infra/` records recovery events (machinery-recorded, never model-narrated). The substitution site in `execute.py` records one event on sibling success. A pre-delivery render step appends a localized line for user-visible events, run BEFORE `surface_critical_failure` (so a failed turn isn't annotated). The backend binds/resets the carrier and emits a unified `[recovery] turn summary` log per turn. Mirrors the just-shipped lessons slice (`lesson_context` / `surface_applied_lessons`).

**Tech Stack:** Python 3.13, contextvars, Pydantic-frozen `ResponseChunk`/`PipelineState`, pytest. Reuses `localize_format`, the W3 substitution actuator, both pipeline backends.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/infra/recovery_context.py` | **Create** | `RecoveryEvent` + turn-scoped ContextVar carrier: `bind`/`reset`/`record_recovery`/`get_recovery` |
| `src/stackowl/pipeline/steps/execute.py` | Modify (`_try_substitute`, after line 366) | One `record_recovery(...)` on sibling success |
| `src/stackowl/pipeline/recovery_summary.py` | **Create** | `surface_recovery(state)` render step |
| `src/stackowl/setup/localize.py` | Modify (`_STRINGS`) | `self_heal_recovery_note` key (en/de/fr/es) |
| `src/stackowl/pipeline/backends/asyncio_backend.py` | Modify | bind/reset + render call + unified log |
| `src/stackowl/pipeline/backends/langgraph_backend.py` | Modify | bind/reset + render call + unified log |
| `tests/infra/test_recovery_context.py` | **Create** | carrier unit |
| `tests/pipeline/test_recovery_summary_render.py` | **Create** | render unit |
| `tests/journeys/test_recovery_explainability_journey.py` | **Create** | gateway FR1–FR4 |

---

## Task 1: Turn-scoped recovery ContextVar carrier

**Files:**
- Create: `src/stackowl/infra/recovery_context.py`
- Test: `tests/infra/test_recovery_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/infra/test_recovery_context.py
from stackowl.infra import recovery_context as rc


def test_record_lands_and_get_is_non_consuming():
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="browse_url",
                           recovered_via="http_fetch", user_visible=True)
        first = rc.get_recovery()
        assert len(first) == 1
        e = first[0]
        assert e.kind == "substitution" and e.failed == "browse_url"
        assert e.recovered_via == "http_fetch" and e.user_visible is True
        # non-consuming: a second get returns the same event
        assert len(rc.get_recovery()) == 1
    finally:
        rc.reset(token)


def test_record_without_bind_is_noop():
    assert rc.record_recovery(kind="substitution", failed="a",
                              recovered_via="b", user_visible=True) is None
    assert rc.get_recovery() == ()


def test_multiple_events_accumulate_in_order():
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="a", recovered_via="b", user_visible=True)
        rc.record_recovery(kind="provider_fallback", failed="c", recovered_via="d", user_visible=False)
        evs = rc.get_recovery()
        assert [e.kind for e in evs] == ["substitution", "provider_fallback"]
        assert evs[1].user_visible is False
    finally:
        rc.reset(token)


def test_reset_clears_state():
    token = rc.bind()
    rc.record_recovery(kind="substitution", failed="a", recovered_via="b", user_visible=True)
    rc.reset(token)
    assert rc.get_recovery() == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infra/test_recovery_context.py -q`
Expected: FAIL — `ModuleNotFoundError: stackowl.infra.recovery_context`

- [ ] **Step 3: Write minimal implementation**

```python
# src/stackowl/infra/recovery_context.py
"""Turn-scoped carrier for machinery-recorded recovery events.

Lives in ``infra/`` (the base layer) so any layer can record a recovery WITHOUT
a dependency inversion — mirrors ``infra/trace.py``'s ContextVar idiom. The
backend ``bind()``s a fresh context at turn start and ``reset()``s it in a
``finally``; recovery sites (e.g. capability substitution) call
``record_recovery``; the render step and the per-turn log read via the
NON-consuming ``get_recovery``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from stackowl.infra.observability import log


@dataclass(frozen=True)
class RecoveryEvent:
    """One machinery recovery this turn (recorded by the code that performed it)."""

    kind: str            # e.g. "substitution", "provider_fallback"
    failed: str          # the capability/tool/provider that failed
    recovered_via: str   # what produced the result instead
    detail: str          # optional extra context (may be "")
    user_visible: bool    # True → surfaced to the user; False → log-only


_events: ContextVar[tuple[RecoveryEvent, ...] | None] = ContextVar(
    "recovery_events", default=None,
)


def bind() -> Token[tuple[RecoveryEvent, ...] | None]:
    """Install a fresh empty recovery context for one turn. Returns a reset token."""
    return _events.set(())


def reset(token: Token[tuple[RecoveryEvent, ...] | None]) -> None:
    """Restore the prior recovery context (call in a ``finally``)."""
    _events.reset(token)


def record_recovery(
    *, kind: str, failed: str, recovered_via: str,
    detail: str = "", user_visible: bool,
) -> None:
    """Record a recovery event. No-op (logged) when unbound; never raises."""
    current = _events.get()
    if current is None:
        log.engine.debug(
            "[recovery_context] record_recovery: unbound turn — ignoring",
            extra={"_fields": {"kind": kind, "failed": failed}},
        )
        return
    _events.set((*current, RecoveryEvent(
        kind=kind, failed=failed, recovered_via=recovered_via,
        detail=detail, user_visible=user_visible,
    )))


def get_recovery() -> tuple[RecoveryEvent, ...]:
    """Non-consuming read of this turn's recovery events (empty if none/unbound)."""
    return _events.get() or ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infra/test_recovery_context.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Verify clean + commit**

Run: `uv run mypy src/stackowl/infra/recovery_context.py` and `uv run ruff check src/stackowl/infra/recovery_context.py tests/infra/test_recovery_context.py` (both clean). Check `tests/infra/` exists and matches the `__init__.py` convention of sibling test dirs (create only if siblings have one).

```bash
git add src/stackowl/infra/recovery_context.py tests/infra/test_recovery_context.py
git commit -m "feat(v2): infra/recovery_context — turn-scoped machinery recovery carrier"
```

---

## Task 2: Record a recovery event on substitution success

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (`_try_substitute`, right after the sibling-success check at line ~366, before the localized note is built)
- Test: `tests/pipeline/test_substitution_records_recovery.py` (create)

**Context:** `_try_substitute` (execute.py:300) finds an in-bounds sibling, runs it via `ledger_guard`, and on `sib_result.success` builds a localized observation note and returns it. We add ONE `record_recovery` call on that success path. Variables in scope at line 366: `failed_tool`, `sibling_name`, `trace_id`.

- [ ] **Step 1: Write the failing test**

This is hard to unit-test in isolation (it's a private coroutine with many params). Instead test it at the integration boundary in Task 5's journey. For THIS task, write a focused test that calls `_try_substitute` directly with minimal real collaborators. First READ `tests/journeys/test_self_heal_substitution.py` to copy its `_CapabilityTool` fake + how it builds a registry with a failing primary + a working tagged sibling. Then:

```python
# tests/pipeline/test_substitution_records_recovery.py
import pytest

from stackowl.infra import recovery_context as rc
from stackowl.pipeline.steps.execute import _try_substitute
# Reuse the capability-tool fake pattern from tests/journeys/test_self_heal_substitution.py
# (import or replicate _CapabilityTool + a ToolRegistry with a tagged read/write sibling).


@pytest.mark.asyncio
async def test_substitution_success_records_user_visible_recovery(substitution_registry):
    # substitution_registry: a ToolRegistry where `failed_tool` shares a capability_tag
    # with a working read/write sibling `sibling_tool` (build it mirroring the
    # self-heal substitution journey's setup). `effective` is an in-bounds verdict.
    token = rc.bind()
    try:
        out = await _try_substitute(
            failed_tool="failing_tool", failed_args={},
            tool_registry=substitution_registry, effective=<in-bounds effective>,
            substituted_tags=set(), trace_id="t",
        )
        assert out is not None  # sibling produced an observation
        evs = rc.get_recovery()
        assert len(evs) == 1
        assert evs[0].kind == "substitution"
        assert evs[0].failed == "failing_tool"
        assert evs[0].recovered_via == "sibling_tool"  # match the sibling's name
        assert evs[0].user_visible is True
    finally:
        rc.reset(token)
```

> Construct `substitution_registry` and `effective` by mirroring `test_self_heal_substitution.py` exactly (same `_CapabilityTool`, same `check_effective_bounds` in-bounds setup). If wiring `_try_substitute`'s real collaborators proves too heavy for a focused test, STOP and report — the journey in Task 5 covers this path end-to-end and can be the primary proof, with this task reduced to the one-line capture + reliance on Task 5. Prefer the focused test if achievable.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_substitution_records_recovery.py -q`
Expected: FAIL — `get_recovery()` empty (no capture yet).

- [ ] **Step 3: Add the capture**

In `src/stackowl/pipeline/steps/execute.py`, add the import near the top (with the other `stackowl.infra` imports):
```python
from stackowl.infra import recovery_context
```
In `_try_substitute`, on the success path — immediately after the `if not sib_result.success:` block returns (i.e. right before/after `tag = sib.manifest.capability_tag`, around line 367-371) — insert:
```python
        # Record the recovery so the user can be told (machinery-recorded, true by
        # construction) and the turn's recovery log captures it.
        recovery_context.record_recovery(
            kind="substitution", failed=failed_tool,
            recovered_via=sibling_name, user_visible=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_substitution_records_recovery.py -q` (PASS). Then run the existing substitution journey to confirm no regression: `uv run pytest tests/journeys/test_self_heal_substitution.py -q`. Then `uv run mypy src/stackowl/pipeline/steps/execute.py` and `uv run ruff check` the changed files (clean).

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py tests/pipeline/test_substitution_records_recovery.py
git commit -m "feat(v2): record a recovery event on capability-substitution success"
```

---

## Task 3: `surface_recovery` render step + localized template

**Files:**
- Modify: `src/stackowl/setup/localize.py` (`_STRINGS`)
- Create: `src/stackowl/pipeline/recovery_summary.py`
- Test: `tests/pipeline/test_recovery_summary_render.py` (create)

**Context:** Mirror the shipped `src/stackowl/pipeline/applied_lessons.py` exactly (same guards, same B5 catch, same chunk-append shape). The only differences: it reads `recovery_context.get_recovery()`, filters to `user_visible`, and uses the `self_heal_recovery_note` template with two slots.

- [ ] **Step 1: Add the localize key.** In `src/stackowl/setup/localize.py` `_STRINGS`, next to the other `self_heal_*` keys, add:
```python
    # Recovery explainability (pillar ④) — appended after a real answer when a
    # capability substitution recovered the turn. 2 slots: {failed} {recovered_via}.
    ("self_heal_recovery_note", "en"): "ℹ️ '{failed}' was unavailable, so I used '{recovered_via}' to complete this.",
    ("self_heal_recovery_note", "de"): "ℹ️ '{failed}' war nicht verfügbar, daher habe ich '{recovered_via}' verwendet.",
    ("self_heal_recovery_note", "fr"): "ℹ️ '{failed}' était indisponible, j'ai donc utilisé '{recovered_via}'.",
    ("self_heal_recovery_note", "es"): "ℹ️ '{failed}' no estaba disponible, así que usé '{recovered_via}'.",
```

- [ ] **Step 2: Write the failing test** `tests/pipeline/test_recovery_summary_render.py`:
```python
import pytest

from stackowl.infra import recovery_context as rc
from stackowl.pipeline.recovery_summary import surface_recovery
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(*, responses):
    return PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="o", pipeline_step="deliver", responses=responses,
    )


def _answer(text="here is your answer", is_floor=False):
    return ResponseChunk(content=text, is_final=False, chunk_index=0,
                         trace_id="t", owl_name="o", is_floor=is_floor)


@pytest.mark.asyncio
async def test_appends_line_for_user_visible_recovery_on_real_answer():
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="browse_url",
                           recovered_via="http_fetch", user_visible=True)
        out = await surface_recovery(_state(responses=(_answer(),)))
        assert len(out.responses) == 2
        assert "browse_url" in out.responses[-1].content
        assert "http_fetch" in out.responses[-1].content
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_log_only_event_is_not_surfaced():
    token = rc.bind()
    try:
        rc.record_recovery(kind="provider_fallback", failed="big",
                           recovered_via="small", user_visible=False)
        s = _state(responses=(_answer(),))
        out = await surface_recovery(s)
        assert out.responses == s.responses  # nothing appended
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_no_recovery_means_unchanged():
    token = rc.bind()
    try:
        s = _state(responses=(_answer(),))
        out = await surface_recovery(s)
        assert out.responses == s.responses
    finally:
        rc.reset(token)


@pytest.mark.asyncio
async def test_floor_only_response_gets_no_recovery_line():
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="a",
                           recovered_via="b", user_visible=True)
        s = _state(responses=(_answer("I couldn't finish", is_floor=True),))
        out = await surface_recovery(s)
        assert out.responses == s.responses
    finally:
        rc.reset(token)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_recovery_summary_render.py -q`
Expected: FAIL — `ModuleNotFoundError: ...recovery_summary`

- [ ] **Step 4: Implement** `src/stackowl/pipeline/recovery_summary.py`:
```python
"""surface_recovery — pre-delivery render of machinery recovery events (pillar ④).

Sibling of ``surface_applied_lessons``: runs once per turn, before deliver, in
BOTH backends — so the explanation reaches every channel with no per-channel
duplication. Appends a line ONLY for ``user_visible`` recovery events AND only
when there is a real (non-floor) answer to annotate. Never raises.
"""

from __future__ import annotations

from stackowl.infra import recovery_context
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.setup.localize import localize_format

_MAX_LINES = 2
_LANG = "en"  # turn language plumbing is out of scope; localize falls back to en


async def surface_recovery(state: PipelineState) -> PipelineState:
    """Append one localized line per user-visible recovery (capped). Self-healing."""
    try:
        events = [e for e in recovery_context.get_recovery() if e.user_visible]
        if not events:
            return state
        has_real_answer = any(
            c.content.strip() and not c.is_floor for c in state.responses
        )
        if not has_real_answer:
            log.engine.debug(
                "[recovery_summary] skip — no real answer to annotate",
                extra={"_fields": {"trace_id": state.trace_id, "n_events": len(events)}},
            )
            return state
        new_chunks: list[ResponseChunk] = []
        base_index = len(state.responses)
        for offset, e in enumerate(events[:_MAX_LINES]):
            text = localize_format(
                "self_heal_recovery_note", _LANG,
                failed=e.failed, recovered_via=e.recovered_via,
            )
            # Annotation chunk appended after the real answer; is_final stays False
            # (not a terminal response).
            new_chunks.append(ResponseChunk(
                content=text, is_final=False, chunk_index=base_index + offset,
                trace_id=state.trace_id, owl_name=state.owl_name,
            ))
        log.engine.info(
            "[recovery_summary] surfaced recovery lines",
            extra={"_fields": {"trace_id": state.trace_id, "n": len(new_chunks)}},
        )
        return state.evolve(responses=(*state.responses, *new_chunks))
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[recovery_summary] surfacing failed — leaving response untouched",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
```

- [ ] **Step 5: Run + verify + commit**

Run: `uv run pytest tests/pipeline/test_recovery_summary_render.py -q` (4 passed); `uv run mypy src/stackowl/pipeline/recovery_summary.py src/stackowl/setup/localize.py` (clean); `uv run ruff check` the 3 files (clean); `uv run pytest -q -k localize` (no regression).

```bash
git add src/stackowl/setup/localize.py src/stackowl/pipeline/recovery_summary.py tests/pipeline/test_recovery_summary_render.py
git commit -m "feat(v2): surface_recovery render step + localized recovery note (pillar 4)"
```

---

## Task 4: Wire both backends (bind/reset + render + unified log)

**Files:**
- Modify: `src/stackowl/pipeline/backends/asyncio_backend.py`
- Modify: `src/stackowl/pipeline/backends/langgraph_backend.py`
- Test: covered by Task 5's journey (this task is wiring).

**Context:** The lessons slice already added `lesson_context` bind/reset and `surface_applied_lessons` to both backends. Add `recovery_context` right alongside, and the unified recovery log in the same `finally`.

- [ ] **Step 1: Wire `asyncio_backend.py`.**

Add imports (with the existing `lesson_context`/`applied_lessons` imports):
```python
from stackowl.infra import recovery_context
from stackowl.pipeline.recovery_summary import surface_recovery
```
Where `lesson_token = lc.bind()` is (just after `TraceContext.start(...)`), add below it:
```python
        recovery_token = recovery_context.bind()
```
At the chokepoint — the current order is `surface_applied_lessons(current)` then `surface_critical_failure(current, ...)`. Insert `surface_recovery` BETWEEN them:
```python
            current = await surface_applied_lessons(current)
            current = await surface_recovery(current)
            current = await surface_critical_failure(current, self._services)
```
In the `finally` block, alongside `lc.reset(lesson_token)`, add the unified recovery log THEN the reset (log before reset so it reads the events):
```python
        finally:
            _recovery_events = recovery_context.get_recovery()
            if _recovery_events:
                log.engine.info(
                    "[recovery] turn summary",
                    extra={"_fields": {
                        "trace_id": state.trace_id,
                        "events": [
                            {"kind": e.kind, "failed": e.failed,
                             "recovered_via": e.recovered_via, "user_visible": e.user_visible}
                            for e in _recovery_events
                        ],
                    }},
                )
            recovery_context.reset(recovery_token)
            lc.reset(lesson_token)
            # ... existing TraceContext.reset / reset_services ...
```
> Read the existing `finally` first and place these lines as the FIRST statements in it (before the existing trace/services resets), so they always run.

- [ ] **Step 2: Wire `langgraph_backend.py`.**

Add the same two imports. In `_deliver_with_surfacing`, current order is `surface_applied_lessons` then `surface_critical_failure`; insert `surface_recovery` between:
```python
    surfaced = await surface_applied_lessons(state)
    surfaced = await surface_recovery(surfaced)
    surfaced = await surface_critical_failure(surfaced, get_services())
    return await deliver.run(surfaced)
```
In `run()`, after `lesson_token = lc.bind()` add `recovery_token = recovery_context.bind()`; in the `finally`, add the same unified-log emit + `recovery_context.reset(recovery_token)` (as the first statements, before the existing resets).

- [ ] **Step 3: Verify wiring compiles + targeted regression**

Run: `uv run mypy src/stackowl/pipeline/backends/asyncio_backend.py src/stackowl/pipeline/backends/langgraph_backend.py` (clean); `uv run ruff check` both (clean). Then:
```bash
uv run pytest -q -p no:cacheprovider tests/journeys/test_self_heal_substitution.py tests/journeys/test_learning_explainability_journey.py tests/journeys/test_self_heal_invariant.py
```
Expected: all PASS (no regression from the new wiring).

- [ ] **Step 4: Commit**

```bash
git add src/stackowl/pipeline/backends/asyncio_backend.py src/stackowl/pipeline/backends/langgraph_backend.py
git commit -m "feat(v2): wire surface_recovery + recovery_context bind + unified recovery log into both backends"
```

---

## Task 5: Gateway journey (FR1–FR4) + full regression

**Files:**
- Create: `tests/journeys/test_recovery_explainability_journey.py`

**Context:** Drive the REAL backend with a scripted provider whose tagged tool fails and an in-bounds tagged sibling succeeds. STUDY `tests/journeys/test_self_heal_substitution.py` for the exact harness (the `_CapabilityTool` fake, how the registry pairs a failing primary with a working same-`capability_tag` sibling, and how the provider drives `tool_dispatcher`). Reuse that harness; only the assertions change (user-visible recovery line present).

- [ ] **Step 1: Write the failing happy-path journey (FR1/FR5).**

Mirror `test_self_heal_substitution.py`'s boot and capability-tool setup. The scripted provider's `complete_with_tools` calls the failing tool via `tool_dispatcher` (substitution fires inside execute), then returns a final answer. Read the delivered text as that journey does. Assert the delivered user-visible text contains BOTH the answer AND the recovery line (the failed tool name AND the sibling name, per the `self_heal_recovery_note` template).

```python
# tests/journeys/test_recovery_explainability_journey.py
# Boot mirrors test_self_heal_substitution.py. Assertions:
#   assert _FINAL_ANSWER in delivered
#   assert "<failed_tool_name>" in delivered and "<sibling_tool_name>" in delivered
#   assert "ℹ️" in delivered  # the recovery note marker
```
Fill in the concrete failing/sibling tool names and the harness from the self-heal substitution journey. Add a docstring naming FR1/FR5.

- [ ] **Step 2: Run — confirm it FAILS** (recovery line absent) if Task 4 weren't wired. Since Task 4 is wired, it should PASS; if it FAILS on harness construction, fix the harness until it runs and the assertion is meaningful. If the substitution doesn't fire in the harness (no eligible sibling), fix the registry setup to mirror the self-heal journey exactly.

Run: `uv run pytest tests/journeys/test_recovery_explainability_journey.py -q`

- [ ] **Step 3: Add negatives (FR2, FR3).**

- `test_no_recovery_line_without_substitution` (FR2): scripted provider that succeeds with NO failing tool → assert delivered answer present AND `"ℹ️"` / recovery template text absent.
- `test_no_recovery_line_on_failed_turn` (FR3): scripted provider where the tool fails, substitution is recorded, BUT the turn ends with no usable answer (provider raises or returns empty after the substitution attempt, forcing a floor/critical-failure). Assert the recovery line is ABSENT (the floor explains the failure). IMPORTANT: if this assertion FAILS (recovery line leaked onto a failed turn), that is a real honesty defect — STOP and report BLOCKED, do not weaken the test.

> For FR3, study how the substitution path interacts with a subsequently-failed turn. If forcing "substitution recorded AND turn floored" is not cleanly reproducible in the harness, the render-unit `test_floor_only_response_gets_no_recovery_line` (Task 3) already covers the guard; note that and keep the journey FR3 best-effort, but TRY the integration version first.

- [ ] **Step 4: Full regression (FR6) + FR4 check.**

Run: `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`
Expected: prior 85 passed + 1 skipped, plus the new journey's tests → report exact counts (e.g. 88 passed, 1 skipped). ZERO failures/regressions; if any prior journey regresses, STOP and report BLOCKED.

For FR4 (broad log), confirm the `[recovery] turn summary` record is emitted: either assert via `caplog` in the happy-path test that a record with `msg` containing `"[recovery] turn summary"` and the substitution event was logged, OR add a small assertion reading the captured log. (Use the logging-capture approach other journey/unit tests in the repo use — check for `caplog` usage; if logs route through a custom sink, assert at the `get_recovery()` level in a backend-level test instead.)

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff check tests/journeys/test_recovery_explainability_journey.py` (clean; mypy on journey test files may carry the same pre-existing import-untyped/stream-stub patterns as siblings — acceptable if consistent).

```bash
git add tests/journeys/test_recovery_explainability_journey.py
git commit -m "test(v2): recovery-explainability journey — substitution surfaced (FR1), negatives (FR2/FR3), broad log (FR4)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1→Task 2+3+4+5; FR2→Task 5; FR3→Task 3 (unit) + Task 5 (journey); FR4→Task 4 (emit) + Task 5 (assert); FR5→Task 2 (machinery-recorded) + Task 3 (renders from recorded fields); FR6→Task 5. Honesty invariants → carrier unbound-noop (T1) + user_visible filter + real-answer guard (T3) + machinery capture (T2) + ordering before critical_failure (T4). All covered.
- **Placeholder scan:** The two `<...>`-style fills in Task 2/Task 5 tests are explicit "mirror this named existing file" harness-reuse instructions (the self-heal substitution journey), not deferred work — the engineer copies a concrete existing harness. No TBD/TODO.
- **Type consistency:** `RecoveryEvent(kind, failed, recovered_via, detail, user_visible)`, `record_recovery(*, kind, failed, recovered_via, detail="", user_visible)`, `get_recovery() -> tuple[...]`, `surface_recovery(state) -> PipelineState`, localize key `self_heal_recovery_note` — consistent across all tasks. Carrier module path `stackowl.infra.recovery_context` consistent.

## Risk & containment
- **Risk:** recovery line on a failed turn (the bug the lessons slice hit). **Contained:** `surface_recovery` runs BEFORE `surface_critical_failure` (Task 4) + the real-answer guard (Task 3); FR3 journey + render unit lock it.
- **Risk:** ContextVar leak across turns/concurrency. **Contained:** bind/reset in both backends' `finally` (Task 4); per-async-task ContextVars; carrier mirrors the proven `lesson_context` lifecycle.
- **Risk:** `_try_substitute` focused test too heavy to wire. **Contained:** Task 2 notes the journey (Task 5) as the end-to-end proof fallback.
- **Rollback:** pure-additive (see spec).

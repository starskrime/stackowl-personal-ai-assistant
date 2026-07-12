# Token Usage Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every final Telegram answer shows total input/output tokens spent that turn, appended to the answer text.

**Architecture:** One new read query summing `cost_records` by `trace_id` (data already captured, no new schema), one text-append at the same `consolidate.py` site Feature 2 (approach-rating buttons) attaches its keyboard.

**Tech Stack:** SQLite (existing `cost_records` table), pydantic `ResponseChunk` (existing).

## Global Constraints

- Every method gets 4-point logging (entry/decision/step/exit) per `CLAUDE.md`.
- No hidden errors: every `except` logs via `log.<ns>.error(..., exc_info=exc, extra={"_fields": {...}})`.
- Zero `cost_records` rows for a `trace_id` → append nothing (never `0 in / 0 out`).
- **Ordering dependency with Feature 2**: both this plan and `docs/superpowers/plans/2026-07-12-approach-rating-buttons.md` modify `consolidate.py`'s tail. This plan's token-line append MUST run before Feature 2's keyboard attachment (Feature 2's `Task 5, Step 4` copies the *already-updated* last chunk when building `rated_chunk` — if these land as separate PRs, whichever lands second must read the other's diff and order the two appends: token text into `content` first, keyboard attach second, both against the same final chunk).

---

### Task 1: `get_turn_token_totals` query

**Files:**
- Modify: `src/stackowl/providers/cost_tracker.py`
- Test: `tests/providers/test_cost_tracker_turn_totals.py`

**Interfaces:**
- Consumes: existing `cost_tracker.py` internals (`DbPool` or equivalent connection the module already holds — read the file first to match its existing query style, e.g. does it use `DbPool` directly like `outcome_store.py`, or a different connection wrapper).
- Produces: `async def get_turn_token_totals(self, trace_id: str) -> tuple[int, int] | None` — returns `(total_input, total_output)` or `None` if no rows exist for that `trace_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/providers/test_cost_tracker_turn_totals.py
import pytest
from stackowl.db.pool import DbPool
from stackowl.providers.cost_tracker import CostTracker  # read cost_tracker.py first to confirm the real class name/constructor


@pytest.mark.asyncio
async def test_sums_multiple_calls_for_same_trace(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE cost_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, provider_name TEXT NOT NULL,
            model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
            cost_usd REAL NOT NULL, trace_id TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL
        )
    """)
    await db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens, cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("openai", "gpt-fast", 100, 20, 0.001, "trace-1", "2026-07-12T00:00:00"),
    )
    await db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens, cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("openai", "gpt-main", 500, 300, 0.01, "trace-1", "2026-07-12T00:00:01"),
    )

    tracker = CostTracker(db)  # constructor args must match the real class — confirm during implementation
    totals = await tracker.get_turn_token_totals("trace-1")

    assert totals == (600, 320)


@pytest.mark.asyncio
async def test_no_rows_returns_none(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE cost_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, provider_name TEXT NOT NULL,
            model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
            cost_usd REAL NOT NULL, trace_id TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL
        )
    """)
    tracker = CostTracker(db)

    totals = await tracker.get_turn_token_totals("no-such-trace")

    assert totals is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_cost_tracker_turn_totals.py -v`
Expected: FAIL — `AttributeError: 'CostTracker' object has no attribute 'get_turn_token_totals'` (or an import error if the class name differs — fix the import to match what Step 1's file read confirms).

- [ ] **Step 3: Add the method**

Read `src/stackowl/providers/cost_tracker.py` first to find the exact class and its DB-access convention, then add:

```python
    async def get_turn_token_totals(self, trace_id: str) -> tuple[int, int] | None:
        log.tool.debug(
            "cost_tracker.get_turn_token_totals: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        rows = await self._db.fetch_all(
            "SELECT SUM(input_tokens) AS total_input, SUM(output_tokens) AS total_output "
            "FROM cost_records WHERE trace_id = ?",
            (trace_id,),
        )
        if not rows or rows[0]["total_input"] is None:
            log.tool.debug(
                "cost_tracker.get_turn_token_totals: no records for trace",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return None
        total_input = int(rows[0]["total_input"])
        total_output = int(rows[0]["total_output"])
        log.tool.info(
            "cost_tracker.get_turn_token_totals: exit",
            extra={"_fields": {"trace_id": trace_id, "total_input": total_input, "total_output": total_output}},
        )
        return (total_input, total_output)
```

(`self._db`/`log.tool` must match whatever the file's existing attribute/logger namespace actually is — read the file first and adjust.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_cost_tracker_turn_totals.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/cost_tracker.py tests/providers/test_cost_tracker_turn_totals.py
git commit -m "feat(providers): add CostTracker.get_turn_token_totals"
```

---

### Task 2: Append the token line in `consolidate.py`

**Files:**
- Modify: `src/stackowl/pipeline/steps/consolidate.py`
- Test: `tests/pipeline/test_consolidate_token_display.py`

**Interfaces:**
- Consumes: `CostTracker.get_turn_token_totals` (Task 1) via a `services.cost_tracker` field (check `services.py` first — `cost_tracker` may already be wired into `StepServices` since `cost_tracker.py` is an existing module other steps likely already use for budget enforcement; if so, reuse the existing field rather than adding a new one).
- Produces: nothing new consumed by later tasks.

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_consolidate_token_display.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk


@pytest.mark.asyncio
async def test_token_line_appended_when_records_exist(monkeypatch):
    cost_tracker = MagicMock()
    cost_tracker.get_turn_token_totals = AsyncMock(return_value=(600, 320))

    class FakeServices:
        cost_tracker = cost_tracker
        approach_rating_tracker = None  # Feature 2 field, not under test here

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t1", session_id="s1", input_text="hi",
        responses=(ResponseChunk(
            content="here is the answer", is_final=False, chunk_index=0,
            trace_id="t1", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    assert result.responses[-1].content == "here is the answer\n\n\U0001F522 600 in / 320 out"


@pytest.mark.asyncio
async def test_no_token_line_when_no_records(monkeypatch):
    cost_tracker = MagicMock()
    cost_tracker.get_turn_token_totals = AsyncMock(return_value=None)

    class FakeServices:
        cost_tracker = cost_tracker
        approach_rating_tracker = None

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t2", session_id="s1", input_text="hi",
        responses=(ResponseChunk(
            content="here is the answer", is_final=False, chunk_index=0,
            trace_id="t2", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    assert result.responses[-1].content == "here is the answer"


@pytest.mark.asyncio
async def test_no_token_line_on_floor_chunk(monkeypatch):
    cost_tracker = MagicMock()
    cost_tracker.get_turn_token_totals = AsyncMock(return_value=(600, 320))

    class FakeServices:
        cost_tracker = cost_tracker
        approach_rating_tracker = None

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t3", session_id="s1", input_text="hi",
        responses=(ResponseChunk(
            content="I couldn't complete this", is_final=False, chunk_index=0,
            trace_id="t3", owl_name="secretary", is_floor=True,
        ),),
    )

    result = await consolidate.run(state)

    assert result.responses[-1].content == "I couldn't complete this"
    cost_tracker.get_turn_token_totals.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_consolidate_token_display.py -v`
Expected: FAIL — `consolidate.run` doesn't append the token line yet.

- [ ] **Step 3: Wire the append into `consolidate.run`**

Read `src/stackowl/pipeline/steps/consolidate.py` first (same file Feature 2's plan, Task 5 Step 4 modifies — if that task has already landed, this step's edit composes with it: append the token line to `content` BEFORE Feature 2's keyboard-attach block runs against the same `last`/`out_state.responses[-1]`, per this plan's Global Constraints ordering note). Add, before the floor check that already exists (or immediately after `out_state` is computed, ahead of any Feature 2 code):

```python
_TOKEN_LINE_TEMPLATE = "\n\n\U0001F522 {input_tokens:,} in / {output_tokens:,} out"


async def _append_token_line(out_state: PipelineState) -> PipelineState:
    if not out_state.responses:
        return out_state
    last = out_state.responses[-1]
    if last.is_floor:
        return out_state
    services = get_services()
    cost_tracker = getattr(services, "cost_tracker", None)
    if cost_tracker is None:
        return out_state
    try:
        totals = await cost_tracker.get_turn_token_totals(out_state.trace_id)
    except Exception as exc:  # token display must never break delivery
        log.gateway.error(
            "consolidate.run: token totals lookup failed",
            exc_info=exc, extra={"_fields": {"trace_id": out_state.trace_id}},
        )
        return out_state
    if totals is None:
        return out_state
    input_tokens, output_tokens = totals
    updated_content = last.content + _TOKEN_LINE_TEMPLATE.format(
        input_tokens=input_tokens, output_tokens=output_tokens,
    )
    updated_chunk = last.model_copy(update={"content": updated_content})
    return out_state.evolve(responses=(*out_state.responses[:-1], updated_chunk))
```

Call `out_state = await _append_token_line(out_state)` at the point in `run` right after `out_state` is finalized, before returning (and before Feature 2's keyboard-attach block, per the ordering constraint above).

- [ ] **Step 4: Check/wire `cost_tracker` into `StepServices`**

Read `src/stackowl/pipeline/services.py` — if `cost_tracker` is already a field (likely, given budget-enforcement code elsewhere in the pipeline per Feature 1's earlier research into `cost_tracker.py`'s `_BUDGET_WARN_RATIO`), no change needed. If not, add `cost_tracker: CostTracker | None = None` at the same construction site as other stores.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_consolidate_token_display.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run full pipeline suite for regressions**

Run: `uv run pytest tests/pipeline/ -x -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/pipeline/steps/consolidate.py src/stackowl/pipeline/services.py tests/pipeline/test_consolidate_token_display.py
git commit -m "feat(pipeline): append token usage line to final answers"
```

---

### Task 3: End-to-end regression pass

**Files:**
- Test: `tests/pipeline/test_token_usage_e2e.py`

**Interfaces:**
- Consumes: everything from Tasks 1-2.

- [ ] **Step 1: Write the end-to-end test**

```python
# tests/pipeline/test_token_usage_e2e.py
"""Full loop: multiple cost_records rows for a trace -> summed total appended to the final answer."""
import pytest

from stackowl.db.pool import DbPool
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.cost_tracker import CostTracker  # confirm real class name during Task 1


@pytest.mark.asyncio
async def test_full_token_display_loop(tmp_path, monkeypatch):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE cost_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, provider_name TEXT NOT NULL,
            model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
            cost_usd REAL NOT NULL, trace_id TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL
        )
    """)
    await db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens, cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("openai", "classifier", 50, 5, 0.0001, "trace-e2e", "2026-07-12T00:00:00"),
    )
    await db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens, cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("openai", "answer", 400, 250, 0.008, "trace-e2e", "2026-07-12T00:00:02"),
    )

    cost_tracker = CostTracker(db)

    class FakeServices:
        cost_tracker = cost_tracker
        approach_rating_tracker = None

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="trace-e2e", session_id="s1", input_text="prepare me for the interview",
        responses=(ResponseChunk(
            content="here's your interview prep plan", is_final=False, chunk_index=0,
            trace_id="trace-e2e", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    assert result.responses[-1].content == "here's your interview prep plan\n\n\U0001F522 450 in / 255 out"

    await db.close()
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/pipeline/test_token_usage_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Run the full targeted test suite**

Run: `uv run pytest tests/providers/test_cost_tracker_turn_totals.py tests/pipeline/test_consolidate_token_display.py tests/pipeline/test_token_usage_e2e.py -v`
Expected: PASS, all green.

- [ ] **Step 4: Run `ruff` and `mypy`**

Run: `uv run ruff check src/ && uv run mypy src/`
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add tests/pipeline/test_token_usage_e2e.py
git commit -m "test(pipeline): add end-to-end token usage display regression test"
```

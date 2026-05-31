# Plan B — Long-Term Memory Fill (extraction → promotion wiring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make long-term recall actually return facts by closing the three gaps that keep `committed_facts` empty.

**Architecture:** (1) Repoint `FactExtractionJobHandler` to read conversation turns from `staged_facts` (where consolidate actually writes them) instead of the never-populated `messages`/`conversations` tables. (2) Give the pipeline a scheduler handle. (3) From `consolidate`, every N session turns, enqueue + run a one-shot `fact_extraction` job via the scheduler (B9-compliant — `create_job`/`run_now`, never direct dispatch). The existing DreamWorker then promotes high-confidence extracted facts into `committed_facts`, which `recall()` reads. Fixes RC-A.

**Tech Stack:** Python 3, aiosqlite, pytest, asyncio, scheduler (cron + one-shot).

**BMad boundaries honored:** B9 (extraction runs through HandlerRegistry/scheduler, not direct call); B5 (every catch logs); uses existing stores (staged_facts) rather than new aggregators.

**Pre-req:** Plan A's `_parse_turns_to_messages` parser is reused here — if Plan A is not merged first, port that helper into `memory/` instead.

---

## Root-cause recap (three gaps, all confirmed)

1. `FactExtractionJobHandler._fetch_messages` reads `messages JOIN conversations` (extraction_handler.py:22-29) — both tables are **empty with no writers** anywhere.
2. `consolidate` writes turns to `staged_facts` (`source_type='conversation'`) — a different store; and it **never enqueues** an extraction job (assembly.py:17-19 admits this).
3. Result: `committed_facts` = 0 rows; `recall()` reads only `committed_facts` → always empty.

---

## File Structure

- Modify: `src/stackowl/memory/extraction_handler.py` — read turns from `staged_facts`.
- Modify: `src/stackowl/pipeline/services.py` — add `scheduler` to `StepServices`.
- Modify: `src/stackowl/startup/orchestrator.py` — pass the scheduler into `StepServices`.
- Modify: `src/stackowl/pipeline/steps/consolidate.py` — enqueue extraction every N turns.
- Test: `tests/memory/test_plan_b_extraction_source.py`, `tests/pipeline/test_plan_b_consolidate_enqueue.py`.

---

### Task 1: Extraction reads conversation turns from `staged_facts`

**Files:**
- Modify: `src/stackowl/memory/extraction_handler.py`
- Test: `tests/memory/test_plan_b_extraction_source.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_plan_b_extraction_source.py
import pytest
from stackowl.memory.extraction_handler import FactExtractionJobHandler


@pytest.mark.asyncio
async def test_fetch_messages_reads_staged_conversation(make_db, make_bridge):
    db = await make_db()
    bridge = make_bridge(db)
    await bridge.store("User: I live in Baku\n\nAssistant: Noted.", "sess-1")
    await bridge.store("User: I use Python\n\nAssistant: Great.", "sess-1")
    handler = FactExtractionJobHandler(extractor=None, memory_bridge=bridge, db=db, message_limit=20)
    msgs = await handler._fetch_messages("sess-1")
    contents = [m.content for m in msgs]
    assert any("Baku" in c for c in contents)
    assert any("Python" in c for c in contents)
    assert [m.role for m in msgs][:2] == ["user", "assistant"]  # oldest-first, real roles
```

> Implementer: reuse the project's existing DB/bridge fixtures (grep `def make_bridge` / conftest under tests/memory). If none, build a `DbPool` over a temp sqlite and run migrations.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/test_plan_b_extraction_source.py -v`
Expected: FAIL — current `_fetch_messages` queries empty `messages`/`conversations`, returns `[]`.

- [ ] **Step 3: Repoint the fetch to `staged_facts`**

In `src/stackowl/memory/extraction_handler.py`, replace `_FETCH_SESSION_MESSAGES_SQL` (lines 22-29) with:

```python
_FETCH_SESSION_MESSAGES_SQL = """
SELECT content
FROM staged_facts
WHERE source_type = 'conversation' AND source_ref = ?
ORDER BY staged_at DESC
LIMIT ?
"""
```

Replace `_fetch_messages` (lines 206-222) with a parser over the stored turn format:

```python
    async def _fetch_messages(self, session_id: str) -> list[Message]:
        rows = await self._db.fetch_all(
            _FETCH_SESSION_MESSAGES_SQL, (session_id, self._message_limit)
        )
        rows = list(reversed(rows))  # oldest-first
        out: list[Message] = []
        for row in rows:
            content = row["content"]
            user_part, _, assistant_part = content.partition("\n\nAssistant:")
            user_text = user_part.removeprefix("User:").strip()
            assistant_text = assistant_part.strip()
            if user_text:
                out.append(Message(role="user", content=user_text))
            if assistant_text:
                out.append(Message(role="assistant", content=assistant_text))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/test_plan_b_extraction_source.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/memory/extraction_handler.py tests/memory/test_plan_b_extraction_source.py
git commit -m "fix(v2): fact extraction reads turns from staged_facts not empty messages table (RC-A gap 1)"
```

---

### Task 2: `StepServices` carries a scheduler handle

**Files:**
- Modify: `src/stackowl/pipeline/services.py`
- Modify: `src/stackowl/startup/orchestrator.py`
- Test: `tests/pipeline/test_plan_b_consolidate_enqueue.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_plan_b_consolidate_enqueue.py
from stackowl.pipeline.services import StepServices


def test_stepservices_has_scheduler_field():
    s = StepServices()
    assert hasattr(s, "scheduler")
    assert s.scheduler is None  # default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_plan_b_consolidate_enqueue.py -k scheduler_field -v`
Expected: FAIL — no `scheduler` field.

- [ ] **Step 3: Add the field + wire it**

In `src/stackowl/pipeline/services.py`, add to the `StepServices` dataclass (mirror the existing optional fields, e.g. after `preference_store`):

```python
    scheduler: JobScheduler | None = field(default=None)
```

Add the typing-only import in the `TYPE_CHECKING` block:

```python
    from stackowl.scheduler.scheduler import JobScheduler
```

In `src/stackowl/startup/orchestrator.py`, find where `StepServices(...)` is constructed (grep `StepServices(`) and pass the already-built scheduler instance: `scheduler=scheduler` (use the same local variable the orchestrator already holds for the `JobScheduler`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_plan_b_consolidate_enqueue.py -k scheduler_field -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/services.py src/stackowl/startup/orchestrator.py tests/pipeline/test_plan_b_consolidate_enqueue.py
git commit -m "feat(v2): StepServices exposes scheduler for pipeline-driven jobs"
```

---

### Task 3: consolidate enqueues extraction every N turns (B9-compliant)

**Files:**
- Modify: `src/stackowl/pipeline/steps/consolidate.py`
- Test: `tests/pipeline/test_plan_b_consolidate_enqueue.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pipeline/test_plan_b_consolidate_enqueue.py
import pytest


class _SpyScheduler:
    def __init__(self): self.created = []; self.ran = []
    async def create_job(self, **kw): self.created.append(kw); 
    # return a stub job with the id derived from idempotency_key
    # (match the real Job shape your run_now needs)
    async def run_now(self, job_id): self.ran.append(job_id)


@pytest.mark.asyncio
async def test_consolidate_enqueues_extraction_on_threshold(make_db, make_bridge, monkeypatch):
    db = await make_db(); bridge = make_bridge(db)
    spy = _SpyScheduler()
    # seed N-1 prior turns so this turn crosses the threshold (N from settings)
    from stackowl.config.settings import Settings
    n = Settings().memory.extraction_after_n_messages
    for i in range(n - 1):
        await bridge.store(f"User: q{i}\n\nAssistant: a{i}", "sess-X")
    from stackowl.pipeline.services import StepServices, set_services
    set_services(StepServices(memory_bridge=bridge, scheduler=spy))
    # build a state whose turn, once persisted, is the Nth
    ...  # run consolidate.run(state) for session sess-X with a response
    assert spy.created, "expected an extraction job to be enqueued at the Nth turn"
    assert spy.created[0]["idempotency_key"] == "fact_extraction:sess-X"
```

> Implementer: finalize the `...` by constructing a `PipelineState` with `session_id="sess-X"`, `input_text` set, and one `ResponseChunk` in `responses`, then `await consolidate.run(state)`. Confirm `run_now` is called with the created job's id.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_plan_b_consolidate_enqueue.py -k enqueues -v`
Expected: FAIL — consolidate never enqueues.

- [ ] **Step 3: Add the enqueue after persist**

First confirm the one-shot schedule token: grep existing one-shot jobs (`grep -rn "schedule=" src/stackowl tests | grep -i "once\|@reboot\|now"`) and the `compute_next_run` accepted forms. Use that literal below as `_ONESHOT`.

In `src/stackowl/pipeline/steps/consolidate.py`, add a helper and call it from `run()` after `_persist_turn`:

```python
_ONESHOT = "@once"  # replace with the verified one-shot token from compute_next_run


async def _maybe_enqueue_extraction(state: PipelineState) -> None:
    """Every N session turns, fire a one-shot fact-extraction job (B9: via scheduler)."""
    services = get_services()
    bridge, scheduler = services.memory_bridge, services.scheduler
    if bridge is None or scheduler is None:
        return
    from stackowl.config.settings import Settings
    n = Settings().memory.extraction_after_n_messages
    try:
        turns = await bridge.recent_conversation_turns(session_id=state.session_id, limit=10_000)
        count = len(turns)
    except Exception as exc:
        log.memory.warning("[pipeline] consolidate: turn-count failed — skip extraction",
                            exc_info=exc, extra={"_fields": {"session_id": state.session_id}})
        return
    if count == 0 or count % n != 0:
        return
    try:
        job = await scheduler.create_job(
            handler_name="fact_extraction",
            schedule=_ONESHOT,
            idempotency_key=f"fact_extraction:{state.session_id}",
        )
        await scheduler.run_now(job.job_id)
        log.memory.info("[pipeline] consolidate: extraction enqueued",
                        extra={"_fields": {"session_id": state.session_id, "turns": count}})
    except Exception as exc:  # B5 — never block delivery on a background job
        log.memory.warning("[pipeline] consolidate: enqueue extraction failed — skipping",
                            exc_info=exc, extra={"_fields": {"session_id": state.session_id}})
```

Call it in `run()` right after `await _persist_turn(out_state)`:

```python
    await _persist_turn(out_state)
    await _maybe_enqueue_extraction(out_state)
```

> Note on `run_now` signature: it lives in `scheduler/scheduler_mutations.py:92` and is exposed on the scheduler. Confirm the exact accessor (`scheduler.run_now(job_id)` vs a module function) and adjust the call; the SpyScheduler test pins the intended contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_plan_b_consolidate_enqueue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/consolidate.py tests/pipeline/test_plan_b_consolidate_enqueue.py
git commit -m "feat(v2): consolidate enqueues fact-extraction every N turns via scheduler (RC-A gaps 2/3)"
```

---

### Task 4: End-to-end staged→committed + promotion-gate documentation

**Files:**
- Test: `tests/memory/test_plan_b_extraction_source.py`
- Modify: `src/stackowl/config/settings.py` (doc comment only)

- [ ] **Step 1: Write an integration test** that: stores N conversation turns containing a stable fact, runs the `FactExtractionJobHandler.execute` with a real (or stub-LLM) extractor that returns a `confidence>=0.8` fact, then runs the promoter and asserts the fact appears in `committed_facts` and that `bridge.recall("...")` returns it. (Stub the extractor to avoid a live LLM call on the Jetson box.)

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/memory/test_plan_b_extraction_source.py -k end_to_end -v --timeout=120`
Expected: PASS — fact reaches `committed_facts` and is recallable.

- [ ] **Step 3: Document the promotion gate as a tunable (no silent cap)**

The gate is `confidence>=0.8 AND reinforcement_count>=3` (fact_promoter.py / settings `reinforcement_required`, default 3). A freshly extracted fact has `reinforcement_count=0`, so it promotes only after being corroborated 3×. In `src/stackowl/config/settings.py`, extend the `reinforcement_required` field doc to state plainly: *"How many times a staged fact must be corroborated before promotion to committed_facts (long-term recall). Lower to 1 for faster long-term recall at the cost of admitting less-established facts."* Do **not** change the default in this plan — surface it for the operator.

- [ ] **Step 4: Phase 2 backlog note** — add a one-line entry to the project's Phase 2 backlog: *"Consider per-source reinforcement thresholds so high-value user facts (preferences, identity) promote faster than incidental chatter."* (Per the project's deferred-work rule.)

- [ ] **Step 5: Commit**

```bash
git add tests/memory/test_plan_b_extraction_source.py src/stackowl/config/settings.py
git commit -m "test(v2): e2e extraction->promotion->recall; document reinforcement_required tunable"
```

---

## Self-Review

- **Spec coverage:** Gap 1 (wrong source store) → Task 1. Gap 2 (never enqueued) → Tasks 2-3. Gap 3 (committed empty) → Task 4 proves the chain fills. ✓
- **Type consistency:** `Message(role, content)` matches Plan A and `providers.base.Message`. `idempotency_key="fact_extraction:<session_id>"` matches the handler's `_parse_session_id` convention (extraction_handler.py:200-204). ✓
- **Placeholders:** Two explicit "confirm exact token/accessor" steps (`_ONESHOT`, `run_now` accessor) name the precise call + pin it with a spy test — these are interface confirmations, not deferred work. ✓
- **B9:** extraction is dispatched only through `scheduler.create_job`/`run_now` (HandlerRegistry-backed), never a direct handler call. ✓

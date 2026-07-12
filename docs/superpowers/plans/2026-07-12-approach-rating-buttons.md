# Approach Rating Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every substantial final Telegram answer gets a Like/Dislike button row. Tapping rates that turn's *approach* (not output content) — Like feeds DNA evolution's existing positive-signal query, Dislike is recorded but excluded from it.

**Architecture:** A new `CallbackRouter` prefix (`apr:`) plus a dedicated module (`approach_rating.py`) mirrors `consent.py`'s proven pattern class-for-class: build a 2-button `InlineKeyboardBuilder` keyboard, attach it via the existing `ResponseChunk.actions`-driven Telegram send path, backfill the sent message's `(chat_id, message_id)` into an in-memory map keyed by `trace_id` (same convention `command_buttons.py` already uses), and on tap, edit the message in place and write `task_outcomes.approach_rating` via one new `TaskOutcomeStore` method.

**Tech Stack:** SQLite (`task_outcomes` table, migration), `python-telegram-bot` inline keyboards (existing adapter), pydantic `Action`/`ResponseChunk` (existing).

## Global Constraints

- Every `execute()`/handler method gets 4-point logging (entry/decision/step/exit) per `CLAUDE.md`.
- No hidden errors: every `except` logs via `log.<ns>.error(..., exc_info=exc, extra={"_fields": {...}})`.
- Migrations idempotent (`ALTER TABLE ... ADD COLUMN` guarded, since SQLite has no `ADD COLUMN IF NOT EXISTS`; the runner's own applied-migrations tracking already prevents re-running a migration file, matching every other file in this directory).
- Dislike must never be readable by DNA evolution's positive-signal query (`dna_attribution.py`) — this is the one hard invariant carried from the spec.

---

### Task 1: `approach_rating` column migration

**Files:**
- Create: `src/stackowl/db/migrations/0083_task_outcomes_approach_rating.sql`
- Test: `tests/db/test_migration_0083.py`

**Interfaces:**
- Produces: `task_outcomes.approach_rating TEXT` (nullable, `'positive'|'negative'|NULL`).

- [ ] **Step 1: Write the migration**

```sql
-- 0083_task_outcomes_approach_rating.sql
ALTER TABLE task_outcomes ADD COLUMN approach_rating TEXT
    CHECK (approach_rating IN ('positive', 'negative') OR approach_rating IS NULL);
```

- [ ] **Step 2: Write the failing test**

```python
# tests/db/test_migration_0083.py
import pytest
from stackowl.db.pool import DbPool


@pytest.mark.asyncio
async def test_approach_rating_column_exists(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    try:
        rows = await db.fetch_all("PRAGMA table_info(task_outcomes)")
        columns = {r["name"] for r in rows}
        assert "approach_rating" in columns
    finally:
        await db.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/db/test_migration_0083.py -v`
Expected: FAIL (`approach_rating` not in `columns` — migration doesn't exist yet, or `task_outcomes` table not created because migration 0029 hasn't run in a fresh `tmp_path` db — if so, the fixture must run all migrations first; use the same fixture pattern `tests/db/test_migration_0082.py` uses, which opens a fresh `DbPool` and calls whatever the repo's actual migration-apply entrypoint is — read that test file first to confirm the exact fixture shape before writing this one).

- [ ] **Step 4: Run migrations and re-run test**

Run: `uv run python -m stackowl db migrate && uv run pytest tests/db/test_migration_0083.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/db/migrations/0083_task_outcomes_approach_rating.sql tests/db/test_migration_0083.py
git commit -m "feat(db): add approach_rating column to task_outcomes"
```

---

### Task 2: `TaskOutcomeStore.set_approach_rating`

**Files:**
- Modify: `src/stackowl/memory/outcome_store.py`
- Test: `tests/memory/test_outcome_store_approach_rating.py`

**Interfaces:**
- Consumes: existing `TaskOutcomeStore` (`_db: DbPool`, `_owner_id`), existing `record()` method (Task 2 does not change it).
- Produces: `async def set_approach_rating(self, *, trace_id: str, rating: str) -> bool` — returns `True` if a row was updated, `False` if no `task_outcomes` row exists for that `trace_id` (never raises on missing row — caller decides how to handle).

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_outcome_store_approach_rating.py
import pytest
from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore


@pytest.mark.asyncio
async def test_set_approach_rating_updates_existing_row(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE task_outcomes (
            outcome_id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL,
            session_id TEXT NOT NULL, owl_name TEXT NOT NULL, channel TEXT NOT NULL,
            success INTEGER NOT NULL, latency_ms REAL NOT NULL,
            tool_call_count INTEGER NOT NULL DEFAULT 0, failure_class TEXT,
            quality_score REAL, step_durations TEXT NOT NULL DEFAULT '{}',
            input_text TEXT NOT NULL DEFAULT '', response_text TEXT NOT NULL DEFAULT '',
            captured_at REAL NOT NULL, scored_at REAL, owner_id TEXT NOT NULL DEFAULT 'principal-default',
            tool_sequence TEXT NOT NULL DEFAULT '[]', dna_snapshot TEXT NOT NULL DEFAULT '{}',
            overclaim_blocked INTEGER NOT NULL DEFAULT 0, recovered_via_tool TEXT,
            failed_capability TEXT, approach_rating TEXT, UNIQUE(trace_id)
        )
    """)
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id="trace-1", session_id="s1", owl_name="secretary", channel="telegram",
        success=True, latency_ms=100.0, tool_call_count=1, failure_class=None,
        step_durations={}, input_text="hi", response_text="hello",
    )

    updated = await store.set_approach_rating(trace_id="trace-1", rating="positive")
    assert updated is True

    rows = await db.fetch_all("SELECT approach_rating FROM task_outcomes WHERE trace_id = ?", ("trace-1",))
    assert rows[0]["approach_rating"] == "positive"


@pytest.mark.asyncio
async def test_set_approach_rating_missing_row_returns_false(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE task_outcomes (
            outcome_id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL,
            session_id TEXT NOT NULL, owl_name TEXT NOT NULL, channel TEXT NOT NULL,
            success INTEGER NOT NULL, latency_ms REAL NOT NULL,
            tool_call_count INTEGER NOT NULL DEFAULT 0, failure_class TEXT,
            quality_score REAL, step_durations TEXT NOT NULL DEFAULT '{}',
            input_text TEXT NOT NULL DEFAULT '', response_text TEXT NOT NULL DEFAULT '',
            captured_at REAL NOT NULL, scored_at REAL, owner_id TEXT NOT NULL DEFAULT 'principal-default',
            approach_rating TEXT, UNIQUE(trace_id)
        )
    """)
    store = TaskOutcomeStore(db)

    updated = await store.set_approach_rating(trace_id="nonexistent", rating="negative")
    assert updated is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/test_outcome_store_approach_rating.py -v`
Expected: FAIL — `AttributeError: 'TaskOutcomeStore' object has no attribute 'set_approach_rating'`

- [ ] **Step 3: Add the method**

Read `src/stackowl/memory/outcome_store.py` first to find the exact class body / existing method style, then add (using `DbPool.execute_returning_rowcount` — confirmed API from Feature 1's research: `async def execute_returning_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int`):

```python
    async def set_approach_rating(self, *, trace_id: str, rating: str) -> bool:
        log.memory.debug(
            "outcome_store.set_approach_rating: entry",
            extra={"_fields": {"trace_id": trace_id, "rating": rating}},
        )
        rowcount = await self._db.execute_returning_rowcount(
            "UPDATE task_outcomes SET approach_rating = ? WHERE trace_id = ? AND owner_id = ?",
            (rating, trace_id, self._owner_id),
        )
        updated = rowcount > 0
        log.memory.info(
            "outcome_store.set_approach_rating: exit",
            extra={"_fields": {"trace_id": trace_id, "rating": rating, "updated": updated}},
        )
        return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/memory/test_outcome_store_approach_rating.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/memory/outcome_store.py tests/memory/test_outcome_store_approach_rating.py
git commit -m "feat(memory): add TaskOutcomeStore.set_approach_rating"
```

---

### Task 3: Wire `approach_rating` into DNA evolution's positive-signal filter

**Files:**
- Modify: `src/stackowl/owls/dna_attribution.py:151-155`
- Test: `tests/owls/test_dna_attribution_approach_rating.py`

**Interfaces:**
- Consumes: the `TaskOutcome` dataclass's `approach_rating` field — add it to `TaskOutcome` in `outcome_store.py` (Task 2's schema addition needs a matching dataclass field, since `_row_to_model`-equivalent code in `outcome_store.py` maps SQL rows to `TaskOutcome` instances — read the file to find and extend that mapping alongside the `set_approach_rating` addition from Task 2, Step 3, since both touch the same file).
- Produces: nothing new consumed by later tasks — this closes the loop the spec flagged as unverified.

- [ ] **Step 1: Add `approach_rating` to `TaskOutcome` and its row-mapping**

In `outcome_store.py`, add one field to the `TaskOutcome` dataclass (alongside `failed_capability: str | None = None`):

```python
    approach_rating: str | None = None
```

And extend whatever function maps a raw SQL row dict to a `TaskOutcome` (read the file to find it — likely near `list_pending_critic`'s row-processing loop) to pass through `row.get("approach_rating")`.

- [ ] **Step 2: Write the failing test**

```python
# tests/owls/test_dna_attribution_approach_rating.py
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.owls.dna_attribution import _filter_scored_outcomes  # read the file first to confirm the real function name wrapping the list-comp at dna_attribution.py:151-155


def _outcome(**overrides):
    defaults = dict(
        outcome_id=1, trace_id="t1", session_id="s1", owl_name="secretary", channel="telegram",
        success=True, latency_ms=100.0, tool_call_count=1, failure_class=None,
        quality_score=0.8, step_durations={}, input_text="hi", response_text="hello",
        captured_at=0.0, scored_at=0.0, dna_snapshot={"trait": 0.5}, approach_rating=None,
    )
    defaults.update(overrides)
    return TaskOutcome(**defaults)


def test_negative_approach_rating_excluded_from_dna_attribution():
    disliked = _outcome(trace_id="t-disliked", approach_rating="negative")
    liked = _outcome(trace_id="t-liked", approach_rating="positive")
    unrated = _outcome(trace_id="t-unrated", approach_rating=None)

    scored = _filter_scored_outcomes([disliked, liked, unrated])

    trace_ids = {o.trace_id for o in scored}
    assert "t-disliked" not in trace_ids
    assert "t-liked" in trace_ids
    assert "t-unrated" in trace_ids  # unrated outcomes keep today's behavior unchanged
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/owls/test_dna_attribution_approach_rating.py -v`
Expected: FAIL — either `ImportError` (function name wrong — fix the import to match the real name found in Step 1's file read) or `assert "t-disliked" not in trace_ids` fails (current filter doesn't know about `approach_rating` yet).

- [ ] **Step 4: Extend the filter**

At `dna_attribution.py:151-155`, add one condition to the existing list comprehension (read the surrounding function first to edit in place rather than guessing indentation/variable names):

```python
    scored = [
        o for o in outcomes
        if o.quality_score is not None and o.dna_snapshot
        and o.success and not o.failure_class
        and o.approach_rating != "negative"
    ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/owls/test_dna_attribution_approach_rating.py -v`
Expected: PASS

- [ ] **Step 6: Run the full DNA/owls test suite for regressions**

Run: `uv run pytest tests/owls/ -x -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/owls/dna_attribution.py src/stackowl/memory/outcome_store.py tests/owls/test_dna_attribution_approach_rating.py
git commit -m "feat(owls): exclude disliked-approach outcomes from DNA attribution"
```

---

### Task 4: `approach_rating.py` — keyboard + callback handler + edit-in-place

**Files:**
- Create: `src/stackowl/channels/telegram/approach_rating.py`
- Test: `tests/channels/telegram/test_approach_rating.py`

**Interfaces:**
- Consumes: `stackowl.channels.telegram.keyboard.InlineKeyboardBuilder` (`.add_button(text, callback_data) -> Self`, `.build() -> dict`), `TaskOutcomeStore.set_approach_rating` (Task 2), the adapter's `edit_message(chat_id, message_id, text, reply_markup=None)` (existing, confirmed usage in `consent.py:217-218`).
- Produces (used by Task 5, 6):
  - `APPROACH_RATING_PREFIX = "apr"` (module constant)
  - `class ApproachRatingTracker: def __init__(self) -> None`, `def record_pending(self, *, trace_id: str) -> None`, `def backfill_message(self, *, trace_id: str, chat_id: int, message_id: int) -> None`, `def build_keyboard(self, *, trace_id: str) -> dict[str, object]`
  - `class ApproachRatingCallbackHandler: def __init__(self, *, tracker: ApproachRatingTracker, outcome_store: TaskOutcomeStore, adapter) -> None`, `async def handle(self, callback_id: str, callback_data: str) -> None` — the `_Handler` signature `CallbackRouter.register` expects (confirmed: `Callable[[str, str], Awaitable[None]]`).

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/telegram/test_approach_rating.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from stackowl.channels.telegram.approach_rating import (
    ApproachRatingCallbackHandler, ApproachRatingTracker,
)


def test_build_keyboard_has_two_buttons():
    tracker = ApproachRatingTracker()
    keyboard = tracker.build_keyboard(trace_id="trace-1")

    buttons = keyboard["inline_keyboard"][0]
    assert len(buttons) == 2
    assert buttons[0]["callback_data"] == "apr:trace-1:positive"
    assert buttons[1]["callback_data"] == "apr:trace-1:negative"


def test_backfill_then_lookup():
    tracker = ApproachRatingTracker()
    tracker.record_pending(trace_id="trace-1")
    tracker.backfill_message(trace_id="trace-1", chat_id=555, message_id=999)

    assert tracker.get_message(trace_id="trace-1") == (555, 999)


@pytest.mark.asyncio
async def test_handle_positive_vote_records_and_edits():
    tracker = ApproachRatingTracker()
    tracker.record_pending(trace_id="trace-1")
    tracker.backfill_message(trace_id="trace-1", chat_id=555, message_id=999)

    outcome_store = MagicMock()
    outcome_store.set_approach_rating = AsyncMock(return_value=True)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    adapter.answer_callback_query = AsyncMock()

    handler = ApproachRatingCallbackHandler(tracker=tracker, outcome_store=outcome_store, adapter=adapter)
    await handler.handle("callback-id-1", "apr:trace-1:positive")

    outcome_store.set_approach_rating.assert_awaited_once_with(trace_id="trace-1", rating="positive")
    adapter.edit_message.assert_awaited_once()
    call_args = adapter.edit_message.await_args
    assert call_args.args[0] == 555
    assert call_args.args[1] == 999


@pytest.mark.asyncio
async def test_handle_unknown_trace_id_noops_gracefully():
    tracker = ApproachRatingTracker()
    outcome_store = MagicMock()
    outcome_store.set_approach_rating = AsyncMock(return_value=False)
    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    adapter.answer_callback_query = AsyncMock()

    handler = ApproachRatingCallbackHandler(tracker=tracker, outcome_store=outcome_store, adapter=adapter)
    await handler.handle("callback-id-2", "apr:unknown-trace:positive")  # must not raise

    adapter.edit_message.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_approach_rating.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/stackowl/channels/telegram/approach_rating.py
"""Like/Dislike approach-rating buttons — CallbackRouter prefix "apr".

Mirrors consent.py's pattern: build a keyboard, track the sent message's
(chat_id, message_id) in an in-memory map keyed by trace_id (backfilled
post-send, same convention command_buttons.py uses), edit the message in
place on tap. Unlike consent.py this is fire-and-forget (no blocking
Future/park) — a vote is a one-shot write, nothing waits on it.
"""

from __future__ import annotations

from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.infra.observability import log

APPROACH_RATING_PREFIX = "apr"

_LIKE_LABEL = "\U0001F44D"
_DISLIKE_LABEL = "\U0001F44E"
_LIKED_SUFFIX = "\n\n\U0001F44D Liked"
_DISLIKED_SUFFIX = "\n\n\U0001F44E Disliked"


class ApproachRatingTracker:
    """In-memory trace_id -> (chat_id, message_id) map for pending votes."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[int, int] | None] = {}

    def record_pending(self, *, trace_id: str) -> None:
        self._pending[trace_id] = None

    def backfill_message(self, *, trace_id: str, chat_id: int, message_id: int) -> None:
        if trace_id in self._pending:
            self._pending[trace_id] = (chat_id, message_id)

    def get_message(self, *, trace_id: str) -> tuple[int, int] | None:
        return self._pending.get(trace_id)

    def clear(self, *, trace_id: str) -> None:
        self._pending.pop(trace_id, None)

    def build_keyboard(self, *, trace_id: str) -> dict[str, object]:
        builder = InlineKeyboardBuilder()
        builder.add_button(_LIKE_LABEL, f"{APPROACH_RATING_PREFIX}:{trace_id}:positive")
        builder.add_button(_DISLIKE_LABEL, f"{APPROACH_RATING_PREFIX}:{trace_id}:negative")
        return builder.build()


class ApproachRatingCallbackHandler:
    """CallbackRouter handler for the "apr" prefix."""

    def __init__(self, *, tracker: ApproachRatingTracker, outcome_store, adapter) -> None:
        self._tracker = tracker
        self._outcome_store = outcome_store
        self._adapter = adapter

    async def handle(self, callback_id: str, callback_data: str) -> None:
        # 1. ENTRY
        log.telegram.debug(
            "approach_rating.handle: entry",
            extra={"_fields": {"callback_data": callback_data}},
        )
        try:
            _, trace_id, vote = callback_data.split(":", 2)
        except ValueError:
            log.telegram.error(
                "approach_rating.handle: malformed callback_data",
                extra={"_fields": {"callback_data": callback_data}},
            )
            await self._safe_answer(callback_id)
            return

        updated = await self._outcome_store.set_approach_rating(trace_id=trace_id, rating=vote)
        await self._safe_answer(callback_id)
        if not updated:
            log.telegram.warning(
                "approach_rating.handle: no task_outcomes row for trace — vote recorded nowhere",
                extra={"_fields": {"trace_id": trace_id}},
            )
            self._tracker.clear(trace_id=trace_id)
            return

        location = self._tracker.get_message(trace_id=trace_id)
        if location is None:
            log.telegram.warning(
                "approach_rating.handle: vote recorded but no message location — edit skipped",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return
        chat_id, message_id = location
        suffix = _LIKED_SUFFIX if vote == "positive" else _DISLIKED_SUFFIX
        try:
            await self._adapter.edit_message(chat_id, message_id, suffix, reply_markup=None)
        except Exception as exc:  # message may be too old/deleted — vote already recorded, don't fail the turn
            log.telegram.error(
                "approach_rating.handle: edit failed — vote already recorded",
                exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
            )
        finally:
            self._tracker.clear(trace_id=trace_id)
        # 4. EXIT
        log.telegram.info(
            "approach_rating.handle: exit",
            extra={"_fields": {"trace_id": trace_id, "vote": vote}},
        )

    async def _safe_answer(self, callback_id: str) -> None:
        try:
            await self._adapter.answer_callback_query(callback_id)
        except Exception as exc:
            log.telegram.error(
                "approach_rating.handle: answer_callback_query failed",
                exc_info=exc, extra={"_fields": {"callback_id": callback_id}},
            )
```

Note: `edit_message`'s real signature must be confirmed against `consent.py:217-218`'s actual call (`await self._adapter.edit_message(pending.chat_id, pending.message_id, decision_text, reply_markup=None)`) before finalizing — the code above matches that positional/keyword shape.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/channels/telegram/test_approach_rating.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/channels/telegram/approach_rating.py tests/channels/telegram/test_approach_rating.py
git commit -m "feat(telegram): add approach-rating keyboard + callback handler"
```

---

### Task 5: Attach the keyboard to qualifying final answers

**Files:**
- Modify: `src/stackowl/pipeline/steps/consolidate.py`
- Test: `tests/pipeline/test_consolidate_approach_rating.py`

**Interfaces:**
- Consumes: `ApproachRatingTracker` (Task 4) via a new `services.approach_rating_tracker` field (mirrors Feature 1's `services.retry_queue_store` wiring pattern — add alongside it in `StepServices`), `Action` (`stackowl.commands.response.Action` — fields `label: str, command: str, destructive: bool = False`), `ResponseChunk.actions` (existing field).
- Produces: nothing new consumed by later tasks — Task 6 reads the tracker directly.

**Note on `Action` vs raw keyboard dict:** `ResponseChunk.actions` is typed `tuple[Action, ...]`, and `Action` has no field for a raw `callback_data` string — it's shaped for slash-command re-invocation (`label` + `command`), not arbitrary callback payloads. Attaching a *raw* inline keyboard (not built from `Action`s) requires a different chunk-level signal than `.actions`. Read `src/stackowl/pipeline/streaming.py` and `src/stackowl/channels/telegram/adapter.py:352-420` (`send()`/`send_text_or_actions`) fully before implementing this task — confirm whether `ResponseChunk` needs a new field (e.g. `raw_keyboard: dict[str, object] | None = None`) parallel to `actions`, since `Action`'s `command`-based shape does not fit a callback-data-carrying vote button. This is a genuine open design question the plan author could not resolve without reading the full adapter send path in more depth than the research pass covered — the two adapter code excerpts seen (`_send_part`, `send_text_or_actions` call site) did not show enough of `send_text_or_actions`'s internals to confirm whether it accepts anything other than `Action`-derived buttons.

- [ ] **Step 1: Add `raw_keyboard` field to `ResponseChunk`**

In `src/stackowl/pipeline/streaming.py`, add alongside the existing `actions` field:

```python
    # Raw inline-keyboard dict (InlineKeyboardBuilder.build() output) for buttons
    # that don't map to a re-invocable slash command (e.g. approach-rating votes,
    # which carry a callback_data payload Action's label+command shape can't
    # represent). None for every ordinary chunk.
    raw_keyboard: dict[str, object] | None = None
```

- [ ] **Step 2: Write the failing test**

```python
# tests/pipeline/test_consolidate_approach_rating.py
import pytest
from unittest.mock import MagicMock

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk


@pytest.mark.asyncio
async def test_qualifying_answer_gets_rating_keyboard(monkeypatch):
    tracker = MagicMock()
    tracker.record_pending = MagicMock()
    tracker.build_keyboard = MagicMock(return_value={"inline_keyboard": [[{"text": "x", "callback_data": "apr:t1:positive"}]]})

    class FakeServices:
        approach_rating_tracker = tracker

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    long_answer = "x" * 250
    state = PipelineState(
        trace_id="t1", session_id="s1", input_text="hi",
        responses=(ResponseChunk(
            content=long_answer, is_final=False, chunk_index=0,
            trace_id="t1", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    tracker.record_pending.assert_called_once_with(trace_id="t1")
    assert result.responses[-1].raw_keyboard is not None


@pytest.mark.asyncio
async def test_short_answer_gets_no_keyboard(monkeypatch):
    tracker = MagicMock()

    class FakeServices:
        approach_rating_tracker = tracker

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t2", session_id="s1", input_text="hi",
        responses=(ResponseChunk(
            content="ok", is_final=False, chunk_index=0,
            trace_id="t2", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    tracker.record_pending.assert_not_called()
    assert result.responses[-1].raw_keyboard is None


@pytest.mark.asyncio
async def test_floor_answer_gets_no_keyboard(monkeypatch):
    tracker = MagicMock()

    class FakeServices:
        approach_rating_tracker = tracker

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t3", session_id="s1", input_text="hi",
        responses=(ResponseChunk(
            content="x" * 250, is_final=False, chunk_index=0,
            trace_id="t3", owl_name="secretary", is_floor=True,
        ),),
    )

    result = await consolidate.run(state)

    tracker.record_pending.assert_not_called()
    assert result.responses[-1].raw_keyboard is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_consolidate_approach_rating.py -v`
Expected: FAIL — `raw_keyboard` doesn't exist yet on the chunk / consolidate doesn't attach it.

- [ ] **Step 4: Wire the qualification check into `consolidate.run`**

Read `src/stackowl/pipeline/steps/consolidate.py` first (confirmed shape: merges `state.responses` for tool-only turns, per Feature 2's research finding #4 — `consolidate.py:45-56`). Add, at the end of `run` before returning, using the module-level constant `_MIN_RATEABLE_LENGTH = 200`:

```python
_MIN_RATEABLE_LENGTH = 200


def _qualifies_for_rating(chunk: ResponseChunk) -> bool:
    return not chunk.is_floor and len(chunk.content) >= _MIN_RATEABLE_LENGTH
```

```python
    # ... existing consolidate logic producing out_state ...
    if out_state.responses:
        last = out_state.responses[-1]
        if _qualifies_for_rating(last):
            services = get_services()
            tracker = getattr(services, "approach_rating_tracker", None)
            if tracker is not None:
                try:
                    tracker.record_pending(trace_id=out_state.trace_id)
                    keyboard = tracker.build_keyboard(trace_id=out_state.trace_id)
                    rated_chunk = last.model_copy(update={"raw_keyboard": keyboard})
                    out_state = out_state.evolve(
                        responses=(*out_state.responses[:-1], rated_chunk)
                    )
                except Exception as exc:  # rating attachment must never break delivery
                    log.gateway.error(
                        "consolidate.run: approach-rating keyboard attach failed",
                        exc_info=exc, extra={"_fields": {"trace_id": out_state.trace_id}},
                    )
    return out_state
```

(`get_services`/`log` imports and the exact `out_state` variable name must match what's already in the file — read it first; this is the logical shape to append, not a byte-exact diff, since consolidate.py's current body wasn't read in full during planning.)

- [ ] **Step 5: Wire `approach_rating_tracker` into `StepServices`**

Same construction site as Feature 1's `retry_queue_store` (`src/stackowl/pipeline/services.py`) — add `approach_rating_tracker: ApproachRatingTracker | None = None`, constructed once as a process-wide singleton (it's in-memory state, must be the same instance the Telegram adapter's backfill call and the callback handler both reference — not a fresh instance per turn).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_consolidate_approach_rating.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run full pipeline suite for regressions**

Run: `uv run pytest tests/pipeline/ -x -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/pipeline/streaming.py src/stackowl/pipeline/steps/consolidate.py src/stackowl/pipeline/services.py tests/pipeline/test_consolidate_approach_rating.py
git commit -m "feat(pipeline): attach approach-rating keyboard to qualifying final answers"
```

---

### Task 6: Send `raw_keyboard`, backfill message location, register the callback handler

**Files:**
- Modify: `src/stackowl/channels/telegram/adapter.py`
- Modify: `src/stackowl/channels/telegram/callbacks.py` (or wherever `CallbackRouter.register` calls are made at startup — grep `callback_router.register(` to find the registration site, per Feature 2's research finding #2 showing `memory_callbacks.py:175-176` as the pattern)
- Test: `tests/channels/telegram/test_adapter_raw_keyboard.py`

**Interfaces:**
- Consumes: `ApproachRatingTracker.backfill_message` (Task 4), `ApproachRatingCallbackHandler` (Task 4), `CallbackRouter.register(prefix: str, handler) -> None` (existing, confirmed signature).
- Produces: nothing new consumed by later tasks.

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/telegram/test_adapter_raw_keyboard.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_send_with_raw_keyboard_backfills_tracker(monkeypatch):
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.pipeline.streaming import ResponseChunk

    tracker = MagicMock()
    tracker.backfill_message = MagicMock()

    class FakeServices:
        approach_rating_tracker = tracker

    monkeypatch.setattr("stackowl.pipeline.services.get_services", lambda: FakeServices())

    adapter = TelegramChannelAdapter.__new__(TelegramChannelAdapter)
    fake_message = MagicMock(message_id=777)
    adapter._bot_app = MagicMock()
    adapter._bot_app.bot.send_message = AsyncMock(return_value=fake_message)

    chunk = ResponseChunk(
        content="x" * 250, is_final=True, chunk_index=0, trace_id="trace-9",
        owl_name="secretary", is_floor=False,
        raw_keyboard={"inline_keyboard": [[{"text": "x", "callback_data": "apr:trace-9:positive"}]]},
    )

    async def _one_chunk():
        yield chunk

    await adapter.send(_one_chunk(), chat_id=321)

    tracker.backfill_message.assert_called_once_with(trace_id="trace-9", chat_id=321, message_id=777)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_adapter_raw_keyboard.py -v`
Expected: FAIL — `send()` doesn't check `chunk.raw_keyboard` yet, `tracker.backfill_message` never called.

- [ ] **Step 3: Wire `raw_keyboard` into `send()`**

Read `src/stackowl/channels/telegram/adapter.py`'s `send()` method fully first (Feature 2's research covered lines ~352-420 at a summary level — `chunk_actions = getattr(chunk, "actions", ())` accumulation, `send_text_or_actions(buffer, actions, chat_id=target)` call, `build_command_keyboard`). Add a parallel check for `raw_keyboard` alongside the existing `actions` accumulation: if any chunk in the stream carries a non-`None` `raw_keyboard`, pass that dict directly as the `reply_markup` to whatever underlying send call `send_text_or_actions` (or a sibling path) makes, instead of routing through `build_command_keyboard` (which is `Action`-shaped, not compatible with a raw dict). After the send resolves and a `message_id` is available (same message-object capture Feature 1's Task 4 already added to `_send_part`), call:

```python
        if chunk.raw_keyboard is not None:
            from stackowl.pipeline.services import get_services

            tracker = getattr(get_services(), "approach_rating_tracker", None)
            if tracker is not None and message is not None:
                tracker.backfill_message(
                    trace_id=chunk.trace_id, chat_id=target, message_id=message.message_id,
                )
```

(`message` and `target` must match whatever the real local variable names are in the current `send()` body — this plan gives the logical shape and the exact backfill call; the surrounding send-with-keyboard branch needs to be read and adapted in place, not guessed byte-for-byte, since the full `send_text_or_actions` internals weren't read during planning.)

- [ ] **Step 4: Register the callback handler at startup**

Find the startup site that constructs `CallbackRouter` and calls `.register(...)` for existing prefixes (grep `callback_router.register(` — per research, `memory_callbacks.py:175-176` shows the pattern: `callback_router.register(_APPROVE_PREFIX, self.handle_approve)`). Add, at the same site:

```python
from stackowl.channels.telegram.approach_rating import (
    APPROACH_RATING_PREFIX, ApproachRatingCallbackHandler, ApproachRatingTracker,
)

approach_rating_tracker = ApproachRatingTracker()
# stash approach_rating_tracker on the process-wide services singleton here (same
# construction site as Task 5 Step 5's services.approach_rating_tracker wiring)
approach_rating_handler = ApproachRatingCallbackHandler(
    tracker=approach_rating_tracker, outcome_store=outcome_store, adapter=adapter,
)
callback_router.register(APPROACH_RATING_PREFIX, approach_rating_handler.handle)
```

(`outcome_store`/`adapter` must be whatever variables are already in scope at that startup site — read it first.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/channels/telegram/test_adapter_raw_keyboard.py -v`
Expected: PASS

- [ ] **Step 6: Run the full Telegram adapter suite for regressions**

Run: `uv run pytest tests/channels/telegram/ -x -q`
Expected: PASS, no regressions (Feature 1's Task 4 changes to `_send_part`/`_deliver`/`send_text` must still be compatible — both features touch `adapter.py`; if Feature 1 already landed, rebase this task's edits on top of it rather than reintroducing the old discard-the-message behavior).

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/channels/telegram/adapter.py tests/channels/telegram/test_adapter_raw_keyboard.py
git commit -m "feat(telegram): send raw_keyboard chunks, backfill approach-rating tracker, register apr callback"
```

---

### Task 7: End-to-end regression pass

**Files:**
- Test: `tests/channels/telegram/test_approach_rating_e2e.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.

- [ ] **Step 1: Write the end-to-end test**

```python
# tests/channels/telegram/test_approach_rating_e2e.py
"""Full loop: qualifying answer -> keyboard attached -> sent -> tapped -> recorded + edited."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from stackowl.channels.telegram.approach_rating import (
    ApproachRatingCallbackHandler, ApproachRatingTracker,
)
from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore


@pytest.mark.asyncio
async def test_full_approach_rating_loop(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE task_outcomes (
            outcome_id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL,
            session_id TEXT NOT NULL, owl_name TEXT NOT NULL, channel TEXT NOT NULL,
            success INTEGER NOT NULL, latency_ms REAL NOT NULL,
            tool_call_count INTEGER NOT NULL DEFAULT 0, failure_class TEXT,
            quality_score REAL, step_durations TEXT NOT NULL DEFAULT '{}',
            input_text TEXT NOT NULL DEFAULT '', response_text TEXT NOT NULL DEFAULT '',
            captured_at REAL NOT NULL, scored_at REAL, owner_id TEXT NOT NULL DEFAULT 'principal-default',
            approach_rating TEXT, UNIQUE(trace_id)
        )
    """)
    store = TaskOutcomeStore(db)
    await store.record(
        trace_id="trace-e2e", session_id="s1", owl_name="secretary", channel="telegram",
        success=True, latency_ms=50.0, tool_call_count=0, failure_class=None,
        step_durations={}, input_text="prepare me for the interview", response_text="here's your plan...",
    )

    tracker = ApproachRatingTracker()
    tracker.record_pending(trace_id="trace-e2e")
    tracker.backfill_message(trace_id="trace-e2e", chat_id=42, message_id=100)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    adapter.answer_callback_query = AsyncMock()

    handler = ApproachRatingCallbackHandler(tracker=tracker, outcome_store=store, adapter=adapter)

    await handler.handle("cb-1", "apr:trace-e2e:positive")

    rows = await db.fetch_all("SELECT approach_rating FROM task_outcomes WHERE trace_id = ?", ("trace-e2e",))
    assert rows[0]["approach_rating"] == "positive"
    adapter.edit_message.assert_awaited_once_with(42, 100, "\n\n\U0001F44D Liked", reply_markup=None)
    assert tracker.get_message(trace_id="trace-e2e") is None  # cleared after vote

    await db.close()
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/channels/telegram/test_approach_rating_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Run the full targeted test suite**

Run: `uv run pytest tests/db/test_migration_0083.py tests/memory/test_outcome_store_approach_rating.py tests/owls/test_dna_attribution_approach_rating.py tests/channels/telegram/test_approach_rating.py tests/pipeline/test_consolidate_approach_rating.py tests/channels/telegram/test_adapter_raw_keyboard.py tests/channels/telegram/test_approach_rating_e2e.py -v`
Expected: PASS, all green.

- [ ] **Step 4: Run `ruff` and `mypy`**

Run: `uv run ruff check src/ && uv run mypy src/`
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add tests/channels/telegram/test_approach_rating_e2e.py
git commit -m "test(telegram): add end-to-end approach-rating regression test"
```

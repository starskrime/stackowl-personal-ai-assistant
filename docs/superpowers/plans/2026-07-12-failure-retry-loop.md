# Failure Retry Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a turn ends in the terminal "I couldn't fully complete this" floor response, the failure retries itself automatically in the background (capped at 3 attempts, forced onto a different capability each time), and a user's "do it again" triggers the same failure-aware retry immediately instead of a blind re-ask.

**Architecture:** A new `retry_queue` table records every floored turn (inserted synchronously in-pipeline, backfilled with the sent Telegram message reference asynchronously post-send). A new scheduler `JobHandler` sweeps due rows every minute; a shared `RetryActuator.attempt_retry()` re-invokes the pipeline the same way scheduled goal-jobs already do (`goal_execution.py`'s `PipelineState` + `backend.run()` pattern), with a prompt-injected note steering the model away from the capability that already failed. Success edits the original floor message in place; 3 failures send one notification and stop.

**Tech Stack:** SQLite via `DbPool`/`OwnedRepository` (existing), `python-telegram-bot` (existing adapter), scheduler `JobHandler` (existing), pydantic `PipelineState` (existing).

## Global Constraints

- Every `execute()`/store method gets 4-point logging (entry/decision/step/exit) per `CLAUDE.md` — `log.scheduler`, `log.telegram`, or `log.tool` namespace as appropriate.
- No hidden errors: every `except` block logs via `log.<ns>.error(..., exc_info=exc, extra={"_fields": {...}})`. `attempt_retry` and the sweep handler must never raise into the scheduler loop.
- No hardcoded English keyword lists for retry-intent detection — LLM classifier only (mirrors `FeedbackClassifier`).
- Migrations are idempotent (`CREATE TABLE IF NOT EXISTS`), applied via `stackowl db migrate`.
- All new DB state lives in SQLite via `DbPool`, owner-scoped via `OwnedRepository` (`owner_id`, default `DEFAULT_PRINCIPAL_ID`).

---

### Task 1: `retry_queue` migration

**Files:**
- Create: `src/stackowl/db/migrations/0082_retry_queue.sql`
- Test: `tests/db/test_migration_0082.py`

**Interfaces:**
- Produces: table `retry_queue` with columns `id, trace_id, session_id, goal, banned_capabilities, attempt_count, status, next_retry_at, last_error, channel, channel_chat_id, channel_message_id, owner_id, created_at, updated_at`.

- [ ] **Step 1: Write the migration**

```sql
-- 0082_retry_queue.sql
CREATE TABLE IF NOT EXISTS retry_queue (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    banned_capabilities TEXT NOT NULL DEFAULT '[]',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
    next_retry_at TEXT NOT NULL,
    last_error TEXT,
    channel TEXT NOT NULL DEFAULT 'telegram',
    channel_chat_id TEXT,
    channel_message_id TEXT,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retry_queue_status_due ON retry_queue(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_retry_queue_session ON retry_queue(owner_id, session_id, status);
CREATE INDEX IF NOT EXISTS idx_retry_queue_trace ON retry_queue(trace_id);
```

- [ ] **Step 2: Write the failing test**

```python
# tests/db/test_migration_0082.py
import pytest
from stackowl.db.pool import DbPool


@pytest.mark.asyncio
async def test_retry_queue_table_exists(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    try:
        rows = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='retry_queue'"
        )
        assert len(rows) == 1
    finally:
        await db.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/db/test_migration_0082.py -v`
Expected: FAIL (table doesn't exist — migration not yet in the runner's applied set, or file didn't exist before Step 1).

- [ ] **Step 4: Run migrations and re-run test**

Run: `uv run python -m stackowl db migrate && uv run pytest tests/db/test_migration_0082.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/db/migrations/0082_retry_queue.sql tests/db/test_migration_0082.py
git commit -m "feat(db): add retry_queue table for failure retry loop"
```

---

### Task 2: `RetryQueueStore`

**Files:**
- Create: `src/stackowl/memory/retry_queue_store.py`
- Test: `tests/memory/test_retry_queue_store.py`

**Interfaces:**
- Consumes: `stackowl.db.pool.DbPool`, `stackowl.tenancy.OwnedRepository`, `stackowl.tenancy.DEFAULT_PRINCIPAL_ID` (existing).
- Produces (used by Task 3, 4, 5, 6):
  - `RetryQueueRow` frozen dataclass — fields: `id: str, trace_id: str, session_id: str, goal: str, banned_capabilities: list[str], attempt_count: int, status: str, next_retry_at: str, last_error: str | None, channel: str, channel_chat_id: str | None, channel_message_id: str | None, created_at: str, updated_at: str`.
  - `RetryQueueStore(db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID)`:
    - `async def insert_pending(self, *, trace_id: str, session_id: str, goal: str, banned_capabilities: list[str], channel: str = "telegram") -> str` — returns the new row's `id`.
    - `async def backfill_channel_message(self, *, trace_id: str, channel_chat_id: int, channel_message_id: int) -> None`
    - `async def get_due(self, *, limit: int = 25) -> list[RetryQueueRow]`
    - `async def get_latest_pending_for_session(self, session_id: str) -> RetryQueueRow | None`
    - `async def mark_completed(self, retry_id: str) -> None`
    - `async def mark_attempt_failed(self, *, retry_id: str, newly_failed_capability: str, error: str) -> RetryQueueRow` — appends to `banned_capabilities`, increments `attempt_count`, sets `status='failed'` when `attempt_count >= 3` else re-arms `next_retry_at` +1 minute; returns the updated row.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_retry_queue_store.py
import pytest
from stackowl.db.pool import DbPool
from stackowl.memory.retry_queue_store import RetryQueueStore


@pytest.mark.asyncio
async def test_insert_and_get_due(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE retry_queue (
            id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, session_id TEXT NOT NULL,
            goal TEXT NOT NULL, banned_capabilities TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            next_retry_at TEXT NOT NULL, last_error TEXT, channel TEXT NOT NULL DEFAULT 'telegram',
            channel_chat_id TEXT, channel_message_id TEXT, owner_id TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    store = RetryQueueStore(db)

    retry_id = await store.insert_pending(
        trace_id="trace-1", session_id="sess-1", goal="do the thing",
        banned_capabilities=["cronjob"],
    )
    assert retry_id

    due = await store.get_due()
    assert len(due) == 1
    assert due[0].trace_id == "trace-1"
    assert due[0].banned_capabilities == ["cronjob"]
    assert due[0].status == "pending"

    await db.close()


@pytest.mark.asyncio
async def test_backfill_channel_message(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE retry_queue (
            id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, session_id TEXT NOT NULL,
            goal TEXT NOT NULL, banned_capabilities TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            next_retry_at TEXT NOT NULL, last_error TEXT, channel TEXT NOT NULL DEFAULT 'telegram',
            channel_chat_id TEXT, channel_message_id TEXT, owner_id TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    store = RetryQueueStore(db)
    await store.insert_pending(
        trace_id="trace-2", session_id="sess-1", goal="do the thing", banned_capabilities=[],
    )

    await store.backfill_channel_message(trace_id="trace-2", channel_chat_id=555, channel_message_id=999)

    row = await store.get_latest_pending_for_session("sess-1")
    assert row.channel_chat_id == "555"
    assert row.channel_message_id == "999"

    await db.close()


@pytest.mark.asyncio
async def test_mark_attempt_failed_caps_at_three(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE retry_queue (
            id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, session_id TEXT NOT NULL,
            goal TEXT NOT NULL, banned_capabilities TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            next_retry_at TEXT NOT NULL, last_error TEXT, channel TEXT NOT NULL DEFAULT 'telegram',
            channel_chat_id TEXT, channel_message_id TEXT, owner_id TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    store = RetryQueueStore(db)
    retry_id = await store.insert_pending(
        trace_id="trace-3", session_id="sess-1", goal="do the thing", banned_capabilities=["a"],
    )

    row = await store.mark_attempt_failed(retry_id=retry_id, newly_failed_capability="b", error="boom")
    assert row.status == "pending"
    assert row.attempt_count == 1
    assert row.banned_capabilities == ["a", "b"]

    row = await store.mark_attempt_failed(retry_id=retry_id, newly_failed_capability="c", error="boom")
    row = await store.mark_attempt_failed(retry_id=retry_id, newly_failed_capability="d", error="boom")
    assert row.status == "failed"
    assert row.attempt_count == 3

    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/test_retry_queue_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stackowl.memory.retry_queue_store'`

- [ ] **Step 3: Write the implementation**

```python
# src/stackowl/memory/retry_queue_store.py
"""RetryQueueStore — persistence for the failure retry loop.

Every floored turn (the terminal "I couldn't fully complete this" response)
gets a row here, inserted synchronously in-pipeline (turn_persist.py) and
backfilled with the sent channel message reference asynchronously once the
Telegram send resolves (adapter.py). A scheduler sweep (retry_sweep.py)
retries due rows every minute, capped at 3 attempts.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

_MAX_ATTEMPTS = 3
_RETRY_INTERVAL_MINUTES = 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class RetryQueueRow:
    """Read-side projection of one retry_queue row."""

    id: str
    trace_id: str
    session_id: str
    goal: str
    banned_capabilities: list[str] = field(default_factory=list)
    attempt_count: int = 0
    status: str = "pending"
    next_retry_at: str = ""
    last_error: str | None = None
    channel: str = "telegram"
    channel_chat_id: str | None = None
    channel_message_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


def _row_to_model(row: dict) -> RetryQueueRow:
    return RetryQueueRow(
        id=row["id"],
        trace_id=row["trace_id"],
        session_id=row["session_id"],
        goal=row["goal"],
        banned_capabilities=json.loads(row["banned_capabilities"]),
        attempt_count=row["attempt_count"],
        status=row["status"],
        next_retry_at=row["next_retry_at"],
        last_error=row["last_error"],
        channel=row["channel"],
        channel_chat_id=row["channel_chat_id"],
        channel_message_id=row["channel_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_SELECT_COLUMNS = (
    "id, trace_id, session_id, goal, banned_capabilities, attempt_count, "
    "status, next_retry_at, last_error, channel, channel_chat_id, "
    "channel_message_id, created_at, updated_at"
)


class RetryQueueStore(OwnedRepository):
    """Async SQLite wrapper for the retry_queue table (migration 0082)."""

    _table = "retry_queue"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)
        self._db = db

    async def insert_pending(
        self, *, trace_id: str, session_id: str, goal: str,
        banned_capabilities: list[str], channel: str = "telegram",
    ) -> str:
        # 1. ENTRY
        log.memory.debug(
            "retry_queue_store.insert_pending: entry",
            extra={"_fields": {"trace_id": trace_id, "session_id": session_id, "channel": channel}},
        )
        retry_id = uuid.uuid4().hex
        now = _now_iso()
        await self._db.execute(
            """INSERT INTO retry_queue
               (id, trace_id, session_id, goal, banned_capabilities, attempt_count,
                status, next_retry_at, last_error, channel, channel_chat_id,
                channel_message_id, owner_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, 'pending', ?, NULL, ?, NULL, NULL, ?, ?, ?)""",
            (
                retry_id, trace_id, session_id, goal,
                json.dumps(banned_capabilities, separators=(",", ":")),
                now, channel, self._owner_id, now, now,
            ),
        )
        # 4. EXIT
        log.memory.info(
            "retry_queue_store.insert_pending: exit",
            extra={"_fields": {"retry_id": retry_id, "trace_id": trace_id}},
        )
        return retry_id

    async def backfill_channel_message(
        self, *, trace_id: str, channel_chat_id: int, channel_message_id: int,
    ) -> None:
        log.memory.debug(
            "retry_queue_store.backfill_channel_message: entry",
            extra={"_fields": {"trace_id": trace_id, "channel_message_id": channel_message_id}},
        )
        await self._db.execute(
            """UPDATE retry_queue SET channel_chat_id = ?, channel_message_id = ?, updated_at = ?
               WHERE trace_id = ? AND owner_id = ? AND status = 'pending'""",
            (str(channel_chat_id), str(channel_message_id), _now_iso(), trace_id, self._owner_id),
        )

    async def get_due(self, *, limit: int = 25) -> list[RetryQueueRow]:
        rows = await self._db.fetch_all(
            f"""SELECT {_SELECT_COLUMNS} FROM retry_queue
                WHERE owner_id = ? AND status = 'pending' AND next_retry_at <= ?
                ORDER BY next_retry_at ASC LIMIT ?""",
            (self._owner_id, _now_iso(), limit),
        )
        return [_row_to_model(r) for r in rows]

    async def get_latest_pending_for_session(self, session_id: str) -> RetryQueueRow | None:
        rows = await self._db.fetch_all(
            f"""SELECT {_SELECT_COLUMNS} FROM retry_queue
                WHERE owner_id = ? AND session_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1""",
            (self._owner_id, session_id),
        )
        return _row_to_model(rows[0]) if rows else None

    async def mark_completed(self, retry_id: str) -> None:
        log.memory.info(
            "retry_queue_store.mark_completed: exit",
            extra={"_fields": {"retry_id": retry_id}},
        )
        await self._db.execute(
            "UPDATE retry_queue SET status = 'completed', updated_at = ? WHERE id = ? AND owner_id = ?",
            (_now_iso(), retry_id, self._owner_id),
        )

    async def mark_attempt_failed(
        self, *, retry_id: str, newly_failed_capability: str, error: str,
    ) -> RetryQueueRow:
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_COLUMNS} FROM retry_queue WHERE id = ? AND owner_id = ?",
            (retry_id, self._owner_id),
        )
        if not rows:
            raise ValueError(f"retry_queue row not found: {retry_id}")
        current = _row_to_model(rows[0])
        banned = [*current.banned_capabilities]
        if newly_failed_capability and newly_failed_capability not in banned:
            banned.append(newly_failed_capability)
        attempt_count = current.attempt_count + 1
        status = "failed" if attempt_count >= _MAX_ATTEMPTS else "pending"
        next_retry_at = (
            datetime.now(UTC) + timedelta(minutes=_RETRY_INTERVAL_MINUTES)
        ).isoformat()
        now = _now_iso()
        log.memory.warning(
            "retry_queue_store.mark_attempt_failed: exit",
            extra={"_fields": {
                "retry_id": retry_id, "attempt_count": attempt_count, "status": status,
                "newly_failed_capability": newly_failed_capability,
            }},
        )
        await self._db.execute(
            """UPDATE retry_queue
               SET banned_capabilities = ?, attempt_count = ?, status = ?,
                   next_retry_at = ?, last_error = ?, updated_at = ?
               WHERE id = ? AND owner_id = ?""",
            (
                json.dumps(banned, separators=(",", ":")), attempt_count, status,
                next_retry_at, error[:2000], now, retry_id, self._owner_id,
            ),
        )
        return RetryQueueRow(
            id=current.id, trace_id=current.trace_id, session_id=current.session_id,
            goal=current.goal, banned_capabilities=banned, attempt_count=attempt_count,
            status=status, next_retry_at=next_retry_at, last_error=error[:2000],
            channel=current.channel, channel_chat_id=current.channel_chat_id,
            channel_message_id=current.channel_message_id, created_at=current.created_at,
            updated_at=now,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/memory/test_retry_queue_store.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/memory/retry_queue_store.py tests/memory/test_retry_queue_store.py
git commit -m "feat(memory): add RetryQueueStore for failure retry loop"
```

---

### Task 3: Insert pending row on floored turns (`turn_persist.py`)

**Files:**
- Modify: `src/stackowl/pipeline/turn_persist.py`
- Test: `tests/pipeline/test_turn_persist_retry_queue.py`

**Interfaces:**
- Consumes: `RetryQueueStore.insert_pending` (Task 2), `stackowl.pipeline.delivery_gate._attempts_for_state` (existing, already imported pattern in `delivery_gate.py`), `services.retry_queue_store` (new field on `StepServices`/`get_services()` — add alongside the existing `memory_bridge`/`preference_store` fields, same construction site).
- Produces: nothing new consumed by later tasks (Task 4/6 read the table directly via `RetryQueueStore`).

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_turn_persist_retry_queue.py
import pytest
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn


@pytest.mark.asyncio
async def test_floored_turn_creates_retry_queue_row(monkeypatch):
    inserted = {}

    class FakeRetryQueueStore:
        async def insert_pending(self, **kwargs):
            inserted.update(kwargs)
            return "retry-id-1"

    class FakeServices:
        memory_bridge = None
        retry_queue_store = FakeRetryQueueStore()

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    state = PipelineState(
        trace_id="trace-x", session_id="sess-x", input_text="prepare me for the interview",
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this...", is_final=False,
                chunk_index=0, trace_id="trace-x", owl_name="secretary", is_floor=True,
            ),
        ),
    )

    await persist_turn(state)

    assert inserted["trace_id"] == "trace-x"
    assert inserted["session_id"] == "sess-x"
    assert inserted["goal"] == "prepare me for the interview"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_turn_persist_retry_queue.py -v`
Expected: FAIL — `inserted` stays empty (no retry_queue insert happens yet), `KeyError: 'trace_id'`.

- [ ] **Step 3: Wire the insert into `persist_turn`**

In `src/stackowl/pipeline/turn_persist.py`, add the import and call the store right after the existing `floored = _turn_floored(state)` check (around line 82, before the existing `if floored:` block's early-return-on-empty-input path — the retry row must be inserted regardless of whether there's a user utterance to persist, since the floor already happened):

```python
from stackowl.pipeline.delivery_gate import _attempts_for_state  # add to existing imports
```

```python
    floored = _turn_floored(state)
    if floored:
        retry_store = getattr(services, "retry_queue_store", None)
        if retry_store is not None:
            try:
                banned = _attempts_for_state(state)
                await retry_store.insert_pending(
                    trace_id=state.trace_id,
                    session_id=state.session_id,
                    goal=state.input_text,
                    banned_capabilities=list(banned) if banned else [],
                )
            except Exception as exc:  # B5 — retry-queue bookkeeping must never block delivery
                log.scheduler.error(
                    "[pipeline] persist_turn: retry_queue insert failed",
                    exc_info=exc,
                    extra={"_fields": {"trace_id": state.trace_id}},
                )
        if not state.input_text:
```

(The `if not state.input_text:` line is the existing code immediately following — this snippet inserts the new block directly above it, inside the existing `if floored:` branch.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_turn_persist_retry_queue.py -v`
Expected: PASS

- [ ] **Step 5: Wire `retry_queue_store` into `StepServices`/`get_services()`**

Find the `StepServices` construction site (same place `memory_bridge` and `preference_store` are built — `src/stackowl/pipeline/services.py`) and add a `retry_queue_store: RetryQueueStore | None = None` field, constructed the same way `TaskOutcomeStore` is constructed wherever that happens (same `DbPool` instance, same `OwnedRepository` pattern). Grep `TaskOutcomeStore(` to find that construction site and mirror it exactly for `RetryQueueStore(db)`.

- [ ] **Step 6: Run full pipeline test suite for regressions**

Run: `uv run pytest tests/pipeline/ -x -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/pipeline/turn_persist.py src/stackowl/pipeline/services.py tests/pipeline/test_turn_persist_retry_queue.py
git commit -m "feat(pipeline): insert retry_queue row on floored turns"
```

---

### Task 4: Capture + backfill the sent Telegram message reference

**Files:**
- Modify: `src/stackowl/channels/telegram/adapter.py` (`_send_part` ~530, `_deliver` ~468, `send_text` ~449, `send` ~322)
- Test: `tests/channels/telegram/test_adapter_message_id_backfill.py`

**Interfaces:**
- Consumes: `RetryQueueStore.backfill_channel_message` (Task 2).
- Produces: `_send_part`, `_deliver`, `send_text` now return `telegram.Message | None` instead of `None` (return-type change — no other caller of these three methods relies on the old `None` return per the codebase's own usage, but grep before committing to confirm — see Step 4).

This closes a real pre-existing gap: `_send_part` (adapter.py:530-556) already calls `self._bot_app.bot.send_message(...)` but discards the returned `telegram.Message` — the only place in the codebase that keeps a sent message's `.message_id` today is the inline-keyboard branch (`send_inline_keyboard`, adapter.py:631-684).

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/telegram/test_adapter_message_id_backfill.py
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_send_part_returns_message(monkeypatch):
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter

    adapter = TelegramChannelAdapter.__new__(TelegramChannelAdapter)
    fake_message = MagicMock(message_id=4242)
    adapter._bot_app = MagicMock()
    adapter._bot_app.bot.send_message = AsyncMock(return_value=fake_message)

    result = await adapter._send_part(target=111, part="hello", idx=0)

    assert result is fake_message
    assert result.message_id == 4242
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_adapter_message_id_backfill.py -v`
Expected: FAIL — `assert result is fake_message` fails (`result` is `None`, current `_send_part` returns nothing).

- [ ] **Step 3: Update `_send_part` to return the sent message**

```python
# adapter.py:530-556, replace the body
async def _send_part(self, target: int, part: str, idx: int) -> "Message | None":
    """Send one message part, MarkdownV2-first with a plain-text fallback.

    Returns the sent :class:`telegram.Message` (used to backfill
    channel_chat_id/channel_message_id for floored turns — retry_queue_store)
    or ``None`` if both send attempts raised.
    """
    assert self._bot_app is not None
    async with traced_span(log.telegram, "telegram.send_message", idx=idx, len=len(part)):
        try:
            return await self._bot_app.bot.send_message(
                chat_id=target,
                text=part,
                parse_mode="MarkdownV2",
            )
        except BadRequest as exc:
            log.telegram.error(
                "[telegram] adapter.send_text: MarkdownV2 rejected — retrying as plain text",
                exc_info=exc,
                extra={"_fields": {"idx": idx, "len": len(part)}},
            )
            return await self._bot_app.bot.send_message(
                chat_id=target,
                text=part,
                parse_mode=None,
            )
```

- [ ] **Step 4: Propagate the return value up `_deliver` → `send_text` → `send`**

Grep first to confirm no caller depends on the old `-> None` contract:

Run: `grep -rn "_send_part(\|_deliver(\|\.send_text(" src/stackowl/`

Update `_deliver` (adapter.py:468) to collect and return the **last** part's message (floor messages are single-part in practice — multi-part messages only backfill the final visible part; this is a documented limitation, not a bug):

```python
async def _deliver(self, text: str, *, chat_id: int | None = _UNSET) -> "Message | None":
    # ... existing part-splitting logic unchanged ...
    last_message = None
    for idx, part in enumerate(parts):
        last_message = await self._send_part(target, part, idx)
    return last_message
```

Update `send_text` (adapter.py:449) to return what `_deliver` returns instead of discarding it:

```python
async def send_text(self, text: str, *, chat_id: int | None = _UNSET) -> "Message | None":
    # ... existing logic unchanged, just change the final `return` / add one ...
    return await self._deliver(text, chat_id=chat_id)
```

In `send()` (adapter.py:322), after accumulating chunks and calling `send_text` (or `send_text_or_actions` on the actions branch), check whether any accumulated chunk carried `is_floor=True` and backfill:

```python
        # after the existing send_text(...) / send_text_or_actions(...) call:
        message = await self.send_text(combined_text, chat_id=target)  # existing call, now capturing return
        floor_chunks = [c for c in accumulated_chunks if c.is_floor]
        if message is not None and floor_chunks:
            from stackowl.memory.retry_queue_store import RetryQueueStore
            from stackowl.pipeline.services import get_services

            retry_store = getattr(get_services(), "retry_queue_store", None)
            if retry_store is not None:
                try:
                    await retry_store.backfill_channel_message(
                        trace_id=floor_chunks[0].trace_id,
                        channel_chat_id=target,
                        channel_message_id=message.message_id,
                    )
                except Exception as exc:  # backfill must never break delivery
                    log.telegram.error(
                        "[telegram] adapter.send: retry_queue backfill failed",
                        exc_info=exc,
                        extra={"_fields": {"trace_id": floor_chunks[0].trace_id}},
                    )
```

(`accumulated_chunks` / `combined_text` / `target` are the existing local names already used in `send()` — match whatever the real local variable names are when editing; read the current `send()` body first since this plan's excerpt approximates the accumulation loop shape without reproducing the whole method.)

- [ ] **Step 5: Run the adapter test suite for regressions**

Run: `uv run pytest tests/channels/telegram/ -x -q`
Expected: PASS, no regressions (existing keyboard-branch and plain-text-branch tests still pass with the new return values).

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/channels/telegram/adapter.py tests/channels/telegram/test_adapter_message_id_backfill.py
git commit -m "fix(telegram): capture sent message_id, backfill retry_queue for floored turns"
```

---

### Task 5: `RetryActuator` — the shared retry function

**Files:**
- Create: `src/stackowl/pipeline/retry_actuator.py`
- Test: `tests/pipeline/test_retry_actuator.py`

**Interfaces:**
- Consumes: `RetryQueueStore` (Task 2), `RetryQueueRow` (Task 2), `stackowl.pipeline.state.PipelineState` (existing — construction pattern mirrors `goal_execution.py:164-177`), `stackowl.channels.registry.ChannelRegistry.instance().get(name) -> ChannelAdapter` (existing), backend's `async def run(state: PipelineState) -> PipelineState` (existing, same object `goal_execution.py` calls `self._backend.run(state)` on).
- Produces (used by Task 6, 7):
  - `@dataclass(frozen=True) class RetryOutcome: status: str  # "completed" | "pending" | "failed"`
  - `class RetryActuator: def __init__(self, *, backend, channel_registry, retry_store: RetryQueueStore) -> None`
  - `async def attempt_retry(self, row: RetryQueueRow) -> RetryOutcome`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_retry_actuator.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from stackowl.memory.retry_queue_store import RetryQueueRow
from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _row(**overrides):
    defaults = dict(
        id="retry-1", trace_id="trace-orig", session_id="sess-1",
        goal="prepare me for the interview", banned_capabilities=["cronjob"],
        attempt_count=0, status="pending", next_retry_at="", last_error=None,
        channel="telegram", channel_chat_id="555", channel_message_id="999",
        created_at="", updated_at="",
    )
    defaults.update(overrides)
    return RetryQueueRow(**defaults)


@pytest.mark.asyncio
async def test_attempt_retry_success_edits_message():
    row = _row()

    success_state = PipelineState(
        trace_id="trace-new", session_id="sess-1", input_text=row.goal,
        responses=(
            ResponseChunk(
                content="Here's your interview prep plan...", is_final=True,
                chunk_index=0, trace_id="trace-new", owl_name="secretary", is_floor=False,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=success_state)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    channel_registry = MagicMock()
    channel_registry.get = MagicMock(return_value=adapter)

    retry_store = MagicMock()
    retry_store.mark_completed = AsyncMock()

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=retry_store)
    outcome = await actuator.attempt_retry(row)

    assert outcome.status == "completed"
    adapter.edit_message.assert_awaited_once()
    retry_store.mark_completed.assert_awaited_once_with("retry-1")

    # banned capability must have been injected into the re-run prompt
    call_state = backend.run.await_args.args[0]
    assert "cronjob" in call_state.input_text


@pytest.mark.asyncio
async def test_attempt_retry_failure_marks_attempt():
    row = _row()

    floored_state = PipelineState(
        trace_id="trace-new", session_id="sess-1", input_text=row.goal,
        responses=(
            ResponseChunk(
                content="I still couldn't...", is_final=False, chunk_index=0,
                trace_id="trace-new", owl_name="secretary", is_floor=True,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=floored_state)

    channel_registry = MagicMock()
    retry_store = MagicMock()
    updated_row = _row(attempt_count=1, status="pending")
    retry_store.mark_attempt_failed = AsyncMock(return_value=updated_row)

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=retry_store)
    outcome = await actuator.attempt_retry(row)

    assert outcome.status == "pending"
    retry_store.mark_attempt_failed.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_retry_actuator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.pipeline.retry_actuator'`

- [ ] **Step 3: Write the implementation**

```python
# src/stackowl/pipeline/retry_actuator.py
"""RetryActuator — re-runs a floored turn's goal, steered away from the
capability that already failed.

Reuses the exact scheduled-turn pattern goal_execution.py already uses
(PipelineState construction + backend.run()) rather than inventing a second
way to inject a synthetic turn.

ponytail: capability avoidance is PROMPT-STEERED (the re-run's goal text
names the banned capabilities and asks the model not to use them again), not
a hard filter threaded through tool-selection. The model can still pick a
banned capability if it insists. Upgrade path: thread banned_capabilities
into execute.py's tool-selection as a real exclusion list if soft steering
proves unreliable in practice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.memory.retry_queue_store import RetryQueueRow, RetryQueueStore
from stackowl.pipeline.state import PipelineState

_STILL_FAILED_NOTICE = (
    "Still couldn't complete this after {attempts} tries: {goal}"
)


@dataclass(frozen=True, slots=True)
class RetryOutcome:
    status: str  # "completed" | "pending" | "failed"


class RetryActuator:
    """Shared retry function — called by both the cron sweep and manual retry."""

    def __init__(self, *, backend, channel_registry, retry_store: RetryQueueStore) -> None:
        self._backend = backend
        self._channel_registry = channel_registry
        self._retry_store = retry_store

    async def attempt_retry(self, row: RetryQueueRow) -> RetryOutcome:
        # 1. ENTRY
        log.scheduler.info(
            "retry_actuator.attempt_retry: entry",
            extra={"_fields": {
                "retry_id": row.id, "attempt_count": row.attempt_count,
                "banned_capabilities": row.banned_capabilities,
            }},
        )
        augmented_goal = self._augment_goal(row)
        trace_id = f"retry-{uuid.uuid4().hex[:8]}"
        state = PipelineState(
            trace_id=trace_id,
            session_id=row.session_id,
            input_text=augmented_goal,
            channel=row.channel,
            owl_name="secretary",
            pipeline_step="",
            interactive=False,
            defer_delivery=True,
        )
        try:
            final_state = await self._backend.run(state)
        except Exception as exc:  # never raise into the scheduler loop
            log.scheduler.error(
                "retry_actuator.attempt_retry: pipeline raised",
                exc_info=exc, extra={"_fields": {"retry_id": row.id}},
            )
            return await self._handle_failure(row, str(exc))

        floored = any(c.is_floor for c in final_state.responses)
        if floored:
            error_text = "retry attempt still floored"
            outcome = await self._handle_failure(row, error_text)
            # 4. EXIT
            log.scheduler.info(
                "retry_actuator.attempt_retry: exit",
                extra={"_fields": {"retry_id": row.id, "status": outcome.status}},
            )
            return outcome

        answer_text = "\n".join(c.content for c in final_state.responses if c.content).strip()
        await self._deliver_success(row, answer_text)
        await self._retry_store.mark_completed(row.id)
        log.scheduler.info(
            "retry_actuator.attempt_retry: exit",
            extra={"_fields": {"retry_id": row.id, "status": "completed"}},
        )
        return RetryOutcome(status="completed")

    def _augment_goal(self, row: RetryQueueRow) -> str:
        if not row.banned_capabilities:
            return row.goal
        banned = ", ".join(row.banned_capabilities)
        return (
            f"(Retry attempt {row.attempt_count + 1}: a previous attempt at this "
            f"same ask already failed using {banned} — try a genuinely different "
            f"approach or tool this time, do not repeat the same failed path.)\n\n"
            f"{row.goal}"
        )

    async def _deliver_success(self, row: RetryQueueRow, answer_text: str) -> None:
        adapter = self._channel_registry.get(row.channel)
        if row.channel_chat_id and row.channel_message_id:
            try:
                await adapter.edit_message(
                    int(row.channel_chat_id), int(row.channel_message_id), answer_text,
                )
                return
            except Exception as exc:  # edit can fail (message too old/deleted) — fall back
                log.telegram.error(
                    "retry_actuator._deliver_success: edit failed — sending new message",
                    exc_info=exc, extra={"_fields": {"retry_id": row.id}},
                )
        await adapter.send_text(answer_text, chat_id=int(row.channel_chat_id) if row.channel_chat_id else None)

    async def _handle_failure(self, row: RetryQueueRow, error: str) -> RetryOutcome:
        newly_failed = row.banned_capabilities[-1] if row.banned_capabilities else "unknown"
        updated = await self._retry_store.mark_attempt_failed(
            retry_id=row.id, newly_failed_capability=newly_failed, error=error,
        )
        if updated.status == "failed":
            await self._notify_gave_up(updated)
        return RetryOutcome(status=updated.status)

    async def _notify_gave_up(self, row: RetryQueueRow) -> None:
        if not row.channel_chat_id:
            return
        adapter = self._channel_registry.get(row.channel)
        text = _STILL_FAILED_NOTICE.format(attempts=row.attempt_count, goal=row.goal)
        try:
            await adapter.send_text(text, chat_id=int(row.channel_chat_id))
        except Exception as exc:  # notification best-effort
            log.telegram.error(
                "retry_actuator._notify_gave_up: notification send failed",
                exc_info=exc, extra={"_fields": {"retry_id": row.id}},
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_retry_actuator.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/retry_actuator.py tests/pipeline/test_retry_actuator.py
git commit -m "feat(pipeline): add RetryActuator, prompt-steered failure-aware retry"
```

---

### Task 6: Scheduler sweep — `retry_sweep.py`

**Files:**
- Create: `src/stackowl/scheduler/handlers/retry_sweep.py`
- Modify: `src/stackowl/scheduler/assembly.py` (seed the recurring job, same place `objective_driver` is seeded, ~line 788)
- Test: `tests/scheduler/test_retry_sweep.py`

**Interfaces:**
- Consumes: `RetryActuator.attempt_retry` (Task 5), `RetryQueueStore.get_due` (Task 2), `stackowl.scheduler.base.JobHandler`/`HandlerRegistry` (existing), `stackowl.scheduler.job.Job`/`JobResult` (existing).
- Produces: `RetrySweepHandler` (`handler_name = "retry_sweep"`), `register_retry_sweep_handler(actuator, retry_store) -> RetrySweepHandler`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_retry_sweep.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from stackowl.memory.retry_queue_store import RetryQueueRow
from stackowl.pipeline.retry_actuator import RetryOutcome
from stackowl.scheduler.handlers.retry_sweep import RetrySweepHandler
from stackowl.scheduler.job import Job


def _row(id_="r1"):
    return RetryQueueRow(
        id=id_, trace_id="t1", session_id="s1", goal="g", banned_capabilities=[],
        attempt_count=0, status="pending", next_retry_at="", last_error=None,
        channel="telegram", channel_chat_id="1", channel_message_id="2",
        created_at="", updated_at="",
    )


@pytest.mark.asyncio
async def test_sweep_retries_all_due_rows():
    retry_store = MagicMock()
    retry_store.get_due = AsyncMock(return_value=[_row("r1"), _row("r2")])

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock(return_value=RetryOutcome(status="completed"))

    handler = RetrySweepHandler(actuator=actuator, retry_store=retry_store)
    job = Job(
        job_id="j1", handler_name="retry_sweep", schedule="every 1m",
        idempotency_key="k1", last_run_at=None, next_run_at="", status="pending",
    )

    result = await handler.execute(job)

    assert result.success is True
    assert actuator.attempt_retry.await_count == 2


@pytest.mark.asyncio
async def test_sweep_never_raises_on_actuator_failure():
    retry_store = MagicMock()
    retry_store.get_due = AsyncMock(return_value=[_row("r1")])

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock(side_effect=RuntimeError("boom"))

    handler = RetrySweepHandler(actuator=actuator, retry_store=retry_store)
    job = Job(
        job_id="j1", handler_name="retry_sweep", schedule="every 1m",
        idempotency_key="k1", last_run_at=None, next_run_at="", status="pending",
    )

    result = await handler.execute(job)  # must not raise
    assert result.success is True  # sweep itself succeeded even if one row's retry errored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_retry_sweep.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.scheduler.handlers.retry_sweep'`

- [ ] **Step 3: Write the implementation**

```python
# src/stackowl/scheduler/handlers/retry_sweep.py
"""RetrySweepHandler — periodically retries due retry_queue rows.

Mirrors the ClarifySweepHandler structure: a JobHandler subclass plus a
module-level register_retry_sweep_handler factory. The recurring JOB row is
seeded separately in scheduler/assembly.py (same place objective_driver is
seeded, every 1m).
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.memory.retry_queue_store import RetryQueueStore
from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult


class RetrySweepHandler(JobHandler):
    """Recurring sweep of due retry_queue rows — retries each via RetryActuator."""

    def __init__(self, *, actuator: RetryActuator, retry_store: RetryQueueStore) -> None:
        self._actuator = actuator
        self._retry_store = retry_store

    @property
    def handler_name(self) -> str:
        return "retry_sweep"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.info(
            "[scheduler] retry_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        retried = 0
        errored = 0
        try:
            due = await self._retry_store.get_due()
        except Exception as exc:
            log.scheduler.error(
                "[scheduler] retry_sweep.execute: get_due failed — treating as empty",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
            due = []

        for row in due:
            try:
                await self._actuator.attempt_retry(row)
                retried += 1
            except Exception as exc:  # self-healing — one bad row must not stop the sweep
                errored += 1
                log.scheduler.error(
                    "[scheduler] retry_sweep.execute: attempt_retry raised for row",
                    exc_info=exc, extra={"_fields": {"job_id": job.job_id, "retry_id": row.id}},
                )

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] retry_sweep.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "retried": retried, "errored": errored,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, success=True,
            output=f"retried={retried} errored={errored}", error=None,
            duration_ms=duration_ms, effect_class="state_change",
        )


def register_retry_sweep_handler(
    *, actuator: RetryActuator, retry_store: RetryQueueStore,
) -> RetrySweepHandler:
    """Construct and register the RetrySweepHandler singleton.

    Mirrors register_clarify_sweep_handler. The recurring JOB row itself is
    seeded separately in scheduler/assembly.py.
    """
    handler = RetrySweepHandler(actuator=actuator, retry_store=retry_store)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] retry_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
    return handler
```

- [ ] **Step 4: Seed the recurring job in `assembly.py`**

In `src/stackowl/scheduler/assembly.py`, next to the existing `objective_driver` seed (~line 788):

```python
        # Retry sweep — retries floored turns every minute, capped at 3
        # attempts per retry_queue row (RetrySweepHandler / RetryActuator).
        await _seed_minutes_schedule(
            db, handler_name="retry_sweep", schedule="every 1m",
            interval_minutes=1,
        )
```

Also call `register_retry_sweep_handler(actuator=..., retry_store=...)` at whatever startup site registers `register_clarify_sweep_handler` (grep for that call to find it) — construct `RetryActuator` there with the process's real `backend`, `ChannelRegistry.instance()`, and a `RetryQueueStore(db)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_retry_sweep.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/scheduler/handlers/retry_sweep.py src/stackowl/scheduler/assembly.py tests/scheduler/test_retry_sweep.py
git commit -m "feat(scheduler): add retry_sweep — 1-minute cron retry of floored turns"
```

---

### Task 7: Manual "do it again" — retry-intent classifier + triage hook

**Files:**
- Create: `src/stackowl/interaction/retry_intent_classifier.py`
- Modify: `src/stackowl/pipeline/steps/triage.py` (first step in `PIPELINE_STEPS`, `registry.py:35` — earliest hook before normal turn handling)
- Test: `tests/interaction/test_retry_intent_classifier.py`, `tests/pipeline/test_triage_retry_intent.py`

**Interfaces:**
- Consumes: `RetryQueueStore.get_latest_pending_for_session` (Task 2), `RetryActuator.attempt_retry` (Task 5), `stackowl.providers.registry.ProviderRegistry` (existing — same type `FeedbackClassifier.__init__` takes).
- Produces: `RetryIntentClassifier(provider_registry, *, timeout_s=10.0, abstain_threshold=0.5)` with `async def classify(self, *, user_message: str, prior_goal: str) -> bool` (True = this message is asking to retry the prior failed ask).

- [ ] **Step 1: Write the failing test for the classifier**

```python
# tests/interaction/test_retry_intent_classifier.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from stackowl.interaction.retry_intent_classifier import RetryIntentClassifier


@pytest.mark.asyncio
async def test_classify_retry_phrase_returns_true():
    provider_registry = MagicMock()
    fake_provider = MagicMock()
    fake_provider.complete = AsyncMock(return_value='{"is_retry": true, "confidence": 0.9}')
    provider_registry.get_fast = MagicMock(return_value=fake_provider)

    classifier = RetryIntentClassifier(provider_registry)
    result = await classifier.classify(user_message="do it again", prior_goal="prepare me for the interview")

    assert result is True


@pytest.mark.asyncio
async def test_classify_unrelated_message_returns_false():
    provider_registry = MagicMock()
    fake_provider = MagicMock()
    fake_provider.complete = AsyncMock(return_value='{"is_retry": false, "confidence": 0.95}')
    provider_registry.get_fast = MagicMock(return_value=fake_provider)

    classifier = RetryIntentClassifier(provider_registry)
    result = await classifier.classify(user_message="what's the weather", prior_goal="prepare me for the interview")

    assert result is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/interaction/test_retry_intent_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the classifier**

Read `src/stackowl/interaction/feedback_classifier.py` first and match its exact provider-call shape (how it calls `provider_registry`, parses JSON, handles timeout/abstain) — the plan mirrors its constructor signature (`provider_registry, *, timeout_s=10.0, abstain_threshold=0.5`) but the concrete `provider.complete(...)`/prompt-building call must match `FeedbackClassifier`'s real implementation byte-for-byte in shape (same provider call pattern), not reinvent it. Implement:

```python
# src/stackowl/interaction/retry_intent_classifier.py
"""RetryIntentClassifier — LLM-based detection of "do it again" intent.

No hardcoded keyword list (multilingual, per repo convention) — mirrors
FeedbackClassifier's shape: fast-tier provider call, JSON verdict, abstain
below confidence threshold. Only invoked when the session has an open
pending retry_queue row (checked by the caller before classifying).
"""

from __future__ import annotations

import json

from stackowl.infra.observability import log

_PROMPT_TEMPLATE = """A user previously asked: "{prior_goal}"
That request failed and the user was told so. The user just sent this new message:
"{user_message}"

Is the user asking to retry/redo the SAME prior failed request (not asking
something new and unrelated)? Respond with ONLY this JSON, no other text:
{{"is_retry": true|false, "confidence": 0.0-1.0}}"""


class RetryIntentClassifier:
    def __init__(self, provider_registry, *, timeout_s: float = 10.0, abstain_threshold: float = 0.5) -> None:
        self._provider_registry = provider_registry
        self._timeout_s = timeout_s
        self._abstain_threshold = abstain_threshold

    async def classify(self, *, user_message: str, prior_goal: str) -> bool:
        log.tool.debug(
            "retry_intent_classifier.classify: entry",
            extra={"_fields": {"user_message_len": len(user_message)}},
        )
        provider = self._provider_registry.get_fast()
        prompt = _PROMPT_TEMPLATE.format(prior_goal=prior_goal, user_message=user_message)
        try:
            raw = await provider.complete(prompt)
            data = json.loads(raw)
            confidence = float(data.get("confidence", 0.0))
            is_retry = bool(data.get("is_retry", False))
        except Exception as exc:
            log.tool.error(
                "retry_intent_classifier.classify: provider call/parse failed — abstaining",
                exc_info=exc, extra={"_fields": {}},
            )
            return False
        if confidence < self._abstain_threshold:
            log.tool.debug(
                "retry_intent_classifier.classify: below abstain threshold",
                extra={"_fields": {"confidence": confidence}},
            )
            return False
        log.tool.info(
            "retry_intent_classifier.classify: exit",
            extra={"_fields": {"is_retry": is_retry, "confidence": confidence}},
        )
        return is_retry
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/interaction/test_retry_intent_classifier.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing test for the triage hook**

```python
# tests/pipeline/test_triage_retry_intent.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from stackowl.memory.retry_queue_store import RetryQueueRow
from stackowl.pipeline.state import PipelineState


def _row():
    return RetryQueueRow(
        id="r1", trace_id="t1", session_id="s1", goal="prepare me for the interview",
        banned_capabilities=[], attempt_count=0, status="pending", next_retry_at="",
        last_error=None, channel="telegram", channel_chat_id="1", channel_message_id="2",
        created_at="", updated_at="",
    )


@pytest.mark.asyncio
async def test_triage_triggers_manual_retry(monkeypatch):
    from stackowl.pipeline.steps import triage

    retry_store = MagicMock()
    retry_store.get_latest_pending_for_session = AsyncMock(return_value=_row())

    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value=True)

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock()

    class FakeServices:
        retry_queue_store = retry_store
        retry_intent_classifier = classifier
        retry_actuator = actuator

    monkeypatch.setattr("stackowl.pipeline.steps.triage.get_services", lambda: FakeServices())

    state = PipelineState(trace_id="t2", session_id="s1", input_text="do it again")
    result = await triage.run(state)

    actuator.attempt_retry.assert_awaited_once()
    assert result.retry_dispatched is True
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_triage_retry_intent.py -v`
Expected: FAIL — `triage.run` doesn't check retry intent yet, `retry_dispatched` field doesn't exist on `PipelineState`.

- [ ] **Step 7: Wire the check into `triage.run`**

Read `src/stackowl/pipeline/steps/triage.py` first to find its current entry point and `PipelineState` field conventions. Add a `retry_dispatched: bool = False` field to `PipelineState` (`state.py`), and at the top of `triage.run`, before its existing logic:

```python
    services = get_services()
    retry_store = getattr(services, "retry_queue_store", None)
    if retry_store is not None:
        pending = await retry_store.get_latest_pending_for_session(state.session_id)
        if pending is not None:
            classifier = services.retry_intent_classifier
            is_retry = await classifier.classify(
                user_message=state.input_text, prior_goal=pending.goal,
            )
            if is_retry:
                await services.retry_actuator.attempt_retry(pending)
                return state.evolve(retry_dispatched=True)
```

(Exact placement/return-short-circuit must match `triage.run`'s real control flow — read the file first; this is the logical shape, not a byte-exact diff, since the plan author has not read triage.py's full current body.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_triage_retry_intent.py tests/interaction/test_retry_intent_classifier.py -v`
Expected: PASS

- [ ] **Step 9: Run full pipeline suite for regressions**

Run: `uv run pytest tests/pipeline/ tests/interaction/ -x -q`
Expected: PASS, no regressions.

- [ ] **Step 10: Commit**

```bash
git add src/stackowl/interaction/retry_intent_classifier.py src/stackowl/pipeline/steps/triage.py src/stackowl/pipeline/state.py tests/interaction/test_retry_intent_classifier.py tests/pipeline/test_triage_retry_intent.py
git commit -m "feat(pipeline): manual do-it-again retries the same failure-aware path"
```

---

### Task 8: End-to-end regression pass

**Files:**
- Test: `tests/scheduler/test_retry_loop_e2e.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7.

- [ ] **Step 1: Write the end-to-end test**

```python
# tests/scheduler/test_retry_loop_e2e.py
"""Full loop: floor -> retry_queue row -> sweep retries -> success edits message."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from stackowl.db.pool import DbPool
from stackowl.memory.retry_queue_store import RetryQueueStore
from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.scheduler.handlers.retry_sweep import RetrySweepHandler
from stackowl.scheduler.job import Job


@pytest.mark.asyncio
async def test_full_retry_loop_success(tmp_path):
    db = DbPool(tmp_path / "test.db")
    await db.open()
    await db.execute("""
        CREATE TABLE retry_queue (
            id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, session_id TEXT NOT NULL,
            goal TEXT NOT NULL, banned_capabilities TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            next_retry_at TEXT NOT NULL, last_error TEXT, channel TEXT NOT NULL DEFAULT 'telegram',
            channel_chat_id TEXT, channel_message_id TEXT, owner_id TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    store = RetryQueueStore(db)
    await store.insert_pending(
        trace_id="trace-1", session_id="sess-1", goal="prepare me for the interview",
        banned_capabilities=["cronjob"],
    )
    await store.backfill_channel_message(trace_id="trace-1", channel_chat_id=555, channel_message_id=999)

    success_state = PipelineState(
        trace_id="trace-2", session_id="sess-1", input_text="prepare me for the interview",
        responses=(ResponseChunk(
            content="Here's your plan...", is_final=True, chunk_index=0,
            trace_id="trace-2", owl_name="secretary", is_floor=False,
        ),),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=success_state)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    channel_registry = MagicMock()
    channel_registry.get = MagicMock(return_value=adapter)

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=store)
    handler = RetrySweepHandler(actuator=actuator, retry_store=store)
    job = Job(
        job_id="j1", handler_name="retry_sweep", schedule="every 1m",
        idempotency_key="k1", last_run_at=None, next_run_at="", status="pending",
    )

    result = await handler.execute(job)

    assert result.success is True
    adapter.edit_message.assert_awaited_once_with(555, 999, "Here's your plan...")

    remaining_due = await store.get_due()
    assert remaining_due == []  # row is now completed, no longer due

    await db.close()
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/scheduler/test_retry_loop_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Run the full targeted test suite**

Run: `uv run pytest tests/db/test_migration_0082.py tests/memory/test_retry_queue_store.py tests/pipeline/test_turn_persist_retry_queue.py tests/channels/telegram/test_adapter_message_id_backfill.py tests/pipeline/test_retry_actuator.py tests/scheduler/test_retry_sweep.py tests/interaction/test_retry_intent_classifier.py tests/pipeline/test_triage_retry_intent.py tests/scheduler/test_retry_loop_e2e.py -v`
Expected: PASS, all green.

- [ ] **Step 4: Run `ruff` and `mypy`**

Run: `uv run ruff check src/ && uv run mypy src/`
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add tests/scheduler/test_retry_loop_e2e.py
git commit -m "test(scheduler): add end-to-end retry loop regression test"
```

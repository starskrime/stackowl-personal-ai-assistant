# Plan B — Long-Term Memory Fill (DreamWorker-integrated extraction) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **REVISED 2026-05-30** after de-risking the scheduler API. The original "consolidate enqueues a one-shot job every N turns" approach was discarded: the scheduler has **no one-shot token**, `create_job` does **not dedupe** (per-turn calls accumulate job rows), and `FactExtractionJobHandler` hard-codes its idempotency key as `fact_extraction:<session_id>`. User vote (2026-05-30): **fold extraction into the DreamWorker** — the existing nightly memory-consolidation job. This supersedes the prior Plan B revision.

**Goal:** Make long-term recall return facts by closing the gaps that keep `committed_facts` empty — extraction now runs as part of the scheduled DreamWorker cycle, reading conversation turns from where they actually live (`staged_facts`).

**Architecture:** A new `ConversationMiner` (memory package) reads recent staged conversation turns per session, runs the existing `FactExtractor`, and stages the resulting high-confidence facts — **idempotently** (content-hash dedup so re-mining the same turns nightly does not duplicate). The `DreamWorkerJobHandler` invokes the miner once at the start of each consolidation pass (before its phase loop), so the existing promotion step then fills `committed_facts`, which `recall()` reads. Fixes RC-A.

**Tech Stack:** Python 3, aiosqlite, pytest, asyncio. Scheduler (DreamWorker = `daily@03:00`).

**BMad boundaries honored:** B9 (extraction runs inside a registered scheduler handler, never direct dispatch); B5 (every catch logs); B1 (memory package owns mining; no pipeline import); reuses existing stores (`staged_facts`, `committed_facts`) — no new aggregators.

---

## Root-cause recap (gaps, all confirmed)

1. `FactExtractionJobHandler._fetch_messages` reads `messages JOIN conversations` (extraction_handler.py:22-29) — both tables are **empty with no writers** anywhere.
2. Nothing ever runs extraction during normal operation (assembly.py:17-19 admits the trigger was never wired).
3. Conversation turns live in `staged_facts` (`source_type='conversation'`, written by consolidate.py:25); `recall()` reads only `committed_facts`, which stays empty.
4. **(De-risk finding)** Extracted-fact `fact_id`s are random UUIDs (models.py:22), so naive re-extraction each night would pile up duplicate staged facts. Mining must be idempotent.

## Promotion-gate note (no silent cap)
Even after extraction works, the promotion gate is `confidence >= 0.8 AND reinforcement_count >= 3` (fact_promoter.py / settings `reinforcement_required`, default 3). A freshly-extracted fact has `reinforcement_count=0`, so it promotes only after being corroborated across ≥3 dream cycles. This is by design (only well-established facts become long-term). Surfaced as a tunable in Task 4; default unchanged.

---

## Cross-plan finding (must fix first — Task 0)

`FactExtractor` stamps extracted facts with `source_type="conversation"` (fact_extractor.py:151) — the SAME type as raw turns. Left as-is this would (a) make extracted facts show up in Plan A's `recent_conversation_turns` short-term history (which filters `source_type='conversation'`), corrupting it, and (b) make mining re-extract from its own output. Fix: extracted facts get a DISTINCT `source_type` (`"conversation_fact"`). This automatically excludes them from `recent_conversation_turns` (still filters `'conversation'`), so it also REMOVES the Plan A pollution risk. Promotion/recall are `source_type`-agnostic, so committed-fact recall is unaffected. Constant: `EXTRACTED_FACT_SOURCE_TYPE = "conversation_fact"`.

### Task 0: Give extracted facts a distinct source_type
- Modify `src/stackowl/memory/fact_extractor.py:151`: change `source_type="conversation"` to `source_type="conversation_fact"` (define a module constant `EXTRACTED_FACT_SOURCE_TYPE = "conversation_fact"` and use it).
- Test (`tests/memory/test_plan_b_conversation_miner.py`): assert `FactExtractor.extract(...)` returns facts whose `source_type == "conversation_fact"` (stub the provider/LLM call — grep how existing fact_extractor tests stub it; TestModeGuard blocks live calls).
- Verify no consumer depended on extracted facts being `'conversation'`: grep `source_type` usages; the only `'conversation'` readers are `recent_conversation_turns` (wants raw turns only — correct to exclude) and the dormant `FactExtractionJobHandler` (unaffected). Promotion (`fact_promoter`) is source_type-agnostic — confirm.
- Commit: `fix(v2): tag extracted facts distinct from raw conversation turns (RC-A; protects Plan A short-term history)`.

Task 1's dedup/exists SQL below uses `source_type='conversation_fact'` (NOT `'fact'`); the distinct-sessions query stays `source_type='conversation'` (raw turns).

## File Structure

- Modify: `src/stackowl/memory/fact_extractor.py` — extracted facts get a distinct source_type (Task 0).
- Create: `src/stackowl/memory/conversation_miner.py` — `ConversationMiner`: mine staged conversation turns → extract → stage (idempotent).
- Modify: `src/stackowl/memory/dream_worker.py` — accept an optional `miner`; call `miner.mine_all()` at the start of `execute()` (B5-guarded).
- Modify: `src/stackowl/scheduler/handlers/dream_worker.py` — `register_dream_worker_handler` accepts + forwards an optional `miner`.
- Modify: `src/stackowl/memory/assembly.py` — build a `ConversationMiner` from the already-constructed `fact_extractor` + `bridge` + `db`, pass it into `register_dream_worker_handler`.
- Test: `tests/memory/test_plan_b_conversation_miner.py`, `tests/memory/test_plan_b_dreamworker_mining.py`.

---

### Task 1: `ConversationMiner` — idempotent staged-turn mining

**Files:**
- Create: `src/stackowl/memory/conversation_miner.py`
- Test: `tests/memory/test_plan_b_conversation_miner.py`

Dedup approach (chosen): **content-hash on the staged fact**. Before staging an extracted fact, compute a stable hash of `(source_ref, normalized content)` and skip if a staged/committed fact with that hash already exists. Implementation: query `SELECT 1 FROM staged_facts WHERE source_type='fact' AND source_ref=? AND content=? LIMIT 1` (and the same against `committed_facts`) before staging; skip on hit. (Exact-content match is sufficient and needs no schema change; a future migration could add a hash column if profiling shows the equality scan is hot — track in Phase 2.)

- [ ] **Step 1: Write failing tests** `tests/memory/test_plan_b_conversation_miner.py`:
```python
import pytest
from stackowl.memory.conversation_miner import ConversationMiner
from stackowl.memory.models import StagedFact


class _StubExtractor:
    """Returns one fact per call, derived from the messages, confidence 0.9."""
    def __init__(self): self.calls = []
    async def extract(self, messages, session_id):
        self.calls.append((session_id, len(messages)))
        return [StagedFact(content=f"fact about {session_id}", source_type="fact",
                           source_ref=session_id, confidence=0.9)]


@pytest.mark.asyncio
async def test_mine_session_extracts_and_stages(make_db, make_bridge):
    db = await make_db(); bridge = make_bridge(db)
    await bridge.store("User: I live in Baku\n\nAssistant: Noted.", "s1")
    miner = ConversationMiner(db=db, extractor=_StubExtractor(), bridge=bridge, message_limit=20)
    staged = await miner.mine_session("s1")
    assert staged == 1
    # the staged fact is queryable
    rows = await db.fetch_all(
        "SELECT content FROM staged_facts WHERE source_type='fact' AND source_ref=?", ("s1",))
    assert any("fact about s1" in r["content"] for r in rows)


@pytest.mark.asyncio
async def test_mine_session_is_idempotent(make_db, make_bridge):
    db = await make_db(); bridge = make_bridge(db)
    await bridge.store("User: I live in Baku\n\nAssistant: Noted.", "s1")
    ex = _StubExtractor()
    miner = ConversationMiner(db=db, extractor=ex, bridge=bridge, message_limit=20)
    await miner.mine_session("s1")
    second = await miner.mine_session("s1")  # same turns again
    assert second == 0  # nothing new staged (content-dedup)
    rows = await db.fetch_all(
        "SELECT count(*) AS n FROM staged_facts WHERE source_type='fact' AND source_ref='s1'")
    assert rows[0]["n"] == 1


@pytest.mark.asyncio
async def test_mine_all_iterates_distinct_sessions(make_db, make_bridge):
    db = await make_db(); bridge = make_bridge(db)
    await bridge.store("User: a\n\nAssistant: b", "s1")
    await bridge.store("User: c\n\nAssistant: d", "s2")
    ex = _StubExtractor()
    miner = ConversationMiner(db=db, extractor=ex, bridge=bridge, message_limit=20)
    total = await miner.mine_all()
    assert total == 2
    assert {c[0] for c in ex.calls} == {"s1", "s2"}
```
> Implementer: reuse existing test DB/bridge fixtures under tests/memory (grep `make_bridge`/conftest). Confirm `StagedFact` field names + that `source_type="fact"` is the right literal for extracted facts (grep how FactExtractor builds StagedFacts — fact_extractor.py:149). Match it.

- [ ] **Step 2:** Run → fail (module missing).

- [ ] **Step 3: Implement** `src/stackowl/memory/conversation_miner.py`:
```python
"""ConversationMiner — extract long-term facts from staged conversation turns.

RC-A fix: conversation turns are persisted to staged_facts(source_type='conversation')
but recall() reads only committed_facts. This miner (run by the DreamWorker) extracts
durable facts from those turns and stages them so the promotion step can commit them.
Idempotent: re-mining the same turns does not create duplicate facts (content dedup).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.exceptions import DuplicateFactError
from stackowl.infra.observability import log
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.db.pool import DbPool
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_extractor import FactExtractor

_DISTINCT_SESSIONS_SQL = (
    "SELECT DISTINCT source_ref FROM staged_facts WHERE source_type = 'conversation'"
)
_EXISTS_STAGED_SQL = (
    "SELECT 1 FROM staged_facts WHERE source_type='fact' AND source_ref=? AND content=? LIMIT 1"
)
_EXISTS_COMMITTED_SQL = (
    "SELECT 1 FROM committed_facts WHERE source_ref=? AND content=? LIMIT 1"
)


def _parse_turns(contents: list[str]) -> list[Message]:
    """Parse stored "User: X\n\nAssistant: Y" rows into Message turns (oldest-first)."""
    msgs: list[Message] = []
    for content in contents:
        user_part, _, assistant_part = content.partition("\n\nAssistant:")
        user_text = user_part.removeprefix("User:").strip()
        assistant_text = assistant_part.strip()
        if user_text:
            msgs.append(Message(role="user", content=user_text))
        if assistant_text:
            msgs.append(Message(role="assistant", content=assistant_text))
    return msgs


class ConversationMiner:
    """Mines staged conversation turns into staged long-term facts (idempotent)."""

    def __init__(self, db: DbPool, extractor: FactExtractor, bridge: MemoryBridge,
                 message_limit: int = 40) -> None:
        self._db = db
        self._extractor = extractor
        self._bridge = bridge
        self._message_limit = message_limit

    async def mine_all(self) -> int:
        """Mine every session that has conversation turns. Returns facts staged."""
        log.memory.info("[memory] conversation_miner.mine_all: entry")
        rows = await self._db.fetch_all(_DISTINCT_SESSIONS_SQL)
        total = 0
        for row in rows:
            session_id = row["source_ref"]
            try:
                total += await self.mine_session(session_id)
            except Exception as exc:  # B5 — one bad session must not abort the rest
                log.memory.error(
                    "[memory] conversation_miner.mine_all: session failed — skipping",
                    exc_info=exc, extra={"_fields": {"session_id": session_id}},
                )
        log.memory.info("[memory] conversation_miner.mine_all: exit",
                        extra={"_fields": {"sessions": len(rows), "staged": total}})
        return total

    async def mine_session(self, session_id: str) -> int:
        """Extract + stage facts for one session. Returns count of NEW facts staged."""
        turns = await self._bridge.recent_conversation_turns(
            session_id=session_id, limit=self._message_limit)
        messages = _parse_turns([t.content for t in turns])
        if not messages:
            return 0
        facts = await self._extractor.extract(messages, session_id)
        staged = 0
        for fact in facts:
            if await self._already_present(fact.source_ref, fact.content):
                continue
            try:
                await self._bridge.stage(fact)
                staged += 1
            except DuplicateFactError as exc:  # B5 — expected on id collision; skip
                log.memory.warning(
                    "[memory] conversation_miner: duplicate fact — skipping",
                    exc_info=exc, extra={"_fields": {"fact_id": fact.fact_id}})
        return staged

    async def _already_present(self, source_ref: str, content: str) -> bool:
        if await self._db.fetch_all(_EXISTS_STAGED_SQL, (source_ref, content)):
            return True
        if await self._db.fetch_all(_EXISTS_COMMITTED_SQL, (source_ref, content)):
            return True
        return False
```
> Implementer: verify `committed_facts` has `source_ref` + `content` columns (it does — recall reads them); adjust the EXISTS SQL column names if they differ. Verify `MemoryBridge.stage` + `recent_conversation_turns` signatures (sqlite_bridge.py).

- [ ] **Step 4:** Run → all 3 pass. `uv run python -c "import stackowl.memory.conversation_miner"`.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/memory/conversation_miner.py tests/memory/test_plan_b_conversation_miner.py
git commit -m "feat(v2): ConversationMiner extracts long-term facts from staged turns (RC-A)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: DreamWorker runs the miner each pass

**Files:**
- Modify: `src/stackowl/memory/dream_worker.py`
- Test: `tests/memory/test_plan_b_dreamworker_mining.py`

- [ ] **Step 1: Write failing test** (test the wiring without running full `execute()`, which is TestModeGuard-blocked). Assert the constructor accepts a `miner` and that the mining hook calls it. Approach: add a small internal method `_mine(self)` that does `if self._miner is not None: return await self._miner.mine_all()` and call THAT from `execute()`; the test calls `_mine` directly with a spy miner.
```python
import pytest


class _SpyMiner:
    def __init__(self): self.called = 0
    async def mine_all(self): self.called += 1; return 3


@pytest.mark.asyncio
async def test_dreamworker_accepts_and_runs_miner():
    from stackowl.memory.dream_worker import DreamWorkerJobHandler
    spy = _SpyMiner()
    h = DreamWorkerJobHandler(bridge=None, promoter=None, pruner=None,
                              kuzu_handler=None, detector=None, miner=spy)
    staged = await h._mine()
    assert staged == 3 and spy.called == 1


@pytest.mark.asyncio
async def test_dreamworker_mine_noop_without_miner():
    from stackowl.memory.dream_worker import DreamWorkerJobHandler
    h = DreamWorkerJobHandler(bridge=None, promoter=None, pruner=None,
                              kuzu_handler=None, detector=None)
    assert await h._mine() == 0  # None-safe
```

- [ ] **Step 2:** Run → fail (`miner` kwarg / `_mine` missing).

- [ ] **Step 3: Implement.** In `src/stackowl/memory/dream_worker.py`:
  - Add `miner: ConversationMiner | None = None` to `__init__` (and the TYPE_CHECKING import), store `self._miner = miner`.
  - Add the method:
```python
    async def _mine(self) -> int:
        """Mine staged conversation turns into staged facts. None-safe, B5-guarded."""
        if self._miner is None:
            return 0
        try:
            return await self._miner.mine_all()
        except Exception as exc:  # B5 — mining must not fail the consolidation pass
            log.memory.error("[memory] dream_worker: mining failed — continuing",
                             exc_info=exc)
            return 0
```
  - In `execute()`, after `TestModeGuard.assert_not_test_mode(...)` (line ~85) and before the resume/phase logic, add: `await self._mine()`. (Runs once per pass; idempotent via Task 1's dedup. Placed before promotion so newly-staged facts are visible to that pass's promotion scan.)

- [ ] **Step 4:** Run → pass. `uv run python -c "import stackowl.memory.dream_worker"`.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/memory/dream_worker.py tests/memory/test_plan_b_dreamworker_mining.py
git commit -m "feat(v2): DreamWorker mines conversation facts each consolidation pass (RC-A)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wire the miner through registration + assembly

**Files:**
- Modify: `src/stackowl/scheduler/handlers/dream_worker.py`
- Modify: `src/stackowl/memory/assembly.py`
- Test: covered by Task 4 e2e + an import smoke.

- [ ] **Step 1:** In `register_dream_worker_handler` (scheduler/handlers/dream_worker.py:38), add a trailing param `miner: ConversationMiner | None = None` (+ TYPE_CHECKING import) and forward it: `DreamWorkerJobHandler(..., miner=miner)`.

- [ ] **Step 2:** In `src/stackowl/memory/assembly.py`, after `fact_extractor` is built (assembly.py:189) and before/around the `register_dream_worker_handler(...)` call (assembly.py:173), construct the miner and pass it:
```python
        from stackowl.memory.conversation_miner import ConversationMiner

        conversation_miner = ConversationMiner(
            db=db, extractor=fact_extractor, bridge=bridge,
            message_limit=mem.extraction_after_n_messages * 4,
        )
```
> Ordering note: `register_dream_worker_handler` is currently called at assembly.py:173, BEFORE `fact_extractor` is built at :189. MOVE the `register_dream_worker_handler(...)` call to AFTER `fact_extractor` (and the miner) are constructed, so the miner can be passed in. Verify nothing between :173 and :189 depends on the dream worker handler already being registered (it doesn't — seed_dream_worker_schedule only needs the DB). Then add `miner=conversation_miner` to the call.

- [ ] **Step 3:** Add `conversation_miner` to the `MemoryComponents` dataclass + return (optional but consistent with the assembly's pattern of exposing wired components). If added, update the frozen dataclass field list.

- [ ] **Step 4:** Smoke: `uv run python -c "import stackowl.memory.assembly, stackowl.scheduler.handlers.dream_worker"`. Run the existing memory-assembly test if present (grep `MemoryAssembly` in tests/) to ensure the reordering didn't break wiring.

- [ ] **Step 5: Commit**
```bash
git add src/stackowl/scheduler/handlers/dream_worker.py src/stackowl/memory/assembly.py
git commit -m "feat(v2): wire ConversationMiner into DreamWorker assembly (RC-A)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: End-to-end fill + promotion-gate documentation

**Files:**
- Test: `tests/memory/test_plan_b_conversation_miner.py` (append e2e)
- Modify: `src/stackowl/config/settings.py` (doc comment only)

- [ ] **Step 1: Write an integration test** that: stores conversation turns; runs `ConversationMiner.mine_all()` with a stub extractor returning a `confidence>=0.8` fact; then drives the `FactPromoter` directly with `reinforcement_required` lowered to 1 (constructor arg) to prove the chain reaches `committed_facts`; finally asserts `bridge.recall("...")` returns the fact. (Stub the extractor — no live LLM on this box; FactPromoter is real.)

- [ ] **Step 2:** Run → pass. `uv run pytest tests/memory/test_plan_b_conversation_miner.py -v`.

- [ ] **Step 3: Document the gate as a tunable.** In `src/stackowl/config/settings.py`, extend the `reinforcement_required` field doc: *"How many dream-cycle corroborations before a staged fact is promoted to committed_facts (long-term recall). Lower to 1 for faster long-term recall at the cost of admitting less-established facts."* Do not change the default.

- [ ] **Step 4: Phase 2 backlog notes** — record: (a) optional `content_hash` column on `staged_facts`/`committed_facts` to replace the equality-scan dedup if it becomes hot; (b) consider a faster manual trigger (the DreamWorker is `daily@03:00`; `/memory` or a `run_now` on the dream job can force a pass) for users who want long-term facts sooner.

- [ ] **Step 5: Commit**
```bash
git add tests/memory/test_plan_b_conversation_miner.py src/stackowl/config/settings.py
git commit -m "test(v2): e2e mine->promote->recall; document reinforcement_required tunable (RC-A)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage:** Gap 1 (wrong source) → miner reads `staged_facts` (Task 1). Gap 2 (never run) → DreamWorker invokes miner each pass (Tasks 2-3). Gap 3 (committed empty) → Task 4 proves the chain fills. Gap 4 (dup staging) → content dedup (Task 1). ✓
- **B9:** mining runs only inside the registered `dream_worker` handler, never direct dispatch. ✓
- **Type consistency:** `Message(role, content)` matches Plan A + providers.base. `ConversationMiner(db, extractor, bridge, message_limit)` consistent across Tasks 1/3. Miner injected as optional `miner=None` everywhere (DreamWorker + register). ✓
- **Cadence caveat (documented):** long-term facts appear after the nightly dream pass(es), not instantly — acceptable for long-term memory; manual-trigger follow-up tracked. ✓
- **Placeholders:** Implementer "confirm column/signature" notes name exact expected shapes — interface confirmations, not deferred work. ✓

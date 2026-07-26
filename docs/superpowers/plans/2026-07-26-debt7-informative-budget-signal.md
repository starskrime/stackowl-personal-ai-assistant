# DEBT-7 — Informative Budget Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make StackOwl tell Bakir what his conversations cost, without any budget signal ever blocking, gating, throttling or aborting a turn.

**Architecture:** Three independent changes. (1) Strip the hard `ProviderError` block out of `CostTracker.record()` so the daily threshold is purely informative. (2) Add a session-scoped cost aggregate and a second consumer on D01.7's existing `session.rollover` seam that reports what the ending conversation cost. (3) Populate the wiring audit's `declared_publishers`, which is a hardcoded empty frozenset today and so can only ever answer "dangling".

**Tech Stack:** Python 3.13, pytest + pytest-asyncio, SQLite via `DbPool`, in-process `EventBus`.

## Global Constraints

- The budget signal is **INFORMATIVE ONLY**. It must never block, gate, throttle or abort a turn. (Bakir, 2026-07-26)
- No vendor-specific logic. Dispatch on shape and capability, never a provider name.
- Every `except` logs. No silent catches.
- 4-point logging (entry / decision / step / exit) on every new `execute()`-shaped method.
- Minimal diffs — change only the lines needed.
- All state under `~/.stackowl/` via `StackowlHome`.
- Targeted test paths with timeouts. Never a full `pytest` run — it hangs on this box.
- Schema changes are idempotent migrations only. **This plan adds no schema** — `cost_records.session_key` and `.session_id` already exist.
- Commit at sub-story granularity when green.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/stackowl/providers/cost_tracker.py` | Cost recording, daily aggregate, budget events | Modify: remove the block; add `session_total()` |
| `src/stackowl/memory/rollover_summary_handler.py` | Existing `session.rollover` consumer | Untouched — reference pattern only |
| `src/stackowl/providers/conversation_cost_report.py` | **New.** Second `session.rollover` consumer: reports the ending incarnation's cost | Create |
| `src/stackowl/notifications/event_bridge.py` | Delivery allowlist | Modify: allow the new event |
| `src/stackowl/startup/orchestrator.py` | Wiring | Modify: register the consumer; populate `declared_publishers` |
| `tests/providers/test_cost_tracker_no_hard_block.py` | **New.** Pins informative-only | Create |
| `tests/providers/test_conversation_cost_report.py` | **New.** Session aggregate + rollover consumer | Create |
| `tests/startup/test_wiring_audit_declared_publishers.py` | **New.** The audit can actually pass | Create |

---

### Task 1: The daily threshold stops blocking turns

`CostTracker.record()` currently raises `ProviderError` on every call after
`budget_exceeded` fires for the day, logging *"budget already exceeded —
blocking call"*. That is the exact behaviour Bakir's constraint forbids. It is
dormant only because no `budget.daily_limit_usd` is configured — enabling the
alert without this change would ship the failure mode he ruled out.

The threshold crossing keeps emitting its events and keeps logging. Only the
raise goes. `per_turn_pause_usd` (a soft pause that ASKS) is untouched and
remains the one mechanism permitted to interrupt.

**Files:**
- Modify: `src/stackowl/providers/cost_tracker.py:182-193` (remove the block), `:76` (docstring)
- Test: `tests/providers/test_cost_tracker_no_hard_block.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CostTracker.record(...)` never raises `ProviderError` for budget reasons. Later tasks rely on `record()` completing regardless of spend.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_record_never_raises_once_the_daily_budget_is_exceeded(db_pool):
    """Bakir, 2026-07-26: the budget signal is INFORMATIVE ONLY — it must never
    block, gate, throttle or abort a turn. The subscriber is named 'exceeded',
    but that is a notification, not a limit."""
    bus = EventBus()
    tracker = CostTracker(db=db_pool, event_bus=bus, daily_limit_usd=0.01)

    # Spend past the cap, which arms the exceeded state for today.
    await tracker.record(provider_name="p", model="m", input_tokens=10_000_000,
                         output_tokens=0, trace_id="t1")
    # The NEXT call is the one that used to raise.
    await tracker.record(provider_name="p", model="m", input_tokens=1,
                         output_tokens=1, trace_id="t2")

    summary = await tracker.daily_total()
    assert summary.call_count == 2  # both calls recorded, neither refused
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 300 uv run pytest tests/providers/test_cost_tracker_no_hard_block.py -q -p no:randomly`
Expected: FAIL with `ProviderError: budget ... Budget cap reached`

- [ ] **Step 3: Remove the block**

Delete lines 182-193 of `src/stackowl/providers/cost_tracker.py` entirely (the
`if self._daily_limit_usd is not None and today in self._exceeded_dates:` guard,
its `log.engine.error` and its `raise ProviderError`).

Replace the docstring line 76:

```python
    Emits `budget_80pct_alert` and `budget_exceeded` events on the EventBus
    when thresholds are crossed. Both are INFORMATIVE ONLY (Bakir,
    2026-07-26): recording NEVER refuses a call, however far over the
    threshold the day has run. The one mechanism permitted to interrupt is
    the SOFT per-turn pause (`per_turn_pause_usd`), which asks the user and
    never raises.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 300 uv run pytest tests/providers/test_cost_tracker_no_hard_block.py tests/providers/test_cost_tracker_turn_totals.py -q -p no:randomly`
Expected: PASS, and the pre-existing turn-totals suite still green.

- [ ] **Step 5: Check for callers that expected the raise**

Run: `grep -rn "Budget cap reached\|budget already exceeded" src/ tests/`
Expected: no hits outside the deleted lines. If a test asserted the raise, STOP
and report it to Bakir rather than rewriting it — standing rule.

- [ ] **Step 6: Commit**

```bash
uv run ruff check src/ && git add -A && git commit -m "fix(cost): DEBT-7 part 1 — the budget signal no longer blocks a turn"
```

---

### Task 2: Report what each conversation cost

Bakir's decision (2026-07-26): report **per-conversation cost** rather than
gate on a daily threshold. D01.7 built exactly the seam for this —
`session.rollover` fires once per boundary and already carries the lane, the
ended incarnation, the owl and the channel. This is dedup target X3's principle
applied: one boundary, many consumers, no second scheduler.

`cost_records` already carries `session_key` and `session_id` (added by D01.6 /
D01.7), so the aggregate is a query, not a migration.

**Files:**
- Modify: `src/stackowl/providers/cost_tracker.py` (add `session_total`)
- Create: `src/stackowl/providers/conversation_cost_report.py`
- Modify: `src/stackowl/notifications/event_bridge.py:48` (allowlist)
- Modify: `src/stackowl/startup/orchestrator.py:971` area (register)
- Test: `tests/providers/test_conversation_cost_report.py`

**Interfaces:**
- Consumes: Task 1's non-raising `record()`.
- Produces:
  - `CostTracker.session_total(session_key: str, session_id: str) -> DailySummary` — `DailySummary.date` carries the `session_id` for this call, since the aggregate is scoped to an incarnation rather than a day.
  - `register_conversation_cost_consumer(event_bus, tracker) -> None`
  - Event `"conversation_cost_report"` with payload keys `session_key`, `session_id`, `total_usd`, `call_count`, `owl_name`, `channel`, `message`, and `target` when a recipient is resolved.

- [ ] **Step 1: Write the failing test for the aggregate**

```python
@pytest.mark.asyncio
async def test_session_total_sums_only_that_incarnation(db_pool):
    """A lane outlives its incarnations, so cost must be scoped to BOTH —
    session_key alone would bill a new conversation for the old one's spend."""
    tracker = CostTracker(db=db_pool, event_bus=EventBus(), daily_limit_usd=None)
    await tracker.record(provider_name="p", model="m", input_tokens=1000,
                         output_tokens=100, trace_id="t1",
                         session_key="owl:sec:tg:dm:1", session_id="INC_A")
    await tracker.record(provider_name="p", model="m", input_tokens=2000,
                         output_tokens=200, trace_id="t2",
                         session_key="owl:sec:tg:dm:1", session_id="INC_B")

    summary = await tracker.session_total("owl:sec:tg:dm:1", "INC_A")

    assert summary.call_count == 1
    assert summary.total_usd > 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 300 uv run pytest tests/providers/test_conversation_cost_report.py -q -p no:randomly`
Expected: FAIL with `AttributeError: 'CostTracker' object has no attribute 'session_total'`

- [ ] **Step 3: Implement the aggregate**

Add to `src/stackowl/providers/cost_tracker.py`, immediately after `daily_total`:

```python
    async def session_total(self, session_key: str, session_id: str) -> DailySummary:
        """Aggregate cost_records for ONE incarnation of one lane.

        Scoped to both identifiers on purpose: a lane outlives its
        incarnations, so ``session_key`` alone would bill a fresh conversation
        for its predecessor's spend. ``DailySummary.date`` carries the
        ``session_id`` here — the shape is an aggregate, not a calendar day.
        """
        log.engine.debug(
            "[cost_tracker] session_total: entry",
            extra={"_fields": {"session_key": session_key, "session_id": session_id}},
        )
        rows = await self._db.fetch_all(
            """
            SELECT provider_name, model, cost_usd
            FROM cost_records
            WHERE owner_id = ? AND session_key = ? AND session_id = ?
            """,
            (self._owner_id, session_key, session_id),
        )
        total = 0.0
        by_provider: dict[str, float] = {}
        by_model: dict[str, float] = {}
        for row in rows:
            cost = float(row["cost_usd"])
            total += cost
            by_provider[row["provider_name"]] = by_provider.get(row["provider_name"], 0.0) + cost
            by_model[row["model"]] = by_model.get(row["model"], 0.0) + cost
        log.engine.debug(
            "[cost_tracker] session_total: exit",
            extra={"_fields": {"session_key": session_key, "session_id": session_id,
                               "total_usd": total, "call_count": len(rows)}},
        )
        return DailySummary(
            date=session_id,
            total_usd=total,
            by_provider=by_provider,
            by_model=by_model,
            call_count=len(rows),
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 300 uv run pytest tests/providers/test_conversation_cost_report.py -q -p no:randomly`
Expected: PASS

- [ ] **Step 5: Write the failing test for the consumer**

```python
@pytest.mark.asyncio
async def test_rollover_publishes_what_the_conversation_cost(db_pool):
    bus = EventBus()
    tracker = CostTracker(db=db_pool, event_bus=bus, daily_limit_usd=None)
    await tracker.record(provider_name="p", model="m", input_tokens=1000,
                         output_tokens=100, trace_id="t1",
                         session_key="owl:sec:tg:dm:1", session_id="INC_A")
    seen: list[dict] = []
    bus.subscribe("conversation_cost_report", lambda p: seen.append(p))
    register_conversation_cost_consumer(bus, tracker)

    bus.emit(SessionStore.ROLLOVER_EVENT, {
        "session_key": "owl:sec:tg:dm:1", "old_session_id": "INC_A",
        "owl_name": "secretary", "channel": "telegram",
    })
    await asyncio.sleep(0)  # let the async subscriber run

    assert len(seen) == 1
    assert seen[0]["session_id"] == "INC_A"
    assert seen[0]["total_usd"] > 0


@pytest.mark.asyncio
async def test_a_free_conversation_reports_nothing(db_pool):
    """A boundary with no spend is noise, not news — no event, no ping."""
    bus = EventBus()
    tracker = CostTracker(db=db_pool, event_bus=bus, daily_limit_usd=None)
    seen: list[dict] = []
    bus.subscribe("conversation_cost_report", lambda p: seen.append(p))
    register_conversation_cost_consumer(bus, tracker)

    bus.emit(SessionStore.ROLLOVER_EVENT, {
        "session_key": "owl:sec:tg:dm:1", "old_session_id": "INC_EMPTY",
    })
    await asyncio.sleep(0)

    assert seen == []


@pytest.mark.asyncio
async def test_a_boundary_that_ended_nothing_reports_nothing(db_pool):
    """The sweeper legitimately publishes new_session_id=None; a missing OLD id
    means nothing finished, so there is nothing to price."""
    bus = EventBus()
    tracker = CostTracker(db=db_pool, event_bus=bus, daily_limit_usd=None)
    seen: list[dict] = []
    bus.subscribe("conversation_cost_report", lambda p: seen.append(p))
    register_conversation_cost_consumer(bus, tracker)

    bus.emit(SessionStore.ROLLOVER_EVENT, {"session_key": "lane", "old_session_id": ""})
    await asyncio.sleep(0)

    assert seen == []


@pytest.mark.asyncio
async def test_a_failing_aggregate_never_breaks_the_boundary(db_pool):
    """The conversation starting matters more than its receipt — same rule the
    rollover summary consumer follows."""
    bus = EventBus()

    class _Boom:
        async def session_total(self, *a, **k):
            raise RuntimeError("db gone")

    register_conversation_cost_consumer(bus, _Boom())
    bus.emit(SessionStore.ROLLOVER_EVENT,
             {"session_key": "lane", "old_session_id": "INC_A"})
    await asyncio.sleep(0)  # must not raise
```

- [ ] **Step 6: Run to verify they fail**

Run: `timeout 300 uv run pytest tests/providers/test_conversation_cost_report.py -q -p no:randomly`
Expected: FAIL with `NameError`/`ImportError` for `register_conversation_cost_consumer`

- [ ] **Step 7: Implement the consumer**

Create `src/stackowl/providers/conversation_cost_report.py`:

```python
"""Report what a conversation cost, at the moment it ends.

DEBT-7 (Bakir, 2026-07-26): the budget signal is INFORMATIVE ONLY — it tells
him what a conversation is costing and never blocks, gates, throttles or
aborts a turn. He chose per-conversation reporting over a daily threshold,
because "what did that conversation cost" is the question he actually has.

This rides D01.7's ``session.rollover`` seam rather than adding a scheduler:
one boundary, many consumers (dedup target X3). The rollover summary consumer
in memory/rollover_summary_handler.py is the sibling that established the
pattern, including its rule that a consumer must never break the boundary.
"""

from __future__ import annotations

from typing import Any

from stackowl.infra.observability import log

COST_REPORT_EVENT = "conversation_cost_report"


def register_conversation_cost_consumer(event_bus: object, tracker: object) -> None:
    """Subscribe a cost report to the session-rollover boundary."""
    from stackowl.sessions.store import SessionStore

    async def _on_rollover(payload: dict[str, Any] | None) -> None:
        data = payload or {}
        lane = str(data.get("session_key") or "")
        ended = str(data.get("old_session_id") or "")
        if not lane or not ended:
            log.engine.debug(
                "[cost] conversation_cost: nothing ended — not reporting",
                extra={"_fields": {"session_key": lane, "old_session_id": ended}},
            )
            return
        try:
            summary = await tracker.session_total(lane, ended)  # type: ignore[attr-defined]
        except Exception as exc:
            # The conversation starting matters more than its receipt.
            log.engine.warning(
                "[cost] conversation_cost: could not price the boundary — skipped",
                exc_info=exc,
                extra={"_fields": {"session_key": lane, "ended_session_id": ended}},
            )
            return
        if summary.call_count == 0 or summary.total_usd <= 0:
            log.engine.debug(
                "[cost] conversation_cost: nothing spent — not reporting",
                extra={"_fields": {"session_key": lane, "ended_session_id": ended}},
            )
            return
        message = (
            f"That conversation cost ${summary.total_usd:.4f} "
            f"over {summary.call_count} model call(s)."
        )
        log.engine.info(
            "[cost] conversation_cost: exit — reporting a finished conversation",
            extra={"_fields": {
                "session_key": lane, "session_id": ended,
                "total_usd": summary.total_usd, "call_count": summary.call_count,
                "owl_name": data.get("owl_name"), "channel": data.get("channel"),
            }},
        )
        event_bus.emit(COST_REPORT_EVENT, {  # type: ignore[attr-defined]
            "session_key": lane,
            "session_id": ended,
            "total_usd": summary.total_usd,
            "call_count": summary.call_count,
            "owl_name": data.get("owl_name"),
            "channel": data.get("channel"),
            "message": message,
        })

    event_bus.subscribe(SessionStore.ROLLOVER_EVENT, _on_rollover)  # type: ignore[attr-defined]
    log.engine.info(
        "[cost] conversation_cost: subscribed",
        extra={"_fields": {"event": SessionStore.ROLLOVER_EVENT, "emits": COST_REPORT_EVENT}},
    )
```

- [ ] **Step 8: Run to verify they pass**

Run: `timeout 300 uv run pytest tests/providers/test_conversation_cost_report.py -q -p no:randomly`
Expected: PASS (5 tests)

- [ ] **Step 9: Allow the event through the delivery bridge**

In `src/stackowl/notifications/event_bridge.py`, change line 48 to:

```python
_ALLOWED_EVENTS: frozenset[str] = frozenset(
    {"budget_exceeded", "budget_80pct_alert", "conversation_cost_report"}
)
```

Note: the bridge's honest-recipient rail drops any event carrying no
`channel`/`target`, so this makes delivery POSSIBLE without making it
unconditional — a report with no resolved recipient stays log-only.

- [ ] **Step 10: Register the consumer at startup**

In `src/stackowl/startup/orchestrator.py`, immediately after the existing
`register_rollover_consumer(event_bus, db_pool, session_store)` call (line ~971):

```python
        from stackowl.providers.conversation_cost_report import (
            register_conversation_cost_consumer,
        )

        # DEBT-7 — the SECOND consumer on the same boundary (X3: one rollover,
        # many consumers). Registered after the tracker exists.
        if cost_tracker is not None:
            register_conversation_cost_consumer(event_bus, cost_tracker)
```

If `cost_tracker` is not in scope at that point, move this registration to
immediately after `provider_registry.set_cost_tracker(cost_tracker)` (line ~1295)
instead, and say so in the commit — the ordering constraint is only that both
the bus and the tracker exist.

- [ ] **Step 11: Commit**

```bash
uv run ruff check src/ && timeout 300 uv run pytest tests/providers/ -q -p no:randomly && git add -A && git commit -m "feat(cost): DEBT-7 part 2 — report what each conversation cost, on the rollover seam"
```

---

### Task 3: Make the wiring audit able to pass

`declared_event_publishers` in `orchestrator.py:3656` is
`frozenset()` — a hardcoded empty set, never assigned anything else in the
tree. The dangling-event check therefore compares subscribers against nothing
and can only ever answer "dangling". It was right about the budget events by
accident; it would say the same about a perfectly-wired event. A permanently-red
signal is one everybody learns to ignore.

**Files:**
- Modify: `src/stackowl/startup/orchestrator.py:3656`
- Test: `tests/startup/test_wiring_audit_declared_publishers.py`

**Interfaces:**
- Consumes: Task 2's `COST_REPORT_EVENT`.
- Produces: nothing downstream.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_a_declared_publisher_is_not_reported_dangling():
    """The audit must be able to answer 'wired'. With an empty declared set it
    can only ever answer 'dangling', which makes the check meaningless."""
    report = await audit_scheduler_wiring(
        db=_FakeDb(), registry=_EmptyRegistry(),
        allowed_events=frozenset({"budget_exceeded"}),
        declared_publishers=frozenset({"budget_exceeded"}),
    )
    assert report.dangling_events == 0


def test_orchestrator_declares_the_events_it_actually_publishes():
    """CostTracker emits both budget events and the conversation cost report;
    all three must be declared, or boot warns about working wiring forever."""
    src = Path("src/stackowl/startup/orchestrator.py").read_text()
    assert "declared_event_publishers: frozenset[str] = frozenset()" not in src
    for event in ("budget_exceeded", "budget_80pct_alert", "conversation_cost_report"):
        assert event in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `timeout 300 uv run pytest tests/startup/test_wiring_audit_declared_publishers.py -q -p no:randomly`
Expected: FAIL on the second test — the empty-frozenset line is present.

- [ ] **Step 3: Populate the declared set**

Replace `orchestrator.py:3656`:

```python
            # DEBT-7 — this was `frozenset()`, so the dangling-event check
            # compared subscribers against NOTHING and could only ever answer
            # "dangling". It reported the budget events correctly by accident
            # and would have said the same about perfectly-wired ones. These
            # three are emitted by providers/cost_tracker.py (both budget
            # thresholds) and providers/conversation_cost_report.py.
            declared_event_publishers: frozenset[str] = frozenset({
                "budget_exceeded",
                "budget_80pct_alert",
                COST_REPORT_EVENT,
            })
```

with `from stackowl.providers.conversation_cost_report import COST_REPORT_EVENT`
added to the local import block just above.

- [ ] **Step 4: Run to verify it passes**

Run: `timeout 300 uv run pytest tests/startup/test_wiring_audit_declared_publishers.py -q -p no:randomly`
Expected: PASS

- [ ] **Step 5: Verify the gates**

Run: `uv run ruff check src/ | tail -2` — expect `Found 46 errors` (DEBT-1 baseline, unchanged).
Run: `timeout 400 uv run mypy src/ | tail -1` — expect `Found 78 errors` (DEBT-8 baseline, unchanged).

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "fix(startup): DEBT-7 part 3 — the wiring audit can now answer 'wired'"
```

---

## Validation (after all three tasks)

- [ ] Restart: `./start.sh`
- [ ] Confirm zero dangling events:
```bash
tail -n +<mark> ~/.stackowl/logs/stackowl.jsonl | grep -c "DANGLING event subscription"
```
Expected: `0` (was 2 per process).
- [ ] Confirm the consumer subscribed:
```bash
tail -n +<mark> ~/.stackowl/logs/stackowl.jsonl | grep "conversation_cost: subscribed"
```
- [ ] Confirm 0 ERROR/CRITICAL on boot.
- [ ] **Needs Bakir:** one `/new` on Telegram ends an incarnation that has spend,
      which should produce `[cost] conversation_cost: exit — reporting a finished
      conversation` with a non-zero `total_usd`.

## Open assumption to confirm with Bakir

Task 2 makes the report **deliverable** (Step 9) rather than log-only, on the
reading of "the publisher tells Bakir what a conversation is costing". Delivery
still requires a resolved recipient, and a zero-cost boundary reports nothing —
but a busy day with several `/new`s would produce several messages. If that is
too chatty, the one-line revert is to drop `conversation_cost_report` from
`_ALLOWED_EVENTS`, leaving it visible in logs and to the TUI without pinging.

## Known debt this plan does NOT fix

- **DEBT-15** — every dollar figure this reports is fallback-derived, because
  `neraai-v1-raw` is absent from `pricing.yaml`. The report is honest about
  *which* calls and *how many*; its dollars inherit that caveat until DEBT-15
  lands. Worth doing next, since this feature's whole output is dollars.

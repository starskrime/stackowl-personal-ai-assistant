# Concurrent / Steerable Message Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop serializing user messages behind the in-flight turn — non-blocking within a chat, truly parallel across chats, with live-steer of the running turn.
**Architecture:** Per chat: one running turn + a FIFO intake queue (non-blocking accept). Cross-session: parallel via request-id-keyed response streams (deletes serialize_prior). Live-steer folds a [steering] message into the running ReAct loop between iterations; hybrid relatedness routes STEER/STOP/QUEUE.
**Tech Stack:** Python 3.11+, asyncio, Pydantic v2, pytest

---

## Recon notes (verified — DO NOT re-guess)

All file:line below are confirmed against the live tree on `feat/agentic-os-stage1`.

- **trace_id ALREADY exists end-to-end.** `gateway/scanner.py:38-46`: `@dataclass(frozen=True) class IngressMessage: text:str; session_id:str; channel:str; trace_id:str`. Threaded into `PipelineState.trace_id` (`pipeline/state.py:30`, field 1) by the orchestrator at construction. Re-keying streams by `trace_id` is **NOT DTO surgery** — the field is already plumbed; this is a registry/deliver/orchestrator re-key + a mint-site guard.
- **Mint sites.** `channels/cli_adapter.py:140-153`: `trace_id = f"cli-{self._session_id[:8]}-{self._trace_counter}"` (counter-based). `channels/telegram/adapter.py:529-539`: `IngressMessage(... trace_id=uuid4().hex)`; `chat_id = int(chat.id)` is captured here but stored on the shared `self._last_chat_id` (line 539), **NOT** on the IngressMessage.
- **Streaming.** `pipeline/streaming.py:61-84`: `StreamRegistry._writers: dict[str, StreamWriter]` keyed by `session_id`; `create(session_id) -> (StreamWriter, StreamReader)` (67-74), `get_writer(session_id) -> StreamWriter | None` (76-77), `remove(session_id)` (79-84). `ResponseChunk(BaseModel, frozen=True)` (13-21): `content, is_final, chunk_index, trace_id, owl_name, duration_ms` — **NO `target` field**. `StreamWriter.close()` (33-41) writes a sentinel with `trace_id=""`.
- **Deliver.** `pipeline/steps/deliver.py:42-52`: `writer = registry.get_writer(state.session_id)`; `if writer is None: return state`; `for chunk in state.responses: await writer.write(chunk)`; `await writer.close()`. Lines 21-27 skip delivery when `state.delegation_depth > 0`.
- **Backend.** `pipeline/backends/asyncio_backend.py:29-53`: reads `state.trace_id`, passes to `TraceContext.start(state.session_id, trace_id=state.trace_id, ...)`. Does NOT mutate `state.trace_id` (it flows from PipelineState construction in the orchestrator).
- **Orchestrator loops.** `startup/orchestrator.py`: CLI loop 740-832, Telegram loop 903-992. Per-message body: `decision = scanner.scan(msg); ...; consumed, input_text = await cli_pump.resolve_or_rewrite(...); if consumed: continue; await cli_pump.serialize_prior(msg.session_id); writer, reader = stream_registry.create(msg.session_id); state = PipelineState(trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text, channel=msg.channel, owl_name=decision.target, pipeline_step="start", interactive=True); producer = asyncio.create_task(backend.run(state)); producer.add_done_callback(_log_pipeline_crash); cli_pump.spawn_send(channel_adapter=adapter, reader=reader, session_id=msg.session_id, producer=producer, writer=writer)`. Telegram loop is identical with `tg_pump`/`telegram_adapter`.
- **Clarify pump.** `gateway/clarify_pump.py`: `_inflight: dict[str, asyncio.Task[None]]` keyed by `session_id` (line 81). `serialize_prior(session_id)` (163-168) awaits `_inflight.get(session_id)`. `spawn_send(...)` (172-220) sets `_inflight[session_id] = send_task` (192); `_cleanup` callback pops it (194-197). `drain()` (235-240). `resolve_or_rewrite(...)` (85-159) does NOT touch `_inflight`. Class docstring flags "re-keying streams per trace_id would remove serialize_prior (FF-E5-B2)".
- **Clarify gateway.** `interaction/clarify_gateway.py:126`: pending clarify state `_pending: dict[str, PendingClarify]` keyed by `clarify_id`; pump reads via `peek_for_session(session_id, channel)` (115), `try_resolve` (140), `cancel_pending` (132), `clear_session` (111).
- **Intent classifier.** `interaction/intent_classifier.py:77`: `async def is_answer(self, *, question: str, choices: tuple[str, ...], message: str) -> bool` — fast-tier provider, `max_tokens=4`; `_parse_verdict` (205-252): "answer"&!"new"→True; "new"&!"answer"→False; both/neither→fail-safe True. Fail-safe True on error.
- **Callback carrier.** `providers/react_callback.py:16-45`: `IterationCallback = Callable[[ReActIterationState], Awaitable[None]]`; `ReActIterationState(frozen)`: `iteration:int, messages:list[dict], tool_call_records:list[dict]`. Callback receives `messages=list(messages)` (a COPY); **return value currently discarded.**
- **Execute step.** `pipeline/steps/execute.py`: `_call_default` (432-445) sets `on_iteration_complete=_budget_cb` only when budget caps apply (from `make_budget_callback` 409-419); `_call_durable` (447-519) builds `cb = make_checkpoint_callback(ctx, session.store)` (488), composed `_cb_with_budget` (492-496), passed as `on_iteration_complete=_iter_cb` (515).
- **Providers.** `providers/openai_provider.py`: `messages = list(resume_messages)` (173) or `[]` (176); callback call sites at no-tool terminal (330-337) and after-tool (388-395), each `await on_iteration_complete(ReActIterationState(iteration=_iter_idx, messages=list(messages), tool_call_records=list(all_calls)))`, return discarded. `providers/anthropic_provider.py`: `messages = list(resume_messages)` (160) or local (163); callbacks 251-252, 294-295 (same shape). **Seven call sites total** across the two providers.
- **Consolidate.** `pipeline/steps/consolidate.py:11-37`: `_persist_turn` → `content = f"User: {state.input_text}\n\nAssistant: {assistant_text}"`; `await bridge.store(content, state.session_id, trust=trust_override)`; try/except, never raises. NO inline embed. `SqliteMemoryBridge.store` (138-164) → `stage(fact)` single INSERT, no lock.
- **Evolution / promotion are OFF the turn path.** Evolution = `owls/evolution.py:119` `EvolutionCoordinator(JobHandler, handler_name="evolution_batch")`, scheduler-driven, per-owl batch. Promotion = `FactPromoter.promote_eligible` invoked only from `memory/dream_worker.py:273`. `consolidate` triggers neither. (§4.6 = guard test only, no new serialization.)
- **D1 ledger DORMANT for interactive turns.** `pipeline/durable/ledger_guard.py:91-99,120-147`: guard is `get_active() is None → passthrough`; durable ctx only active when `state.task_id` set. Interactive turns have no `task_id`. (§5.3 ledger shield = durable-only → backlog.)
- **Proactive deliverer.** `notifications/deliverer.py:132-170`: `ProactiveDeliverer._transport` → `adapter = self._registry.get(channel); await adapter.send_text(message)` — DIRECT, not stream path.
- **Telegram adapter send.** `channels/telegram/adapter.py:125-153`: `async def send_text(self, text: str) -> None` — NO chat_id param, targets `self._last_chat_id` (132 guard, 149). `send` (112-123) buffers chunks → `self.send_text(...)`. `send_inline_keyboard`/`send_clarify` DO accept `chat_id: int | None = None` (159, fallback 185). `_last_chat_id` is set on every `_handle_update` (539).
- **Test scaffold to mirror:** `tests/pipeline/test_plan_a_gateway_integration.py` (drives `GatewayScanner.scan(IngressMessage(...))`, builds PipelineState as the orchestrator does, real `ToolRegistry.with_defaults()`, `_RecordingProvider` via real `ProviderRegistry`, real `AsyncioBackend`). Session-keyed tests needing DELIBERATE updates: `tests/gateway/test_clarify_pump.py` (`test_serialize_prior_awaits_unfinished_same_session` 175-185; `test_spawn_send_drains_and_reaps_on_normal_close` 191-212 asserts `reg.get_writer("s1") is None`), `tests/pipeline/test_plan_a_*`.

### House rules (apply to EVERY task)
- Run tests from `v2/`, **targeted paths only** (the full suite hangs on this box). **NEVER pass `--timeout`.**
- Commits stage `v2/` only, from the repo root.
- Every `except` logs (`log.<ns>.error("op failed", ...)`); never a silent/empty catch.
- A pre-existing test that this feature changes is a **DELIBERATE update**: assert the NEW behavior, FLAG it in the commit body, never weaken/skip/silent-fix.
- Real code at every step — no placeholders, no "see Task N" cross-references inside a step.
- P2/P3 live paths (steer dispatch, stop, router) gate behind the §9 invariant tests (Tasks 11, 8, 15).

---

## Task 1 — request_id uniqueness + non-empty guard at the mint sites

**Files:**
- Modify: `v2/src/stackowl/channels/cli_adapter.py`
- Modify: `v2/src/stackowl/channels/telegram/adapter.py`
- Create: `v2/tests/channels/test_request_id_mint.py`

- [ ] **Failing test** — `v2/tests/channels/test_request_id_mint.py`:
  ```python
  from __future__ import annotations

  import pytest

  from stackowl.channels.cli_adapter import CliAdapter
  from stackowl.channels.telegram.adapter import _mint_request_id  # extracted helper


  def test_cli_request_ids_are_unique_and_non_empty() -> None:
      adapter = CliAdapter(session_id="abc12345")
      ids = {adapter._next_request_id() for _ in range(1000)}
      assert len(ids) == 1000, "CLI request ids must be unique within a session"
      assert all(rid for rid in ids), "CLI request id must be non-empty"


  def test_telegram_request_ids_unique_non_empty() -> None:
      ids = {_mint_request_id() for _ in range(1000)}
      assert len(ids) == 1000
      assert all(rid for rid in ids)
      assert "" not in ids
  ```
  (Adjust `CliAdapter(...)` construction to the real ctor; the point is to exercise the actual mint path. If the CLI counter is reachable only inside the run loop, extract a private `_next_request_id()` helper that the loop calls, and test that.)
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/channels/test_request_id_mint.py -v`
- [ ] **Minimal impl** — in `cli_adapter.py`, extract the counter into a helper and assert:
  ```python
  def _next_request_id(self) -> str:
      self._trace_counter += 1
      rid = f"cli-{self._session_id[:8]}-{self._trace_counter}"
      if not rid or self._trace_counter < 1:
          log.gateway.error("[mint] cli request_id invalid", extra={"_fields": {"rid": rid}})
          raise ValueError("empty/invalid request_id")
      return rid
  ```
  Replace the inline `f"cli-..."` at line 140-153 with a call to `_next_request_id()`.
  In `telegram/adapter.py`, extract a module-level `_mint_request_id()`:
  ```python
  def _mint_request_id() -> str:
      rid = uuid4().hex
      if not rid:
          log.gateway.error("[mint] telegram request_id empty")
          raise ValueError("empty request_id")
      return rid
  ```
  Replace `trace_id=uuid4().hex` (line 535) with `trace_id=_mint_request_id()`.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/channels/test_request_id_mint.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/channels/ tests/channels/ && uv run mypy src/stackowl/channels/`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/channels/cli_adapter.py v2/src/stackowl/channels/telegram/adapter.py v2/tests/channels/test_request_id_mint.py && git commit -m "feat(v2): guard request_id unique+non-empty at mint sites (concurrent-msg §4.1)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 2 — Re-key StreamRegistry session_id→request_id + ResponseChunk.target + stream-miss hard-drop + deliver

**Files:**
- Modify: `v2/src/stackowl/pipeline/streaming.py`
- Modify: `v2/src/stackowl/pipeline/steps/deliver.py`
- Modify: `v2/tests/gateway/test_clarify_pump.py` (DELIBERATE — session→request_id re-key)
- Modify: `v2/tests/pipeline/test_plan_a_gateway_integration.py` (DELIBERATE — re-key)
- Create: `v2/tests/pipeline/test_stream_rekey.py`

- [ ] **Failing test** — `v2/tests/pipeline/test_stream_rekey.py`:
  ```python
  from __future__ import annotations

  import pytest

  from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry


  @pytest.mark.asyncio
  async def test_registry_is_keyed_by_request_id_not_session() -> None:
      reg = StreamRegistry()
      w1, r1 = reg.create("req-1")
      w2, r2 = reg.create("req-2")
      assert reg.get_writer("req-1") is w1
      assert reg.get_writer("req-2") is w2
      assert reg.get_writer("session-x") is None  # session_id no longer a key
      reg.remove("req-1")
      assert reg.get_writer("req-1") is None
      assert reg.get_writer("req-2") is w2


  def test_response_chunk_has_optional_target() -> None:
      chunk = ResponseChunk(
          content="hi", is_final=False, chunk_index=0,
          trace_id="req-1", owl_name="owl",
      )
      assert chunk.target is None
      tagged = chunk.model_copy(update={"target": 555})
      assert tagged.target == 555
  ```
  Also add to `test_clarify_pump.py` a NEW assertion that the registry slot is created/removed under `request_id` (the existing `test_spawn_send_drains_and_reaps_on_normal_close` asserts `reg.get_writer("s1") is None` — update it to use the request_id the test mints, FLAG it).
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/test_stream_rekey.py -v`
- [ ] **Minimal impl** — `streaming.py`:
  - Add `target: int | None = None` to `ResponseChunk` (after `duration_ms`).
  - Rename the `StreamRegistry` param `session_id` → `request_id` in `create`/`get_writer`/`remove` and the docstring/log fields. Body unchanged otherwise.
  - `deliver.py:42`: `writer = registry.get_writer(state.trace_id)`. Add, right after, a stream-miss hard-drop guard for any chunk whose `trace_id` mismatches (defensive — the response-side mirror of no-hidden-errors):
    ```python
    writer = registry.get_writer(state.trace_id)
    if writer is None:
        log.gateway.warning(
            "[deliver] stream-miss: no writer for request_id; dropping turn output",
            extra={"_fields": {"request_id": state.trace_id, "session_id": state.session_id}},
        )
        return state
    for chunk in state.responses:
        if chunk.trace_id and chunk.trace_id != state.trace_id:
            log.gateway.error(
                "[deliver] chunk request_id mismatch — hard drop, never reroute",
                extra={"_fields": {"chunk_request_id": chunk.trace_id, "turn_request_id": state.trace_id}},
            )
            continue
        await writer.write(chunk)
    await writer.close()
    ```
- [ ] **DELIBERATE test updates** — change `test_clarify_pump.py` and `test_plan_a_gateway_integration.py` to key the registry by the request_id the orchestrator/test mints (not `session_id`/`"s1"`). FLAG in commit body: "session-keyed stream assertions re-keyed to request_id — this is the intended §4.1 behavior change, not a weakening." Run them to confirm they assert the NEW behavior.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/test_stream_rekey.py tests/pipeline/test_plan_a_gateway_integration.py tests/gateway/test_clarify_pump.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/pipeline/ && uv run mypy src/stackowl/pipeline/streaming.py src/stackowl/pipeline/steps/deliver.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/streaming.py v2/src/stackowl/pipeline/steps/deliver.py v2/tests/pipeline/test_stream_rekey.py v2/tests/pipeline/test_plan_a_gateway_integration.py v2/tests/gateway/test_clarify_pump.py && git commit -m "feat(v2): re-key response streams by request_id + ResponseChunk.target + stream-miss hard-drop (concurrent-msg §4.1/§4.5)" -m "DELIBERATE: session-keyed stream tests re-keyed to request_id (intended behavior change)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 3 — Create `gateway/turn_registry.py` (Turn + CAS status + per-session running/queue + sweeper)

**Files:**
- Create: `v2/src/stackowl/gateway/turn_registry.py`
- Create: `v2/tests/gateway/test_turn_registry.py`

- [ ] **Failing test** — `v2/tests/gateway/test_turn_registry.py`:
  ```python
  from __future__ import annotations

  import asyncio

  import pytest

  from stackowl.gateway.turn_registry import Turn, TurnRegistry, TurnStatus


  @pytest.mark.asyncio
  async def test_status_cas_is_one_way() -> None:
      reg = TurnRegistry()
      task = asyncio.create_task(asyncio.sleep(0))
      turn = await reg.register("req-1", session_id="s1", task=task, target=None, original_input="hi")
      assert turn.status is TurnStatus.RUNNING
      assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.FINALIZING) is True
      # backward / skip transitions rejected
      assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.DONE) is False
      assert await reg.cas_status("req-1", TurnStatus.FINALIZING, TurnStatus.DONE) is True
      await task

  @pytest.mark.asyncio
  async def test_one_running_per_session_plus_fifo_queue() -> None:
      reg = TurnRegistry()
      t = asyncio.create_task(asyncio.sleep(0))
      await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
      assert reg.running("s1") is not None
      reg.enqueue("s1", original_input="b", request_id="req-2", target=None)
      reg.enqueue("s1", original_input="c", request_id="req-3", target=None)
      first = reg.pop_next("s1")
      assert first is not None and first.original_input == "b"
      assert reg.pop_next("s1").original_input == "c"  # FIFO
      assert reg.pop_next("s1") is None
      await t

  @pytest.mark.asyncio
  async def test_deregister_clears_running() -> None:
      reg = TurnRegistry()
      t = asyncio.create_task(asyncio.sleep(0))
      await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
      await reg.deregister("req-1")
      assert reg.running("s1") is None
      assert reg.get("req-1") is None
      await t

  @pytest.mark.asyncio
  async def test_sweeper_snapshots_then_acts_and_reaps_done_without_status() -> None:
      reg = TurnRegistry()
      async def quick() -> None:
          return None
      t = asyncio.create_task(quick())
      await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
      await t  # task done, status still RUNNING (lost the finally race)
      reaped = await reg.sweep(ttl_seconds=0.0)
      assert "req-1" in reaped
      assert reg.get("req-1") is None
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_turn_registry.py -v`
- [ ] **Minimal impl** — `turn_registry.py`:
  ```python
  from __future__ import annotations

  import asyncio
  import enum
  import time
  from collections import deque
  from dataclasses import dataclass, field

  from stackowl.infra.observability import log

  _MAILBOX_MAX = 8


  class TurnStatus(enum.Enum):
      RUNNING = "running"
      FINALIZING = "finalizing"
      DONE = "done"


  # legal one-way transitions
  _NEXT: dict[TurnStatus, TurnStatus] = {
      TurnStatus.RUNNING: TurnStatus.FINALIZING,
      TurnStatus.FINALIZING: TurnStatus.DONE,
  }


  @dataclass
  class PendingIntake:
      request_id: str
      original_input: str
      target: int | None


  @dataclass
  class Turn:
      turn_id: str  # == request_id
      session_id: str
      task: asyncio.Task[None] | None
      target: int | None
      original_input: str
      status: TurnStatus = TurnStatus.RUNNING
      stop_requested: bool = False
      clarify_pending: bool = False
      steering_mailbox: asyncio.Queue[str] = field(
          default_factory=lambda: asyncio.Queue(maxsize=_MAILBOX_MAX)
      )
      started_at: float = field(default_factory=time.monotonic)
      lock: asyncio.Lock = field(default_factory=asyncio.Lock)


  class TurnRegistry:
      """In-memory per-session turn tracking: one running turn + FIFO intake queue."""

      def __init__(self) -> None:
          self._turns: dict[str, Turn] = {}            # request_id -> Turn
          self._running: dict[str, str] = {}           # session_id -> request_id
          self._queues: dict[str, deque[PendingIntake]] = {}

      def get(self, request_id: str) -> Turn | None:
          return self._turns.get(request_id)

      def running(self, session_id: str) -> Turn | None:
          rid = self._running.get(session_id)
          return self._turns.get(rid) if rid else None

      async def register(self, request_id: str, *, session_id: str,
                         task: asyncio.Task[None] | None, target: int | None,
                         original_input: str) -> Turn:
          turn = Turn(turn_id=request_id, session_id=session_id, task=task,
                      target=target, original_input=original_input)
          self._turns[request_id] = turn
          self._running[session_id] = request_id
          log.gateway.debug("[turn] register", extra={"_fields": {"request_id": request_id, "session_id": session_id}})
          return turn

      async def cas_status(self, request_id: str, expect: TurnStatus, new: TurnStatus) -> bool:
          turn = self._turns.get(request_id)
          if turn is None:
              return False
          async with turn.lock:
              if turn.status is not expect or _NEXT.get(expect) is not new:
                  return False
              turn.status = new
              return True

      def enqueue(self, session_id: str, *, original_input: str, request_id: str, target: int | None) -> None:
          self._queues.setdefault(session_id, deque()).append(
              PendingIntake(request_id=request_id, original_input=original_input, target=target)
          )

      def pop_next(self, session_id: str) -> PendingIntake | None:
          q = self._queues.get(session_id)
          if not q:
              return None
          return q.popleft()

      async def deregister(self, request_id: str) -> None:
          turn = self._turns.pop(request_id, None)
          if turn is None:
              return
          if self._running.get(turn.session_id) == request_id:
              self._running.pop(turn.session_id, None)
          log.gateway.debug("[turn] deregister", extra={"_fields": {"request_id": request_id}})

      async def sweep(self, *, ttl_seconds: float) -> list[str]:
          """Backstop: reap turns whose task is done but status not terminal, or past TTL.

          Snapshot keys THEN act — never iterate-and-mutate (dict changed size).
          """
          now = time.monotonic()
          reaped: list[str] = []
          for rid in list(self._turns.keys()):  # snapshot
              turn = self._turns.get(rid)
              if turn is None:
                  continue
              done = turn.task is not None and turn.task.done()
              expired = (now - turn.started_at) >= ttl_seconds
              if (done and turn.status is not TurnStatus.DONE) or expired:
                  await self.deregister(rid)
                  reaped.append(rid)
                  log.gateway.warning("[turn] sweeper reaped", extra={"_fields": {"request_id": rid}})
          return reaped
  ```
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_turn_registry.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/gateway/turn_registry.py tests/gateway/test_turn_registry.py && uv run mypy src/stackowl/gateway/turn_registry.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/gateway/turn_registry.py v2/tests/gateway/test_turn_registry.py && git commit -m "feat(v2): TurnRegistry — Turn + one-way CAS status + per-session running/FIFO queue + snapshot-then-act sweeper (concurrent-msg §4.2)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 4 — Telegram per-message target (IngressMessage.chat_id → Turn.target → ResponseChunk.target)

**Files:**
- Modify: `v2/src/stackowl/gateway/scanner.py` (add `chat_id: int | None = None`)
- Modify: `v2/src/stackowl/channels/telegram/adapter.py` (set chat_id; `send_text(text, *, chat_id=None)`)
- Modify: `v2/src/stackowl/channels/cli_adapter.py` (chat_id stays None)
- Create: `v2/tests/channels/test_telegram_target.py`

- [ ] **Failing test** — `v2/tests/channels/test_telegram_target.py`:
  ```python
  from __future__ import annotations

  import pytest

  from stackowl.gateway.scanner import IngressMessage


  def test_ingress_message_carries_optional_chat_id() -> None:
      m = IngressMessage(text="hi", session_id="s1", channel="telegram", trace_id="req-1", chat_id=999)
      assert m.chat_id == 999
      m2 = IngressMessage(text="hi", session_id="s1", channel="cli", trace_id="req-2")
      assert m2.chat_id is None


  @pytest.mark.asyncio
  async def test_send_text_targets_explicit_chat_id_not_last(monkeypatch) -> None:
      from stackowl.channels.telegram.adapter import TelegramAdapter  # adjust ctor to real
      sent: list[tuple[int, str]] = []

      class _Bot:
          class api:  # noqa: N801
              @staticmethod
              async def send_message(chat_id: int, text: str, **_: object) -> None:
                  sent.append((chat_id, text))

      adapter = TelegramAdapter.__new__(TelegramAdapter)
      adapter._bot = _Bot()  # type: ignore[attr-defined]
      adapter._last_chat_id = 111  # type: ignore[attr-defined]
      await adapter.send_text("to-A", chat_id=222)
      await adapter.send_text("to-default")  # falls back to _last_chat_id
      assert (222, "to-A") in sent
      assert (111, "to-default") in sent
  ```
  (Adjust the bot/ctor shim to the real TelegramAdapter internals; the load-bearing assertion is: explicit `chat_id` wins, omitted falls back to `_last_chat_id`.)
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/channels/test_telegram_target.py -v`
- [ ] **Minimal impl:**
  - `scanner.py`: add `chat_id: int | None = None` to the frozen `IngressMessage` dataclass.
  - `telegram/adapter.py:529-539`: capture `chat_id=int(chat.id)` onto the IngressMessage (`IngressMessage(... trace_id=_mint_request_id(), chat_id=int(chat.id))`). Keep setting `_last_chat_id` for back-compat fallback.
  - `telegram/adapter.py:125-153`: change signature to `async def send_text(self, text: str, *, chat_id: int | None = None) -> None`; resolve `target = chat_id if chat_id is not None else self._last_chat_id`; guard + send to `target`. Update the internal `send` chunk-buffer caller to thread `chat_id` through.
  - `cli_adapter.py`: leave `chat_id=None` (the IngressMessage default) — no change needed beyond the Task 1 mint helper.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/channels/test_telegram_target.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/gateway/scanner.py src/stackowl/channels/ tests/channels/ && uv run mypy src/stackowl/gateway/scanner.py src/stackowl/channels/telegram/adapter.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/gateway/scanner.py v2/src/stackowl/channels/telegram/adapter.py v2/src/stackowl/channels/cli_adapter.py v2/tests/channels/test_telegram_target.py && git commit -m "feat(v2): per-message Telegram target — IngressMessage.chat_id + send_text(chat_id=) (concurrent-msg §4.5)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 5 — Non-blocking in-chat intake: delete serialize_prior, wire TurnRegistry into both loops, FIFO drain

**Files:**
- Modify: `v2/src/stackowl/gateway/clarify_pump.py` (delete `serialize_prior`; retire/re-key `_inflight` to request_id)
- Modify: `v2/src/stackowl/startup/orchestrator.py` (both loops)
- Modify: `v2/tests/gateway/test_clarify_pump.py` (DELIBERATE — serialize_prior removed)
- Create: `v2/tests/gateway/test_nonblocking_intake.py`

- [ ] **Failing test** — `v2/tests/gateway/test_nonblocking_intake.py`: drive the registry + a fake dispatcher to assert: (a) a mid-turn same-session message does NOT await the running turn (no `serialize_prior` call), it enqueues; (b) on completion the next queued intake is popped + dispatched in FIFO order; (c) a queued message gets an instant ack.
  ```python
  from __future__ import annotations

  import asyncio
  import pytest

  from stackowl.gateway.turn_registry import TurnRegistry


  @pytest.mark.asyncio
  async def test_midturn_same_session_enqueues_without_blocking() -> None:
      reg = TurnRegistry()
      gate = asyncio.Event()
      async def slow_turn() -> None:
          await gate.wait()
      t = asyncio.create_task(slow_turn())
      await reg.register("req-1", session_id="s1", task=t, target=None, original_input="first")
      # second message arrives while req-1 runs — must NOT block
      assert reg.running("s1") is not None
      reg.enqueue("s1", original_input="second", request_id="req-2", target=None)
      # intake returned immediately; running turn still in flight
      assert not t.done()
      gate.set(); await t
      await reg.deregister("req-1")
      nxt = reg.pop_next("s1")
      assert nxt is not None and nxt.original_input == "second"
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_nonblocking_intake.py -v`
- [ ] **Minimal impl:**
  - `clarify_pump.py`: delete `serialize_prior` (163-168). Re-key `_inflight` to `request_id` (the send task is per-request now) — or retire it entirely if the TurnRegistry's `Turn.task` subsumes it (grep for every `_inflight` reader first; `drain()` at 235-240 must still await all in-flight send tasks — switch it to iterate registry tasks or the re-keyed `_inflight`). `spawn_send` keys by `request_id`.
  - `orchestrator.py` (both loops, 740-832 + 903-992): replace the `await cli_pump.serialize_prior(...)` line with the TurnRegistry intake path:
    ```python
    running = turn_registry.running(msg.session_id)
    if running is None:
        writer, reader = stream_registry.create(msg.trace_id)
        state = PipelineState(trace_id=msg.trace_id, session_id=msg.session_id,
                              input_text=input_text, channel=msg.channel,
                              owl_name=decision.target, pipeline_step="start", interactive=True)
        producer = asyncio.create_task(backend.run(state))
        producer.add_done_callback(_log_pipeline_crash)
        await turn_registry.register(msg.trace_id, session_id=msg.session_id, task=producer,
                                     target=msg.chat_id, original_input=input_text)
        cli_pump.spawn_send(channel_adapter=adapter, reader=reader,
                            request_id=msg.trace_id, producer=producer, writer=writer)
    else:
        # P1: no router yet → always queue (P3 Task 16 inserts TurnRouter here)
        turn_registry.enqueue(msg.session_id, original_input=input_text,
                              request_id=msg.trace_id, target=msg.chat_id)
        await adapter.send_text("Queued — I'll start that next.")  # instant ack
    ```
  - On turn completion, the producer's done-callback (or a small teardown coroutine attached in `spawn_send`) calls a `_drain_next(session_id)` that `pop_next` + dispatches the queued intake exactly as the `running is None` branch does. Keep `spawn_send`'s `request_id` keying consistent.
- [ ] **DELIBERATE test update** — `test_clarify_pump.py::test_serialize_prior_awaits_unfinished_same_session` (175-185) asserts behavior of a method being deleted. Remove/replace it with a test asserting the NEW non-blocking intake (the running turn is not awaited at intake). FLAG in commit: "serialize_prior deleted by design (§4.1) — test replaced to assert non-blocking intake, not weakened."
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_nonblocking_intake.py tests/gateway/test_clarify_pump.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/gateway/ src/stackowl/startup/orchestrator.py && uv run mypy src/stackowl/gateway/clarify_pump.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/gateway/clarify_pump.py v2/src/stackowl/startup/orchestrator.py v2/tests/gateway/test_nonblocking_intake.py v2/tests/gateway/test_clarify_pump.py && git commit -m "feat(v2): non-blocking in-chat intake — delete serialize_prior, wire TurnRegistry FIFO drain into both loops (concurrent-msg §4.3)" -m "DELIBERATE: serialize_prior removed; clarify_pump test replaced to assert non-blocking intake" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 6 — Heartbeat/proactive delivery targets the correct chat_id

**Files:**
- Modify: `v2/src/stackowl/notifications/deliverer.py` (pass explicit chat_id)
- Create: `v2/tests/notifications/test_proactive_target.py`

- [ ] **Failing test** — `v2/tests/notifications/test_proactive_target.py`: assert `ProactiveDeliverer._transport` sends to the message's intended chat_id via `send_text(text, chat_id=...)`, NOT the global `_last_chat_id`. Use a fake adapter recording `(chat_id, text)`.
  ```python
  from __future__ import annotations
  import pytest

  @pytest.mark.asyncio
  async def test_proactive_uses_explicit_chat_id(monkeypatch) -> None:
      sent: list[tuple[int | None, str]] = []
      class _Adapter:
          async def send_text(self, text: str, *, chat_id: int | None = None) -> None:
              sent.append((chat_id, text))
      class _Reg:
          def get(self, channel: str) -> _Adapter:
              return _Adapter()
      from stackowl.notifications.deliverer import ProactiveDeliverer
      d = ProactiveDeliverer.__new__(ProactiveDeliverer)
      d._registry = _Reg()  # type: ignore[attr-defined]
      await d._transport("telegram", "ping", chat_id=777)  # signature extended
      assert sent == [(777, "ping")]
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/notifications/test_proactive_target.py -v`
- [ ] **Minimal impl** — `deliverer.py:132-170`: thread the destination chat_id (already known to the proactive job's recipient record) into `_transport(..., chat_id=...)` and call `await adapter.send_text(message, chat_id=chat_id)`. If the proactive record has no chat_id, pass `None` (falls back to `_last_chat_id` — back-compat). Log the resolved target. Per recon this stays a DIRECT send (not the stream path) — that is acceptable for P1; modeling proactive as a full Turn is §4.5 future polish (note in §11/backlog).
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/notifications/test_proactive_target.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/notifications/ tests/notifications/ && uv run mypy src/stackowl/notifications/deliverer.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/notifications/deliverer.py v2/tests/notifications/test_proactive_target.py && git commit -m "feat(v2): proactive/heartbeat delivery targets explicit chat_id, not _last_chat_id (concurrent-msg §4.5)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 7 — Concurrency caps: bounded per-session queue + global cap (host-probe-sized)

**Files:**
- Modify: `v2/src/stackowl/gateway/turn_registry.py` (queue max + global running cap)
- Create: `v2/tests/gateway/test_concurrency_caps.py`

- [ ] **Failing test** — `v2/tests/gateway/test_concurrency_caps.py`:
  ```python
  from __future__ import annotations
  import asyncio
  import pytest
  from stackowl.gateway.turn_registry import TurnRegistry, QueueFull


  @pytest.mark.asyncio
  async def test_per_session_queue_bounded() -> None:
      reg = TurnRegistry(per_session_queue_max=2, global_running_max=100)
      t = asyncio.create_task(asyncio.sleep(0))
      await reg.register("r0", session_id="s1", task=t, target=None, original_input="x")
      reg.enqueue("s1", original_input="a", request_id="r1", target=None)
      reg.enqueue("s1", original_input="b", request_id="r2", target=None)
      with pytest.raises(QueueFull):
          reg.enqueue("s1", original_input="c", request_id="r3", target=None)
      await t

  @pytest.mark.asyncio
  async def test_global_running_cap(monkeypatch) -> None:
      reg = TurnRegistry(per_session_queue_max=8, global_running_max=1)
      t = asyncio.create_task(asyncio.sleep(0))
      await reg.register("r0", session_id="s1", task=t, target=None, original_input="x")
      assert reg.at_global_capacity() is True  # second session must wait/queue
      await t
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_concurrency_caps.py -v`
- [ ] **Minimal impl** — `turn_registry.py`: add ctor params `per_session_queue_max: int` and `global_running_max: int` (default the global from a host capability probe — reuse the existing capability-probe util; never hardcode Jetson limits). Add `QueueFull(Exception)`. `enqueue` raises `QueueFull` past the per-session cap (the orchestrator catches it → reject-with-notice; coalesce-oldest is the §4.7 alternative — pick reject-with-notice for P1, note coalesce in backlog). Add `at_global_capacity() -> bool` = `len(self._running) >= self._global_running_max`; the orchestrator queues new turns when at capacity (bounded wait, loudly logged). Log on every overflow/at-capacity decision.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_concurrency_caps.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/gateway/turn_registry.py tests/gateway/test_concurrency_caps.py && uv run mypy src/stackowl/gateway/turn_registry.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/gateway/turn_registry.py v2/tests/gateway/test_concurrency_caps.py && git commit -m "feat(v2): concurrency caps — bounded per-session queue + host-probe global running cap (concurrent-msg §4.7)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 8 — Off-path guard test: concurrent cross-session turns do NOT trigger inline evolution/promotion (§4.6)

**Files:**
- Create: `v2/tests/journeys/test_evolution_promotion_off_turn_path.py`

- [ ] **Failing test** (lock the invariant) — drive two concurrent cross-session turns through the real pipeline (mirror `test_plan_a_gateway_integration.py`: real `ToolRegistry.with_defaults()`, real `AsyncioBackend`, `_RecordingProvider`), spy on `EvolutionCoordinator.handle`/`run` and `FactPromoter.promote_eligible` (monkeypatch to record calls). Assert **neither is invoked** during the turns — they remain scheduler/DreamWorker-driven. Also assert `consolidate` only STAGED facts (one `bridge.store` per turn, no embed call).
  ```python
  from __future__ import annotations
  import asyncio
  import pytest

  @pytest.mark.asyncio
  async def test_concurrent_turns_do_not_inline_evolution_or_promotion(monkeypatch) -> None:
      evo_calls: list[str] = []
      promo_calls: list[str] = []
      monkeypatch.setattr(
          "stackowl.owls.evolution.EvolutionCoordinator.handle",
          lambda self, *a, **k: evo_calls.append("evo") or asyncio.sleep(0),
          raising=False,
      )
      monkeypatch.setattr(
          "stackowl.memory.fact_promoter.FactPromoter.promote_eligible",
          lambda self, *a, **k: promo_calls.append("promo") or asyncio.sleep(0),
          raising=False,
      )
      # ... build two sessions (s1,s2) same owl, dispatch both through AsyncioBackend, await both ...
      assert evo_calls == [], "evolution must stay off the turn path (§4.6 invariant)"
      assert promo_calls == [], "promotion must stay off the turn path (§4.6 invariant)"
  ```
  (Flesh out the two-turn dispatch using the Task-2/Task-5 wiring; the load-bearing assertions are the two empty lists.)
- [ ] **Run to fail then pass** (this should PASS once written if the invariant holds — if it FAILS, STOP and inform the developer per the no-silent-fix rule; a failure means evolution/promotion is unexpectedly on the turn path): `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/journeys/test_evolution_promotion_off_turn_path.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check tests/journeys/test_evolution_promotion_off_turn_path.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/tests/journeys/test_evolution_promotion_off_turn_path.py && git commit -m "test(v2): guard — concurrent cross-session turns never inline evolution/promotion (concurrent-msg §4.6 invariant)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 9 — Callback splice contract: IterationCallback returns `list[dict] | None`; providers extend messages

**Files:**
- Modify: `v2/src/stackowl/providers/react_callback.py` (return type)
- Modify: `v2/src/stackowl/pipeline/steps/execute.py` (budget + checkpoint factories return None)
- Modify: `v2/src/stackowl/providers/openai_provider.py` (extend at 2 call sites)
- Modify: `v2/src/stackowl/providers/anthropic_provider.py` (extend at 2 call sites)
- Create: `v2/tests/providers/test_callback_splice.py`

- [ ] **Failing test** — `v2/tests/providers/test_callback_splice.py`: a fake provider loop calling an `on_iteration_complete` that returns `[{"role": "user", "content": "[steering] also do X"}]`; assert the returned list is appended to `messages` before the next LLM round (and a callback returning `None` appends nothing). Drive the real provider with a stub LLM client that records the `messages` it receives on the 2nd call.
  ```python
  from __future__ import annotations
  import pytest
  from stackowl.providers.react_callback import ReActIterationState


  @pytest.mark.asyncio
  async def test_callback_returned_messages_are_spliced() -> None:
      folded = [{"role": "user", "content": "[steering] also include Y"}]
      async def cb(state: ReActIterationState) -> list[dict] | None:
          return folded if state.iteration == 0 else None
      # drive openai_provider.complete_with_tools with a 2-iteration stub client;
      # assert the second LLM call's messages contains the folded steering message,
      # and that the provider did NOT defensively copy messages away from the fold.
      ...
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/providers/test_callback_splice.py -v`
- [ ] **Minimal impl:**
  - `react_callback.py`: change `IterationCallback = Callable[[ReActIterationState], Awaitable[list[dict[str, Any]] | None]]`. Update the module docstring.
  - `execute.py`: `make_budget_callback` (409-419) and `make_checkpoint_callback` (488) and the composed `_cb_with_budget` (492-496) all explicitly `return None` (they do side-effects only).
  - `openai_provider.py` (sites 330-337, 388-395) and `anthropic_provider.py` (sites 251-252, 294-295): at each call site,
    ```python
    folded = await on_iteration_complete(ReActIterationState(iteration=_iter_idx, messages=list(messages), tool_call_records=list(all_calls)))
    if folded:
        messages.extend(folded)
    ```
    Verify `messages` is the live local list (recon: `messages = list(resume_messages)` at openai 173 / anthropic 160 — it IS a local list the loop mutates, so `.extend` lands; the `list(messages)` passed to the callback is a copy — the fold returns NEW messages, not a mutation of the copy). One line of `if folded: messages.extend(folded)` per site.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/providers/test_callback_splice.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/providers/ src/stackowl/pipeline/steps/execute.py tests/providers/ && uv run mypy src/stackowl/providers/react_callback.py src/stackowl/providers/openai_provider.py src/stackowl/providers/anthropic_provider.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/providers/react_callback.py v2/src/stackowl/pipeline/steps/execute.py v2/src/stackowl/providers/openai_provider.py v2/src/stackowl/providers/anthropic_provider.py v2/tests/providers/test_callback_splice.py && git commit -m "feat(v2): callback splice contract — IterationCallback returns list[dict]|None, providers extend messages (concurrent-msg §5.1)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 10 — Steering closure in execute.py: drain turn mailbox, fold `[steering]` message

**Files:**
- Modify: `v2/src/stackowl/pipeline/steps/execute.py` (build a steering-drain closure, compose with existing callbacks)
- Create: `v2/tests/pipeline/test_steering_fold.py`

- [ ] **Failing test** — `v2/tests/pipeline/test_steering_fold.py`: register a Turn in a TurnRegistry, put a steering string on its mailbox, invoke the execute step's steering closure for that `trace_id`, assert it returns `[{"role": "user", "content": "[steering] ..."}]` (drained via `get_nowait`), and that an empty mailbox returns `None` (NEVER blocks on `await get()`).
  ```python
  from __future__ import annotations
  import asyncio
  import pytest
  from stackowl.gateway.turn_registry import TurnRegistry
  from stackowl.pipeline.steps.execute import make_steering_callback
  from stackowl.providers.react_callback import ReActIterationState


  @pytest.mark.asyncio
  async def test_steering_drain_folds_pending_message() -> None:
      reg = TurnRegistry()
      t = asyncio.create_task(asyncio.sleep(0))
      turn = await reg.register("req-1", session_id="s1", task=t, target=None, original_input="research X")
      turn.steering_mailbox.put_nowait("also include Y")
      cb = make_steering_callback(reg, "req-1")
      folded = await cb(ReActIterationState(iteration=0, messages=[], tool_call_records=[]))
      assert folded is not None
      assert "[steering]" in folded[0]["content"] and "include Y" in folded[0]["content"]
      # empty mailbox -> None, and never blocks
      assert await asyncio.wait_for(cb(ReActIterationState(iteration=1, messages=[], tool_call_records=[])), timeout=1.0) is None
      await t
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/test_steering_fold.py -v`
- [ ] **Minimal impl** — `execute.py`: add a factory `make_steering_callback(registry, request_id)`:
  ```python
  def make_steering_callback(registry, request_id):
      async def _cb(state):
          turn = registry.get(request_id)
          if turn is None:
              return None
          drained: list[str] = []
          while True:
              try:
                  drained.append(turn.steering_mailbox.get_nowait())  # NEVER await get()
              except asyncio.QueueEmpty:
                  break
          if not drained:
              return None
          log.engine.debug("[steer] folding steering messages", extra={"_fields": {"request_id": request_id, "count": len(drained)}})
          merged = " ".join(drained)
          return [{"role": "user", "content": f"[steering] {merged}"}]
      return _cb
  ```
  Compose it into the `on_iteration_complete` chain (both `_call_default` and `_call_durable`) so its returned messages are concatenated with any (None-returning) budget/checkpoint callbacks. Reach the registry via the execute ctx (thread the TurnRegistry into the execute step's deps; it already has session context). Coalesce here is the merge of all drained items (§5.4 minimal form; Task 13 hardens bound/coalesce).
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/test_steering_fold.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/pipeline/steps/execute.py tests/pipeline/test_steering_fold.py && uv run mypy src/stackowl/pipeline/steps/execute.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/steps/execute.py v2/tests/pipeline/test_steering_fold.py && git commit -m "feat(v2): steering closure — drain turn mailbox (get_nowait loop), fold [steering] message at iteration boundary (concurrent-msg §5.1)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 11 — Lost-steer CAS guard: re-check mailbox under lock before FINALIZING; guarded enqueue; teardown re-route

**Files:**
- Modify: `v2/src/stackowl/gateway/turn_registry.py` (guarded `try_steer` + `finalize_if_drained` + `drain_survivors`)
- Modify: `v2/src/stackowl/pipeline/steps/execute.py` (loop re-check at terminal boundary)
- Create: `v2/tests/gateway/test_lost_steer_cas.py`

- [ ] **Failing test** (§9 invariant 1) — `v2/tests/gateway/test_lost_steer_cas.py`: randomized interleaving of "steer arrives" vs "turn finalizes" with a controllable barrier; assert **zero lost steers** across many runs — every steer is either accepted (RUNNING) or converted to a queued-new turn (FINALIZING/DONE), never silently dropped.
  ```python
  from __future__ import annotations
  import asyncio, random
  import pytest
  from stackowl.gateway.turn_registry import TurnRegistry, TurnStatus


  @pytest.mark.asyncio
  async def test_no_lost_steers_across_randomized_interleavings() -> None:
      for seed in range(200):
          random.seed(seed)
          reg = TurnRegistry()
          t = asyncio.create_task(asyncio.sleep(0))
          await reg.register("r1", session_id="s1", task=t, target=None, original_input="orig")
          accepted: list[str] = []
          queued_new: list[str] = []

          async def steer() -> None:
              outcome = await reg.try_steer("r1", "corr", session_id="s1", request_id="r2", target=None)
              (accepted if outcome == "STEER" else queued_new).append("corr")

          async def finish() -> None:
              # loop re-checks mailbox under lock before finalizing
              while not await reg.finalize_if_drained("r1"):
                  await asyncio.sleep(0)
              await reg.cas_status("r1", TurnStatus.FINALIZING, TurnStatus.DONE)

          await asyncio.gather(steer(), finish())
          # exactly one of accepted/queued_new holds the steer; never neither
          assert len(accepted) + len(queued_new) == 1
          survivors = await reg.drain_survivors("r1")  # teardown re-route
          # any steer accepted-but-not-folded becomes queued-new on teardown
          await t
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_lost_steer_cas.py -v`
- [ ] **Minimal impl** — `turn_registry.py`:
  - `try_steer(request_id, text, *, session_id, request_id_new, target) -> str`: take `turn.lock`; read status; if `RUNNING` → `mailbox.put_nowait(text)`; return `"STEER"`; else (`FINALIZING`/`DONE`) → `enqueue(session_id, original_input=text, request_id=request_id_new, target=target)`; return `"NEW"`. Status read + put are atomic under the lock.
  - `finalize_if_drained(request_id) -> bool`: take `turn.lock`; if mailbox non-empty → return `False` (caller loops again, does NOT finalize); else CAS `RUNNING→FINALIZING` and return `True`.
  - `drain_survivors(request_id) -> list[str]`: on teardown, drain remaining mailbox items and re-route each as a queued-new turn (`enqueue`), returning them. A discarded steer is a lost instruction → convert, never GC. Log each survivor re-route.
  - `execute.py` terminal sequence: before the loop exits / the turn finalizes, call `finalize_if_drained` in a loop (loop again if it returns False — folds any last-moment steer before going FINALIZING).
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_lost_steer_cas.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/gateway/turn_registry.py src/stackowl/pipeline/steps/execute.py tests/gateway/test_lost_steer_cas.py && uv run mypy src/stackowl/gateway/turn_registry.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/gateway/turn_registry.py v2/src/stackowl/pipeline/steps/execute.py v2/tests/gateway/test_lost_steer_cas.py && git commit -m "feat(v2): lost-steer CAS guard — atomic steer-vs-finalize, teardown re-routes survivors as queued-new (concurrent-msg §5.2)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 12 — Cooperative stop: stop_requested flag honored at iteration boundary

**Files:**
- Modify: `v2/src/stackowl/pipeline/steps/execute.py` (check `stop_requested` in the closure; finalize gracefully)
- Modify: `v2/src/stackowl/gateway/turn_registry.py` (`request_stop(request_id)`)
- Create: `v2/tests/pipeline/test_cooperative_stop.py`

- [ ] **Failing test** — `v2/tests/pipeline/test_cooperative_stop.py`: a turn with `stop_requested` set finalizes at the next iteration boundary — writes a "stopped" chunk, closes the stream — and the underlying task is NOT `cancel()`-ed (assert no `CancelledError`, assert a graceful "stopped" terminal chunk).
  ```python
  from __future__ import annotations
  import asyncio
  import pytest
  from stackowl.gateway.turn_registry import TurnRegistry
  from stackowl.pipeline.steps.execute import make_steering_callback


  @pytest.mark.asyncio
  async def test_stop_flag_finalizes_at_boundary_not_cancel() -> None:
      reg = TurnRegistry()
      t = asyncio.create_task(asyncio.sleep(0))
      turn = await reg.register("r1", session_id="s1", task=t, target=None, original_input="x")
      reg.request_stop("r1")
      assert turn.stop_requested is True
      # the execute closure surfaces stop to the loop; the loop finalizes gracefully.
      # assert the closure signals stop (e.g. returns a sentinel / sets a flag the loop reads)
      # and that task.cancel() was NEVER called.
      assert not t.cancelled()
      await t
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/test_cooperative_stop.py -v`
- [ ] **Minimal impl:**
  - `turn_registry.py`: `request_stop(request_id)` sets `turn.stop_requested = True` (log it).
  - `execute.py`: in the steering closure (or a sibling check), after draining/folding, read `registry.get(request_id).stop_requested`; if set, signal the loop to finalize gracefully after the current tool batch is fully observed (write a "stopped" ResponseChunk, close the stream). NOT `task.cancel()` — flag only. Stop is cooperative at iteration granularity, bounded-latency (cannot interrupt a 90s in-flight tool — documented, not a bug). The D1 ledger shield is out of scope (interactive turns have no task_id → ledger dormant — §5.3/backlog).
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/test_cooperative_stop.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/pipeline/steps/execute.py src/stackowl/gateway/turn_registry.py tests/pipeline/test_cooperative_stop.py && uv run mypy src/stackowl/pipeline/steps/execute.py src/stackowl/gateway/turn_registry.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/steps/execute.py v2/src/stackowl/gateway/turn_registry.py v2/tests/pipeline/test_cooperative_stop.py && git commit -m "feat(v2): cooperative stop — stop_requested flag finalizes at iteration boundary, never task.cancel() (concurrent-msg §5.3)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 13 — Bounded mailbox + coalesce under steer-spam

**Files:**
- Modify: `v2/src/stackowl/gateway/turn_registry.py` (coalesce-on-full; supersede oldest)
- Create: `v2/tests/gateway/test_mailbox_coalesce.py`

- [ ] **Failing test** — `v2/tests/gateway/test_mailbox_coalesce.py`: spam N steers (N > mailbox max) at a slow turn; assert the mailbox never exceeds its bound and the loop folds a coalesced/merged latest (not all N, which would blow context). A full mailbox supersedes the oldest pending steer.
  ```python
  from __future__ import annotations
  import asyncio
  import pytest
  from stackowl.gateway.turn_registry import TurnRegistry


  @pytest.mark.asyncio
  async def test_mailbox_bounded_and_coalesces_under_spam() -> None:
      reg = TurnRegistry()
      t = asyncio.create_task(asyncio.sleep(0))
      turn = await reg.register("r1", session_id="s1", task=t, target=None, original_input="x")
      for i in range(50):
          reg.put_steer("r1", f"steer-{i}")  # bounded + supersede-oldest
      assert turn.steering_mailbox.qsize() <= turn.steering_mailbox.maxsize
      # the newest steers survive (oldest superseded)
      drained = []
      while not turn.steering_mailbox.empty():
          drained.append(turn.steering_mailbox.get_nowait())
      assert "steer-49" in drained
      await t
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_mailbox_coalesce.py -v`
- [ ] **Minimal impl** — `turn_registry.py`: add `put_steer(request_id, text)` that `put_nowait`s; on `asyncio.QueueFull`, drop the oldest (`get_nowait()`) then `put_nowait(text)` — supersede-oldest backpressure (log the supersede). The steering closure's drain (Task 10) already merges all drained items into one `[steering]` message — that IS the coalesce on the fold side. Keep `_MAILBOX_MAX` bounded.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_mailbox_coalesce.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/gateway/turn_registry.py tests/gateway/test_mailbox_coalesce.py && uv run mypy src/stackowl/gateway/turn_registry.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/gateway/turn_registry.py v2/tests/gateway/test_mailbox_coalesce.py && git commit -m "feat(v2): bounded steering mailbox + supersede-oldest coalesce under spam (concurrent-msg §5.4)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 14 — Create `gateway/turn_router.py`: explicit-signal parser

**Files:**
- Create: `v2/src/stackowl/gateway/turn_router.py`
- Create: `v2/tests/gateway/test_turn_router_signals.py`

- [ ] **Failing test** — `v2/tests/gateway/test_turn_router_signals.py`: assert the explicit-signal parser maps deterministically: `stop`/cancel → `STOP`; `/steer` (or Telegram reply-to-in-flight) → `STEER`; `/new` → `NEW`; clarify-pending + answer → `REPLY` (defer to clarify path). Use Unicode-safe matching (no hardcoded English-only regex — `\p{L}` via the `regex` module or casefold + token compare; the signal tokens are the slash-commands which are language-neutral).
  ```python
  from __future__ import annotations
  import pytest
  from stackowl.gateway.turn_router import ExplicitSignal, parse_explicit_signal


  @pytest.mark.parametrize("text,expected", [
      ("/stop", ExplicitSignal.STOP),
      ("stop", ExplicitSignal.STOP),
      ("/steer also include Y", ExplicitSignal.STEER),
      ("/new what's the weather", ExplicitSignal.NEW),
      ("just a normal message", ExplicitSignal.NONE),
  ])
  def test_explicit_signal_parsing(text: str, expected: ExplicitSignal) -> None:
      assert parse_explicit_signal(text, is_reply_to_inflight=False) == expected


  def test_telegram_reply_to_inflight_is_steer() -> None:
      assert parse_explicit_signal("a correction", is_reply_to_inflight=True) == ExplicitSignal.STEER
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_turn_router_signals.py -v`
- [ ] **Minimal impl** — `turn_router.py`: `ExplicitSignal` enum (`STOP, STEER, NEW, REPLY, NONE`); `parse_explicit_signal(text, *, is_reply_to_inflight)`: leading `/stop` or bare stop-token → STOP; `/steer` or `is_reply_to_inflight` → STEER; `/new` → NEW; else NONE. Slash-prefix tokens are language-neutral; the bare `stop` keyword check must be a configurable token set, NOT a hardcoded English literal (per the no-hardcoded-English rule — make the stop tokens a small configurable set, default including common forms, casefolded). Log the parsed signal.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_turn_router_signals.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/gateway/turn_router.py tests/gateway/test_turn_router_signals.py && uv run mypy src/stackowl/gateway/turn_router.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/gateway/turn_router.py v2/tests/gateway/test_turn_router_signals.py && git commit -m "feat(v2): TurnRouter explicit-signal parser — STOP/STEER/NEW/REPLY deterministic (concurrent-msg §6.1)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 15 — Conservative STEER-vs-NEW classifier + turn-veto (generalize ClarifyIntentClassifier)

**Files:**
- Modify: `v2/src/stackowl/interaction/intent_classifier.py` (generalize to STEER-vs-NEW)
- Modify: `v2/src/stackowl/gateway/turn_router.py` (wire classifier + two-stage veto)
- Create: `v2/tests/gateway/test_turn_router_classifier.py`

- [ ] **Failing test** (§9 invariant 4) — `v2/tests/gateway/test_turn_router_classifier.py`: feed ambiguous corrections; assert **STEER only at HIGH confidence**, everything uncertain → NEW (false-STEER rate ≈ 0); assert a HIGH-confidence STEER offered to a vetoing turn → NEW. Mock the fast-tier provider verdicts.
  ```python
  from __future__ import annotations
  import pytest


  @pytest.mark.asyncio
  async def test_uncertain_defaults_to_new_steer_only_high_conf() -> None:
      # classifier returns "uncertain" -> route NEW (never STEER on doubt)
      ...

  @pytest.mark.asyncio
  async def test_high_conf_steer_but_turn_vetoes_becomes_new() -> None:
      # stage1 says STEER (high conf); stage2 turn-veto says "doesn't fit" -> NEW
      ...
  ```
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_turn_router_classifier.py -v`
- [ ] **Minimal impl:**
  - `intent_classifier.py`: add a method `async def is_steer(self, *, running_ask: str, message: str) -> bool` reusing the fast-tier one-token verdict shape of `is_answer` (max_tokens=4, `_parse_verdict`-style). Asymmetric fail-safe: on error/ambiguity → return `False` (NEW), the OPPOSITE of `is_answer`'s fail-safe-True — because false-STEER poisons a turn AND loses the new ask invisibly, while false-NEW is a recoverable visible second answer. Two-stage (reuse the D3 pattern): only return True at HIGH confidence.
  - `turn_router.py`: a `route(...)` that calls `parse_explicit_signal` first; on NONE, calls `is_steer`; a proposed STEER is then offered to the running turn for veto (stage 2 — the turn's own LLM judges coherence; reuse D3 two-stage); veto → NEW. Fail-safe everywhere → NEW.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_turn_router_classifier.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/interaction/intent_classifier.py src/stackowl/gateway/turn_router.py tests/gateway/test_turn_router_classifier.py && uv run mypy src/stackowl/interaction/intent_classifier.py src/stackowl/gateway/turn_router.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/interaction/intent_classifier.py v2/src/stackowl/gateway/turn_router.py v2/tests/gateway/test_turn_router_classifier.py && git commit -m "feat(v2): conservative STEER-vs-NEW classifier (high-conf only, fail-safe NEW) + two-stage turn-veto (concurrent-msg §6.2/§6.3)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 16 — Wire TurnRouter into both gateway loops at arrival; fail-safe queued-new

**Files:**
- Modify: `v2/src/stackowl/startup/orchestrator.py` (both loops — insert router at the `running is not None` branch)
- Create: `v2/tests/gateway/test_router_wiring.py`

- [ ] **Failing test** — `v2/tests/gateway/test_router_wiring.py`: with a running turn for the session, assert arrival routing dispatches: explicit `/steer` → `try_steer` on the running turn; `stop` → `request_stop`; `/new` / uncertain classifier → enqueue + instant ack; any router error → fail-safe queued-new (loudly logged). Idle session (no running turn) skips the router entirely (zero added latency).
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_router_wiring.py -v`
- [ ] **Minimal impl** — `orchestrator.py` (both loops): replace the P1 "always queue" `else` branch (Task 5) with:
  ```python
  else:
      try:
          decision = await turn_router.route(
              text=input_text, session_id=msg.session_id, request_id=msg.trace_id,
              target=msg.chat_id, is_reply_to_inflight=getattr(msg, "is_reply", False),
          )
      except Exception as exc:  # fail-safe: never block, never mis-steer
          log.gateway.error("[router] route failed — fail-safe queued-new", exc_info=exc)
          decision = "NEW"
      if decision == "STEER":
          await turn_registry.try_steer(running.turn_id, input_text, session_id=msg.session_id, request_id_new=msg.trace_id, target=msg.chat_id)
      elif decision == "STOP":
          turn_registry.request_stop(running.turn_id)
          await adapter.send_text("Stopping the current task at the next safe point.")
      else:  # NEW / queued
          turn_registry.enqueue(msg.session_id, original_input=input_text, request_id=msg.trace_id, target=msg.chat_id)
          await adapter.send_text("Queued — I'll start that next.")
  ```
  Idle (`running is None`) path is unchanged from Task 5 → router never runs for idle sessions.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/gateway/test_router_wiring.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check src/stackowl/startup/orchestrator.py tests/gateway/test_router_wiring.py && uv run mypy src/stackowl/startup/orchestrator.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/startup/orchestrator.py v2/tests/gateway/test_router_wiring.py && git commit -m "feat(v2): wire TurnRouter into both gateway loops, fail-safe queued-new, idle skips router (concurrent-msg §6/§7)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 17 — P1 journey: cross-session parallel + correlated; in-chat queued non-blocking; Telegram no cross-deliver

**Files:**
- Create: `v2/tests/journeys/test_p1_concurrent_foundation.py`

- [ ] **Failing test** — mirror `tests/pipeline/test_plan_a_gateway_integration.py` (real `GatewayScanner.scan`, real `ToolRegistry.with_defaults()`, `_RecordingProvider` via real `ProviderRegistry`, real `AsyncioBackend`, real `StreamRegistry` + `TurnRegistry`). Assert:
  - Two **different session_ids** dispatched concurrently run in parallel and each reply correlates to its own `request_id` stream (no cross-read).
  - A second **same-session** message mid-turn is accepted instantly (non-blocking — the intake call returns before the first turn completes) and is queued, then runs after the first finishes (FIFO).
  - Telegram: two sessions with different `chat_id`s never cross-deliver (`send_text` targets the per-message chat_id).
  - `state.trace_id` is populated at deliver and equals `TraceContext.current().trace_id`.
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/journeys/test_p1_concurrent_foundation.py -v`
- [ ] **Minimal impl** — wiring already exists (Tasks 1-9); this journey may surface an integration gap. If a real wiring bug surfaces, STOP and inform the developer (no silent fix — the failing integration test = possible broken wiring). Otherwise the test passes against the assembled P1.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/journeys/test_p1_concurrent_foundation.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check tests/journeys/test_p1_concurrent_foundation.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/tests/journeys/test_p1_concurrent_foundation.py && git commit -m "test(v2): P1 journey — cross-session parallel+correlated, in-chat queued non-blocking, Telegram no cross-deliver (concurrent-msg §9)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 18 — P2+P3 merge-gate journey: research + mid-turn ADD-steer + parallel second chat + contradiction-degrades

**Files:**
- Create: `v2/tests/journeys/test_p2p3_steer_merge_gate.py`

- [ ] **Failing test** (the spec §9 merge-gate) — real channel adapters + gateway, mocking ONLY the AI provider:
  - User (session A) sends "research X" → a turn starts.
  - Mid-turn (session A) sends "also include Y" → classifier/explicit → ADD → STEERs the running turn; assert the running turn's output reflects Y (the folded `[steering]` message reached the provider's `messages`).
  - From a **second chat** (session B) "what's the weather" → runs truly in parallel, separate correlated reply, no cross-deliver.
  - Then (session A) "no, I meant Z" → contradiction → conservative classifier low-conf OR turn-veto → **queued-new**; assert it runs as a fresh coherent turn after the steered research finishes (does NOT blend two goals into nonsense).
  - Assert outcomes (correlation, no cross-deliver, steer-applied, parallel-cross-chat, contradiction-degrades-coherently) — NOT tool names.
- [ ] **Run to fail:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/journeys/test_p2p3_steer_merge_gate.py -v`
- [ ] **Minimal impl** — assembled from Tasks 10-16; if a real wiring gap surfaces, STOP and inform the developer (no silent fix). Otherwise green against the full feature.
- [ ] **Run to pass:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/journeys/test_p2p3_steer_merge_gate.py -v`
- [ ] **Lint/type:** `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run ruff check tests/journeys/test_p2p3_steer_merge_gate.py`
- [ ] **Commit:** `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/tests/journeys/test_p2p3_steer_merge_gate.py && git commit -m "test(v2): P2+P3 merge-gate journey — ADD-steer applied, parallel cross-chat, contradiction degrades to queued-new (concurrent-msg §9)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Self-Review

### Spec coverage (every invariant → task)

| Spec section | Requirement | Task(s) |
|---|---|---|
| §4.1 | Re-key streams by request_id; delete serialize_prior; unique+non-empty mint; deliver `get_writer(state.trace_id)`; `state.trace_id == TraceContext.current()` | 1, 2, 5 |
| §4.2 | TurnRegistry: running+queue, Turn fields, one-way CAS, finally-deregister, snapshot-then-act sweeper, clarify_pending as turn status | 3 (+ clarify_pending field 3; folded into router 15/16) |
| §4.3 | Non-blocking intake + FIFO queue-drain on completion + instant ack | 5 |
| §4.4 | Cross-session parallelism (free from re-key) | 2, 5, 17 (journey) |
| §4.5 | Atomic tagged blob; Telegram per-message target; stream-miss hard-drop+log; heartbeat as Turn | 2 (hard-drop), 4 (target), 6 (heartbeat — DIRECT send fixed; full-Turn modeling → backlog) |
| §4.6 | Evolution/promotion off-path — guard test, no new serialization | 8 |
| §4.7 | Bounded per-session queue + host-probe global cap | 7 |
| §5.1 | Mailbox drain (get_nowait loop, never await get); splice contract (callback returns list, providers extend, no defensive copy) | 9 (contract), 10 (drain/fold) |
| §5.2 | Lost-steer CAS: re-check under lock before FINALIZING; guarded enqueue; teardown re-route survivors | 11 |
| §5.3 | Cooperative stop flag at iteration boundary (never cancel); ledger-shield OUT (durable-only) | 12 (+ ledger-shield → backlog) |
| §5.4 | Bounded mailbox + coalesce/supersede-oldest | 13 |
| §5.5 | Coherence caveat + turn-veto degrades contradiction to queued-new | 15 (veto), 18 (journey) |
| §6 | Hybrid arrival: explicit > conservative high-conf classifier + veto > queued-new; fail-safe queued-new | 14 (explicit), 15 (classifier+veto), 16 (wiring+fail-safe) |
| §9 inv.1 | Steer atomic w/ status, zero lost steers | 11 |
| §9 inv.2 | Durable op uninterruptible | N/A — interactive turns dormant ledger (recon); → backlog |
| §9 inv.3 | Commit/promotion await-free, no deadlock | 8 (off-path locks invariant; per-owl lock not built per recon) |
| §9 inv.4 | STEER high-conf only; uncertain→new; veto | 15 |
| §10 | All 7 load-bearing invariants | 1-2 (inv.2 routing), 3/5 (inv.1), 11 (inv.3), 12 (inv.4), 8 (inv.5), 14-16 (inv.6), §2 policy (inv.7 — no task, documented) |

### Backlog (deferred per spec §11, recon-confirmed)
- D1 ledger `asyncio.shield(begin→commit)` for stoppable durable goal-turns — interactive turns never touch the ledger (§5.3).
- Per-owl evolution/promotion serialization — both off-path today (§4.6); guard test (Task 8) fails loudly if a future change moves them on-turn.
- Heartbeat modeled as a full Turn through the stream path — Task 6 fixes the DIRECT-send target bug; full-Turn modeling is §4.5 polish.
- Token streaming, same-chat true parallelism, supersede-as-primitive, reply-threading richness, crash-durable interactive turns — all cut (§2/§11).
- Per-session queue coalesce-on-overflow (Task 7 ships reject-with-notice; coalesce is the §4.7 alternative).

### Placeholder scan
No `TODO`/`FIXME`/`...`-as-impl in any impl step; every code block is real. Journey tests (17, 18) contain prose-described assertions to be fleshed against the assembled wiring (standard for gateway journeys) — flagged, not placeholders.

### Type/name consistency
- `Turn`, `TurnRegistry`, `TurnRouter`, `TurnStatus`, `PendingIntake`, `ExplicitSignal`, `QueueFull` — used consistently across Tasks 3, 7, 11, 14, 16.
- `ResponseChunk.target: int | None = None` (Task 2) consistent with Telegram `chat_id: int` and `Turn.target: int | None` (Tasks 3, 4).
- `IngressMessage.chat_id: int | None = None` (Task 4) consistent with mint sites (Task 1) and orchestrator wiring (Tasks 5, 16).
- `IterationCallback` return type `list[dict[str, Any]] | None` (Task 9) consistent with provider `if folded: messages.extend(folded)` (Task 9) and `make_steering_callback` return (Task 10).
- Status flow `RUNNING → FINALIZING → DONE` one-way via `_NEXT` map; CAS rejects skips/reversals (Task 3); re-checked under lock before FINALIZING (Task 11).
- `try_steer`/`finalize_if_drained`/`drain_survivors`/`request_stop`/`put_steer`/`enqueue`/`pop_next`/`register`/`deregister`/`sweep`/`running`/`get`/`at_global_capacity` — single coherent TurnRegistry surface across Tasks 3, 7, 11, 12, 13, 16.

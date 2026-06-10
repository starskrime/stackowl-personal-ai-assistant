# Concurrent / Steerable Message Handling (Design Spec)

**Status:** Approved design (2026-06-09), pre-plan.
**Branch:** `feat/agentic-os-stage1`.
**Origin:** user report — "the assistant is not async; a new message waits for the previous one, and it doesn't check whether the new message is related to the in-flight work or a new/different ask."

---

## 0. Context

Today messages **serialize** at exactly one gate. The pipeline run and delivery are *already* backgrounded asyncio tasks (`asyncio.create_task(backend.run(state))`); there is no lock or busy-flag. A new same-session message blocks at `gateway/clarify_pump.py:163` — `await serialize_prior(session_id)` — which awaits the prior turn's send task. That gate exists for one structural reason: the response stream (`pipeline/streaming.py`) is keyed by **`session_id`**, so only one turn per session can own the stream slot (`deliver.py:42` resolves `get_writer(state.session_id)`). A per-message `trace_id` already exists (CLI `cli_adapter.py:141`, Telegram `adapter.py:535`) and already rides inside every `ResponseChunk`, but it is logging-only — routing is by session_id everywhere. A `ClarifyIntentClassifier` (`interaction/intent_classifier.py`) already does a one-token ANSWER-vs-NEW verdict, but only when a clarify question is pending.

This spec reflects the 2026-06-09 party-mode stress-test (Winston/architecture, Murat/race-correctness, Amelia/asyncio-mechanics, Dr. Quinn/abstraction). The squad reshaped the model and contributed the guard invariants in §5/§8. The user resolved the three load-bearing decisions: **(1)** within a chat, non-blocking-but-serialized (true parallelism only across chats/sessions); **(2)** live-steer the running turn (kept); **(3)** hybrid relatedness — explicit user signals win, else a conservative high-confidence classifier with a turn-veto, else default to a queued new turn.

---

## 1. Goal & the reconciled model

**The guarantees to the user:**

> "A new message never hangs behind your previous one — it's accepted instantly. Within one chat I run one turn-of-attention at a time so my answers stay coherent, but I'll either fold your follow-up into what I'm doing or queue it — never block on it. Across different chats I work genuinely in parallel, and every reply is tied to the message it answers. When I 'steer' a running turn with your correction, the turn itself can reject a correction that doesn't fit — in which case I treat it as a fresh ask rather than blend two goals into nonsense."

**The model in one diagram:**

- **Per chat (= per session):** at most **one RUNNING turn** + a **FIFO queue** of pending intake. Intake is non-blocking (accept → decide → ack). A mid-turn message is routed (§6) to one of: **STEER** the running turn (live, §5), or **QUEUE** as a new turn (runs when the current finishes), or **STOP** (cooperative cancel).
- **Across chats/sessions:** genuinely parallel — each turn owns its own request-id stream slot and its own per-session history. No shared mutable state except per-owl persona/memory (§4.6).

This deletes the hardest race class (two concurrent writers to one chat's history) **by construction**, while still killing the blocking the user reported and giving true cross-chat concurrency.

---

## 2. Scope

**In scope:**
- Re-key the response stream by request-id ⇒ delete `serialize_prior`; cross-session parallelism + request↔response correlation.
- A `TurnRegistry` (in-memory) tracking the running turn + intake queue per session, with a steering mailbox + cooperative-stop per turn.
- Live-steer: fold a `[steering]` message into the running ReAct loop between iterations, with the lost-steer + D1-ledger-cancel guards.
- Hybrid arrival policy: explicit signals → deterministic; else conservative classifier + turn-veto; else queued-new.
- Telegram per-message target routing (fix the `_last_chat_id` cross-delivery bug).
- Per-owl serialization of DNA-evolution + memory-promotion (the cross-session shared-state race).
- Atomic per-turn delivery (buffered blob tagged with the request it answers).

**Out of scope / explicit policy:**
- **Same-chat true parallelism** — deliberately cut (user decision). Within a chat, turns are serialized; parallelism is cross-chat only.
- **Crash-durability of in-flight interactive turns** — **stated policy:** interactive turns are in-memory and NOT crash-durable; a process crash drops in-flight turns silently and the next user message starts fresh. (Durable goals via D1 remain a separate, opt-in path.)
- **Token streaming** — delivery stays buffered (one blob per turn). This is load-bearing: it shrinks the cross-delivery surface (correlate once at the blob, not per token). Token streaming is a separate future concern.
- **Supersede as the correction primitive** — the user chose live-steer; supersede is reachable indirectly via the turn-veto path (an incoherent steer → queued-new) but is not the default.

---

## 3. Architecture overview (three phases)

Each phase ships value on its own; **P1 alone removes the blocking.** Murat's caution is honored: **the concurrency-correctness risk concentrates where live dispatch + steering go live (P2/P3), so those phases gate behind the §9 invariant tests.**

- **P1 — Foundation:** request-id streams, `TurnRegistry`, non-blocking in-chat intake + FIFO queue, cross-session parallel, atomic tagged delivery, Telegram target fix, per-owl persona/memory serialization, clarify-pending folded into the registry, concurrency caps.
- **P2 — Live-steer infrastructure:** steering mailbox; the ReAct loop drains+folds at the `on_iteration_complete` boundary; the lost-steer CAS guard; cooperative stop shielded against the D1 ledger; bounded+coalesced mailbox; teardown drain.
- **P3 — Hybrid arrival policy:** explicit-signal parser; conservative classifier + turn-veto; fail-safe to queued-new.

---

## 4. Phase 1 — Foundation

### 4.1 Re-key the response stream by request-id
`trace_id` becomes the load-bearing **request_id**. Three coupled edits (Amelia — it is *not* one dict rename):
- Thread `trace_id` onto the **inbound message envelope** (the message DTO the adapters produce) — verify it is on the message object, not only a log field. Mint sites: `cli_adapter.py:141`, telegram `adapter.py:535`. **Assert uniqueness + non-empty at mint** (Winston — a colliding/empty request_id reintroduces cross-delivery one layer up).
- `StreamRegistry` (`pipeline/streaming.py`): `_writers` keyed by `request_id`; `create(request_id)` / `get_writer(request_id)`.
- `deliver.py:42`: `registry.get_writer(state.trace_id)`. Pick **one** source of truth for `trace_id` at deliver — `state.trace_id` — and assert `state.trace_id == TraceContext.current().trace_id` in a test so they cannot silently diverge. Confirm `AsyncioBackend.run` writes `trace_id` onto the `PipelineState`, not only the ContextVar.
- Delete `ClarifyPump.serialize_prior` and retire/re-key `_inflight` (grep for any teardown reader first).

### 4.2 `TurnRegistry` (new — `gateway/turn_registry.py`)
In-memory, per session. Holds:
- `running: Turn | None` — at most one RUNNING turn per session.
- `queue: deque[PendingIntake]` — FIFO of accepted-but-not-started messages.
- `Turn = {turn_id (=request_id), session_id, task: asyncio.Task, status, steering_mailbox: asyncio.Queue(maxsize=N), stop_requested: bool, target (channel reply target, e.g. chat_id), original_input, started_at}`.
- `status: RUNNING → FINALIZING → DONE` (one-way; the CAS in §5.2).
- `clarify_pending` is a turn status, not a private `ClarifyPump` flag (Winston — "is this the answer to my question?" *is* the arrival decision; move the state where the decision is made).

**Lifecycle (Winston):** the turn's **own task self-deregisters in a `finally`** (covers normal completion, exception, stop) — this is primary cleanup, never the happy path alone. A **sweeper is a backstop only**: it reaps entries whose `task.done()` is true but status wasn't updated (lost the `finally` race) and entries past a hard wall-clock TTL with no terminal status. The sweeper must **snapshot keys then act** (never iterate-and-mutate the registry — `dict changed size`).

### 4.3 Non-blocking in-chat intake + queue drain
The gateway loop (`orchestrator.py` CLI ~740-832, Telegram ~903-992) changes from "serialize then dispatch" to "accept → decide (§6) → act":
- **No running turn for this session** → mint request_id, create stream slot, register `Turn`, dispatch `backend.run` task, spawn send keyed by request_id.
- **Running turn exists** → §6 decides STEER / STOP / QUEUE. QUEUE appends to `registry.queue`; an **instant ack** is emitted ("queued — I'll start that next") so the user is never met with silence.
- **On turn completion**, the turn's teardown pops the next `PendingIntake` from `queue` (if any) and dispatches it. This is the in-chat serialization: one running turn, FIFO drain — *non-blocking at intake, serialized at execution.*

### 4.4 Cross-session parallelism
Different `session_id`s are independent: separate request-id stream slots, separate `TurnRegistry` entries, separate per-session history. They run genuinely in parallel with zero shared mutable state *except* per-owl persona/memory (§4.6). This is the parallelism the request-id re-key buys for free.

### 4.5 Atomic tagged delivery + Telegram target fix
- **Presentation (Winston):** delivery stays a **buffered blob**, tagged with the request_id it answers, **emitted atomically** — never let two turns' chunks interleave into one channel view.
- **Telegram cross-delivery fix (Amelia — necessary, the re-key alone does NOT fix it):** the decoupled send still reads the global `_last_chat_id` (`adapter.py:148,539`). Capture the origin chat_id **per message** at `_handle_update` (before `put_nowait`), carry it on `Turn.target` (and/or a `ResponseChunk.target` field), and have the Telegram send loop resolve `target = registry.get(chunk.request_id).target` — `send_text(text, chat_id=target)` with an explicit chat_id param (default `_last_chat_id` for back-compat). Kill the always-`_last_chat_id` send.
- **Heartbeat (Winston):** model a proactive heartbeat message as a `Turn` (it *is* a turn, just not user-initiated) so it routes through the same delivery + registry path — no side door that races delivery.
- **Stream-miss is a hard drop + log (Murat):** a `ResponseChunk` whose request_id is not registered (a late chunk after slot cleanup) is **discarded loudly, never rerouted to a default** (the response-side mirror of no-hidden-errors). Slot removal is idempotent and happens only after the final blob is handed to the channel.

### 4.6 Per-owl persona/memory serialization (Winston's #1 hole)
Cross-session concurrency introduces a real race: the **same owl** can run in two chats at once, and **DNA-evolution + memory-promotion are read-modify-write on shared, value-bearing state** (the max-delta envelope assumes serial application; concurrent promotion races dedup). The per-chat serialization does NOT cover this (it's cross-session). Guard: **serialize DNA-evolution and memory-promotion per-owl** (a per-owl async lock or a single-consumer queue), and move them **off the turn's critical path** (enqueue, drain serially). This is nondeterministic corruption of the product's core asset if missed — it passes every test and fails in the field.

### 4.7 Concurrency caps (Winston)
- **Per session:** naturally 1 running turn (in-chat serialized) + a bounded intake queue (overflow policy: coalesce/supersede the oldest queued, or reject-with-notice past a hard cap — never unbounded-queue).
- **Global (across sessions):** a cap sized from the host **capability probe**, not Jetson limits (per the all-hardware rule), so one chatty session can't starve others or fall the box over. Overflow → bounded wait, loudly observable.

---

## 5. Phase 2 — Live-steer infrastructure

### 5.1 The mailbox drain + splice contract (Amelia)
- Each `Turn` has a bounded `steering_mailbox: asyncio.Queue` (single-event-loop, cross-task-safe; no extra lock on the queue).
- The ReAct loop reaches its **own** mailbox via `TurnRegistry[TraceContext.current().trace_id].steering_mailbox` inside the **`on_iteration_complete` callback closure** (the same boundary D1 checkpoints at — zero new provider plumbing for the drain). The execute step builds that closure.
- **Drain-to-empty with `get_nowait()` in a loop — NEVER `await get()`** (an await would block the iteration boundary forever when there is no steering — the single likeliest P2 bug).
- **Splice contract:** the callback **returns** `list[Message]` (the folded `[steering]` messages) and each provider's `complete_with_tools` does `messages.extend(returned)` before the next LLM call — **2 provider edits, one line each.** Verify the provider does not defensively copy `messages` (else the fold is silently lost). Do not claim zero provider edits.

### 5.2 Lost-steer guard — the CAS invariant (Murat / Winston TOCTOU)
A steer must never land in a dead mailbox. The invariant: **a steer is either accepted by a still-RUNNING turn, or converted to a queued-new turn — never enqueued onto a turn past its finalization line.** Mechanism, both halves required:
- **Status CAS under a per-turn (or per-session) lock:** `RUNNING → FINALIZING → DONE`, one-way.
- **Loop side:** the terminal sequence is *take lock → re-check mailbox one last time under the lock → if non-empty, release and loop again (do not finalize with pending steers) → if empty, set FINALIZING, release.*
- **Router side:** the enqueue is a guarded transaction — *take lock → read status → RUNNING: `put` + return STEER → FINALIZING/DONE: return NEW (the steer becomes a queued turn).* The status read and the put are atomic.
- **Teardown:** on turn teardown, **drain the mailbox and re-route survivors as queued-new turns** (a discarded steer is a lost user instruction — convert, don't GC).
- This unifies with the fail-safe (§6): *an undeliverable steer takes the same path as classifier-uncertainty — queued-new.* One fallback path, not two.

### 5.3 Cooperative stop, shielded against the D1 ledger (Murat's landmine)
- Stop is a **flag (`stop_requested`), NOT `task.cancel()`.** `task.cancel()` raises `CancelledError` at the next await — almost always mid-tool — and a cancel between `ledger.begin()` and `ledger.commit()` leaves a begun-not-committed durable op that **D1 startup recovery REPLAYS for exactly-once** — so 'stop' would produce the *opposite* of stopping (the side effect replays on next boot).
- **Durable side-effecting ops are uninterruptible until their ledger boundary:** wrap the `begin → commit` critical section in `asyncio.shield` so cancellation cannot tear it. Stop is **deferred-and-remembered** — the flag is honored at the next iteration boundary, after the current tool batch has committed. shell / side-effecting writes = uninterruptible-until-ledger-boundary, *period* (a `shell` that already ran `rm` cannot be aborted).
- Consequence (documented, not a bug): a stop cannot interrupt a 90-second in-flight tool — stop is **cooperative at iteration granularity**, bounded-latency by construction, not instant. The stop point writes a "stopped" chunk and finalizes gracefully.

### 5.4 Bounded mailbox + coalesce (Murat)
The mailbox is bounded; if a user spams N steers at a slow turn, the loop **coalesces** — folds the latest (or a merged summary), not all N (which would blow the context window). Backpressure: a full mailbox supersedes the oldest pending steer.

### 5.5 The coherence caveat (Dr. Quinn) + the turn-veto mitigation
Live-steer on a buffered turn can produce an incoherent blend when the steer *contradicts* the in-flight goal ("no, I meant Y"). Mitigation built into §6: the **target turn can veto** an incoherent steer (its own LLM judges "this doesn't fit what I'm doing"); a vetoed steer falls back to a **queued-new turn** — which is effectively supersede-with-fresh-context, delivering the coherent answer. Live-steer is therefore best for *additions* ("also include Z"); *contradictions* gracefully degrade to queued-new via the veto.

---

## 6. Phase 3 — Hybrid arrival policy (`gateway/turn_router.py`)

Runs only when a running turn exists for the session (idle sessions skip it entirely — zero added latency to the common case). Decision order:

1. **Explicit user signal (deterministic, highest priority):** `stop`/cancel → STOP (§5.3). `/steer` (or reply-to-the-in-flight-message in Telegram) → STEER. `/new` → queued-new. A pending **clarify question** answered → the existing `ClarifyIntentClassifier` ANSWER path (now read from the Turn's `clarify_pending` status).
2. **No explicit signal → conservative classifier:** generalize `ClarifyIntentClassifier` into a one-token verdict over (new message + the running turn's original ask). **STEER only at HIGH confidence**; everything uncertain → queued-new (Murat/Dr. Quinn — false-STEER poisons a turn *and* loses the new ask, invisibly; false-NEW gives a recoverable visible second answer; the asymmetry mandates conservatism toward STEER).
3. **Turn-veto (two-stage, reuse the D3 pattern):** a proposed STEER is offered to the running turn, which may **veto** if the steer is incoherent with its in-flight goal → falls back to queued-new (§5.5).
4. **Fail-safe:** classifier error / uncertainty / undeliverable steer (§5.2) → **queued-new**. Never block, never mis-steer someone else's work, always loudly logged.

STEER → guarded enqueue on the running turn's mailbox (§5.2). Queued-new → `registry.queue.append` (§4.3) + instant ack.

---

## 7. Data flow (unified)

```
message arrives (adapter stamps request_id + captures target/chat_id)
  → gateway loop: running turn for this session?
      NO  → mint slot(request_id) + register Turn + dispatch backend.run task + spawn send(request_id)
      YES → TurnRouter (§6): explicit signal? → STOP | STEER | /new
                              else conservative classifier (high-conf STEER + turn-veto) else queued-new
              STEER → guarded enqueue on running turn's mailbox (CAS, §5.2)
              STOP  → set stop_requested (honored at next iteration boundary, ledger-shielded)
              QUEUE → registry.queue.append + instant ack
  → running ReAct loop: at on_iteration_complete → drain mailbox (get_nowait loop) → fold [steering] via callback-returns-messages → check stop_requested
  → turn completes → deliver atomic tagged blob to request_id stream → channel sends to Turn.target
  → turn teardown (finally): drain mailbox → re-route survivors as queued-new → deregister Turn → pop next queued intake → dispatch
cross-session: different session_id ⇒ fully parallel, own slot/history (per-owl evolution/promotion serialized, §4.6)
```

---

## 8. Error handling & the fail-safe invariants

- **Undeliverable steer → queued-new** (§5.2) — single unified fallback with classifier-uncertainty.
- **Cancel never tears a durable op** — ledger-shielded; stop deferred to the iteration boundary (§5.3).
- **Commit critical section is pure-append, zero re-entrant awaits** — embedding computed *outside* any lock (Murat — a slow embed under a lock is a throughput cliff masquerading as a hang). (Within a chat there are no concurrent commits; the per-owl promotion serialization §4.6 must still keep its critical section await-free to avoid cross-session stalls.)
- **Stream-miss → hard drop + log**, never reroute (§4.5).
- **Turn task always reaches a terminal status in `finally`** (§4.2); sweeper backstop snapshots-then-acts.
- **Bounded mailbox + coalesce**; teardown drains and re-routes (§5.4).
- **request_id unique + non-empty at mint** (§4.1).
- **Fail-safe everywhere is toward queued-new** (never STEER on doubt, never block).

---

## 9. Testing

**Murat's four invariants — each a test, and P2/P3's live paths gate behind them:**
1. **Steer-acceptance atomic with status:** hammer steer-vs-finish with a controllable mailbox-check barrier; assert **zero lost steers** across many randomized interleavings (an undeliverable steer always becomes a queued-new turn).
2. **Durable op uninterruptible:** inject stop mid-side-effecting-op; assert the ledger is fully committed or fully aborted — never torn — and D1 recovery does not replay a stopped op.
3. **Commit/promotion critical section await-free + no deadlock:** a turn holding the per-owl promotion lock while another session's turn runs must not deadlock; assert hold-time bounded + independent of embed latency.
4. **STEER requires high confidence; uncertain → queued-new; turn can veto:** feed ambiguous corrections; assert false-STEER (poisoning) rate ≈ 0 even at the cost of higher false-NEW.

**Phase tests:**
- P1: two **cross-session** turns run truly in parallel + each reply correlates to its request_id; in-chat: a mid-turn message is accepted instantly (non-blocking) and queued, runs after; Telegram per-message chat_id (no cross-deliver); `state.trace_id` populated at deliver; provider folds the callback's returned messages (not a copy); per-owl evolution serialized (two sessions, same owl, no lost-update).
- P2: a `[steering]` ADD folded mid-turn is reflected in the running turn's output; `stop` finalizes gracefully at the next boundary; coalesce under steer-spam; teardown re-routes a late steer to queued-new.
- P3: explicit `/steer`/`/new`/`stop` deterministic; conservative classifier high-conf-STEER + turn-veto; uncertainty/error → queued-new; idle session skips the classifier.

**Gateway journey (the merge-gate):** user sends "research X"; mid-turn sends "also include Y" (ADD → steers the running research turn, output includes Y) and, from a **second chat**, "what's the weather" (runs truly in parallel, separate correlated reply); then sends "no, I meant Z" (contradiction → conservative classifier or turn-veto → queued-new, coherent fresh answer). Real channel adapters + gateway, mocking only the AI provider; assert outcomes (correlation, no cross-deliver, steer-applied, parallel-cross-chat, contradiction-degrades-coherently).

---

## 10. Load-bearing invariants (sign-off summary)

1. **One running turn per chat + FIFO queue; non-blocking intake.** True parallelism is cross-session only (§4.3/§4.4). Deletes the concurrent-writers-to-one-history race by construction.
2. **request_id is the routing identity.** Streams re-keyed by request_id; unique+non-empty at mint; `serialize_prior` deleted (§4.1).
3. **Steer-acceptance is atomic with turn status; an undeliverable steer becomes a queued-new turn** (§5.2) — unified with all fail-safes.
4. **Cancel never tears a durable op; stop is deferred-and-remembered, ledger-shielded** (§5.3).
5. **Per-owl DNA-evolution + memory-promotion serialized, off the critical path** (§4.6) — the cross-session shared-state guard.
6. **Hybrid relatedness: explicit signal > conservative high-confidence classifier + turn-veto > queued-new; fail-safe toward queued-new** (§6).
7. **In-flight interactive turns are not crash-durable — stated policy** (§2).

---

## 11. Cuts & backlog
- **Same-chat true parallelism** — cut by decision (§2).
- **Token streaming** — cut; buffered atomic delivery is load-bearing (§2/§4.5). Revisit if a streaming UX is wanted (would re-open per-token cross-delivery).
- **Supersede as a first-class correction primitive** — reachable via the turn-veto path; not a separate mechanism.
- **Crash-durable interactive turns** — explicit non-goal; durable goals via D1 remain the opt-in path.
- **Reply-threading UX richness** (Telegram reply-to as a steer signal) — ship the basic signal; richer threading is future.

---

## 11a. Planning recon (must run before the plan freezes)

Several exact seams were not pinned during the design recon and **must be located by a code-recon task at the start of planning** (Amelia flagged the consolidate file was never cited): (1) the **consolidate / `_persist_turn`** step and the **memory-promotion (`FactPromoter`)** + **DNA-evolution (`EvolutionCoordinator`)** modules + their current concurrency assumptions (for §4.6 per-owl serialization); (2) the exact **`on_iteration_complete` callback construction site** in the execute step and whether the providers copy `messages` (for §5.1 the splice contract); (3) whether the **inbound message DTO** already carries `trace_id` or it must be added (§4.1); (4) the **D1 ledger `begin → commit` boundary** to `asyncio.shield` (§5.3); (5) the **heartbeat** producer path (§4.5). The plan's tasks reference these by their recon-confirmed paths, not guesses.

## 12. File map (responsibilities)

| File | Change | Phase |
|---|---|---|
| message DTO + `cli_adapter.py`/`telegram/adapter.py` | stamp `trace_id`(request_id) on the inbound envelope; assert unique+non-empty; capture per-message `target`/chat_id | P1 |
| `pipeline/streaming.py` | `_writers` keyed by request_id; `create`/`get_writer` sig; `ResponseChunk.target`; stream-miss hard-drop | P1 |
| `pipeline/steps/deliver.py` | `get_writer(state.trace_id)`; atomic tagged blob | P1 |
| `pipeline/backends/asyncio_backend.py` | ensure `state.trace_id` populated == TraceContext | P1 |
| `gateway/clarify_pump.py` | delete `serialize_prior`; move clarify-pending into TurnRegistry | P1 |
| `gateway/turn_registry.py` | **create** — Turn, running+queue, CAS status, mailbox, target, finally-deregister, sweeper | P1/P2 |
| `startup/orchestrator.py` (both loops) | accept → route → dispatch/queue; non-blocking intake; queue-drain on completion | P1/P3 |
| `channels/telegram/adapter.py` | `send_text` explicit chat_id; resolve target by request_id; kill `_last_chat_id` send | P1 |
| owls evolution + memory promotion (per `project_v2_*` modules) | per-owl serialization, off critical path, await-free critical section | P1 |
| heartbeat (`heartbeat/`) | model proactive message as a Turn | P1 |
| the execute step + `on_iteration_complete` closure | drain mailbox (get_nowait loop), return folded `[steering]` messages, check `stop_requested` | P2 |
| openai + anthropic providers (`complete_with_tools`) | `messages.extend(callback_returned)` (verify no defensive copy) | P2 |
| D1 ledger op boundary | `asyncio.shield` the begin→commit critical section | P2 |
| `gateway/turn_router.py` | **create** — explicit-signal parser; conservative classifier (generalize `ClarifyIntentClassifier`); turn-veto; fail-safe queued-new | P3 |

# Implementation Design — Durable ReAct Execution

**Status:** Design + sequenced plan (no production code in this artifact).
**Scope:** Make each ReAct iteration a durable, checkpointed step; route every
side-effecting tool call through the already-built `SideEffectLedger` for
exactly-once; resume the loop from the last checkpoint after a crash without
re-running committed side-effects.
**Decision being designed to (fixed):** One ReAct iteration = one LLM round +
the tool calls it triggers = ONE durable checkpointed step. Checkpoint pipeline
state after each iteration. On resume, re-enter the ReAct loop from the last
checkpoint; committed side-effects are not re-run.

All file:line references are against the repo at design time.

---

## 1. Inventory (grounded in code)

### 1.1 The ReAct loop — where iterations happen

The loop is **inside the provider**, not in a pipeline step. `execute.run`
(`src/stackowl/pipeline/steps/execute.py:452`) resolves a provider
(`_select_tool_provider`, line 349) and, when tools exist, calls
`_run_with_tools` (line 57), which builds the tool schemas + a closure
`_dispatch` (line 109) and makes a **single** call to
`provider.complete_with_tools(...)` (line 257 / 266). That one call internally
runs the entire multi-iteration ReAct loop and returns `(final_text, raw_calls)`
(line 257). So from the pipeline's perspective the whole agentic loop is one
opaque await inside the `execute` node.

The actual iteration structure lives in each provider's `complete_with_tools`:

- Anthropic: `src/stackowl/providers/anthropic_provider.py:118`. The loop is
  `for _ in range(resolved_iterations):` at line 182. Each iteration:
  1. `messages = trim_messages_to_budget(...)` (line 186);
  2. `await self._client.messages.create(...)` — the LLM round (line 189);
  3. if `response.stop_reason != "tool_use"` → run `_enforce` persistence check
     (line 213) then `return text, all_calls` (line 221) — terminal;
  4. else append the assistant turn (line 230), then `for b in response.content`
     dispatch each `tool_use` block via `await tool_dispatcher(b.name, ...)`
     (line 238), append `tool_result` blocks as a user turn (line 252);
  5. `LoopGuard` observes each call (line 249); `guard.tripped()` breaks early
     (line 253).
- OpenAI: `src/stackowl/providers/openai_provider.py:126`, loop at line 201,
  same shape plus a text-protocol ReAct fallback (`parse_react_action`,
  line 229) for models without native tool-calls.
- Base default: `src/stackowl/providers/base.py:171` — no loop, single
  `complete()`; mock/weak providers ride this.

**Bound:** `max_iterations: int = 8` (base.py:177; anthropic:124; openai:127),
overridden by `self._config.tool_max_iterations` (anthropic:130; openai:135).
A `LoopGuard` (`src/stackowl/providers/_react.py:49`) detects identical
`(name,args)` repeats: `warn_at=3` injects a directive, `break_at=4` trips and
breaks the loop to a graceful wrap-up (anthropic:253; openai:253). At max-out
the Anthropic provider makes one tool-less wrap-up call so the user always gets
an answer (anthropic:267–298).

**Key consequence for the design:** "one ReAct iteration" is a concept that
exists *inside the provider's for-loop*, where the pipeline checkpoint machinery
(LangGraph) has no visibility. The per-iteration durable seam therefore must be
introduced **at the provider loop body**, with a callback the provider invokes
once per completed iteration. The pipeline/LangGraph node boundary is too coarse
(it brackets the entire loop).

### 1.2 Tool dispatch — where the ledger intercepts

The single chokepoint is the `_dispatch(name, args)` closure in
`_run_with_tools` (`src/stackowl/pipeline/steps/execute.py:109`). Both providers
call it as `tool_dispatcher` (anthropic:238; openai:234). Inside it, in order:

1. depth>0 fork-bomb refusal (line 114);
2. `t = tool_registry.get(name)` (line 124), unknown-tool guard (line 125);
3. `denied_this_run` short-circuit (line 128);
4. **consent gate** `await gate.check(t, ...)` (line 148) — `is_consequential`
   from `t.manifest.action_severity == "consequential"` (line 142);
5. **`tr = await t(**args)`** — the actual execution (line 178);
6. heuristic post-hook (line 183);
7. returns `tr.output` on success, or `TOOL_FAILED_MARKER + error` on failure
   (line 206).

This `_dispatch` seam is **exactly** where the `SideEffectLedger` belongs: it
already has `name`, the validated `args`, the resolved `Tool` `t`, and therefore
`t.manifest.action_severity` (`src/stackowl/tools/base.py:33`,
`Literal["read","write","consequential"]`). `is_side_effecting(severity)`
(`ledger.py:81`) decides guard-vs-replay; `read` is replay-safe, `write` /
`consequential` are ledger-guarded. The ledger wrap goes **between step 4
(consent) and step 5 (execute)**: consult `ledger.begin(...)`; on
`already_committed` return the recorded result and skip `await t(**args)`; on
`proceed` execute once then `ledger.commit(...)`; on `uncertain` surface (park).

### 1.3 Pipeline state, checkpoint, and task_id threading

`PipelineState` (`src/stackowl/pipeline/state.py:23`, `frozen=True`) carries
`task_id: str | None` (line 57), `history: tuple[Message, ...]` (line 69),
`tool_calls: tuple[ToolCall, ...]` (line 63), `responses` (line 62),
`system_prompt` (line 73), and mutates only via `evolve()` (line 81).

The LangGraph backend (`src/stackowl/pipeline/backends/langgraph_backend.py`)
wraps `PipelineState` in a `_LGState` TypedDict (line 58) and runs the 8 canonical
steps as graph nodes (`_build_graph_builder`, line 192). **The whole ReAct loop
lives inside the single `execute` node** (`_wrap_step("execute", …)`, the node
awaits `execute.run` which awaits the provider loop). So a LangGraph "super-step"
checkpoint is written once per *pipeline step* (per node), i.e. **once for the
entire ReAct loop**, NOT per iteration. The checkpointer is LangGraph's
`AsyncSqliteSaver` (line 46/283) keyed by `thread_id`.

`task_id` is *already* threaded into the checkpoint key:
`thread_id = f"{state.session_id}::{state.task_id}" if state.task_id else
state.session_id` (line 118–122), passed as `config["configurable"]["thread_id"]`
(line 124). So a durable task already gets an isolated LangGraph checkpoint
thread — but that checkpoint only captures the pipeline at node boundaries, which
is too coarse for per-iteration durability.

**Conclusion (the hard architectural call):** The LangGraph checkpointer cannot
express per-iteration granularity because the iterations happen *below* the node.
Per-iteration durability must be a **finer custom seam**: a per-iteration
checkpoint of the ReAct working set (messages array + completed tool calls +
iteration counter) written to the durable `tasks` row / a sidecar, driven by a
callback the provider calls at the end of each iteration. The existing LangGraph
checkpointer is **retained unchanged** for the coarse pipeline-node resume; the
new fine seam handles the inner loop. (See §2 and Risk R2 for the recommendation
and the rejected alternative.)

### 1.4 Durable primitives already built (REUSE — do not redesign)

- `DurableTask` (`src/stackowl/pipeline/durable/task.py:29`): `task_id`,
  `owner_id`, `goal`, `status` (`TaskStatus`, line 26:
  pending/running/parked/completed/failed), `current_step: int = 0` (line 36),
  `thread_id`, `result`, timestamps.
- `DurableTaskStore` (`store.py:30`, owner-scoped): `create` (line 38),
  `get` (line 69), `list` (line 97), `update_status(task_id, status, *,
  current_step=, thread_id=, result=)` (line 117) — the checkpoint primitive.
- `SideEffectLedger` (`ledger.py:92`): `begin(task_id, step_index, tool_name,
  args) -> LedgerDecision` (line 134) and `commit(...)` (line 197).
  `idempotency_key = sha256(task_id ∥ step_index ∥ tool_name ∥ canonical-json
  args)` (line 64); owner folded into the stored key via `_owned_key` (line 117).
  Outcomes: `proceed` / `already_committed` (carries `result`) / `uncertain`
  (intent without commit) (line 48–61).
- `DurableExecutor` (`executor.py:104`) over an abstract `TaskStep`
  (`executor.py:40`): `start` (line 120), `resume` (line 158, drives from
  `task.current_step` with ledger semantics), `recover` (line 190), `_drive`
  (line 244, the checkpoint loop: per step → `update_status(current_step=
  next_step, result=running_aggregate)`, line 308), `_run_side_effecting`
  (line 330, the begin/commit/park contract).
- Migration 0045 (`src/stackowl/db/migrations/0045_durable_tasks.sql`): `tasks`
  (PK `(owner_id, task_id)`, line 36) and `side_effect_ledger` (PK
  `idempotency_key`, line 42).
- `Tool.manifest.action_severity` (`tools/base.py:33`); dispatch already reads it
  at `execute.py:142`.

### 1.5 Task assignment entry point (FR13)

A user message reaches an owl + the pipeline via the gateway loop in
`src/stackowl/startup/orchestrator.py`. For the CLI channel: `scanner.scan(msg)`
(line 738) → build `PipelineState(... interactive=True ...)` (line 778) →
`asyncio.create_task(backend.run(state))` (line 787). The Telegram path is the
mirror image (lines 901/942/951). `task_id` is **never set** on these
interactive turns today (default `None`), so they run ephemerally with no durable
record.

The non-interactive *goal* path already exists:
`GoalExecutionHandler.execute` (`src/stackowl/scheduler/handlers/goal_execution.py:44`)
builds `PipelineState(... interactive=False ...)` (line 122) from a scheduled
goal and calls `self._backend.run(state)` (line 146). This is the natural
"unattended owl-directed goal" surface — but it also does **not** set `task_id`
and does not create a `DurableTask`.

Startup already calls durable **recovery**:
`startup/orchestrator.py:1003–1012` constructs `DurableExecutor(db_pool)` and
awaits `.recover()` (fail-open). But `recover()` today can only **park** orphans
because it has no in-memory step definitions (`executor.py:190–242`, documented
limitation at lines 200–208). This design closes that gap: once a ReAct task's
"steps" are *the loop itself*, recovery can re-drive by re-entering the loop.

**Assignment trigger sub-decision (flagged, not assumed):** the simplest viable
trigger is **owl-directed goal = durable; interactive chat = ephemeral**. i.e.
`GoalExecutionHandler` (and a future explicit "give the owl this goal" surface)
creates a `DurableTask` and sets `state.task_id`; ordinary CLI/Telegram chat
turns stay ephemeral (`task_id=None`, exact current behavior). This is the
recommended Phase-1 boundary because it is purely additive and touches one
handler. See Open Decision OD1 for alternatives.

### 1.6 Journey-test harness

`tests/journeys/` drive the real stack and **mock only the AI provider**. Pattern
(`test_j6_clarify_pause_resume.py`): a `_FakeBot` transport (line 111), a
`_ScriptedSecretary` provider whose `complete_with_tools` calls the *real*
`tool_dispatcher` (line 154–180), a `_FakeProviderRegistry` (line 196), and
`StepServices` wiring the **real** `ToolRegistry`, consent gate, owl registry,
etc. (line 248). `TestModeGuard._active=False` enables live I/O (line 222). The
test starts a turn as `asyncio.create_task(backend.run(state))` (line 304), polls
with `_wait_until` (line 278), and asserts **business outcomes** (question
delivered, turn suspended, answer threaded) — not tool return shapes.

A J-DURABLE test reuses this exact scaffold: a scripted provider that emits a
known multi-iteration plan (iter 1: a `read` tool; iter 2: a `write`/consequential
tool that is the side-effect; iter 3: final answer). The "kill" is **simulated
restart**, not SIGKILL: cancel the `run` task (or raise inside the second
iteration's dispatch) *after* the side-effect committed but *before* the loop
returned, then build a fresh backend/executor over the **same DB** and call
`resume(task_id)`. Assert at the user-outcome level: the side-effecting tool's
real side effect happened **exactly once** (probe the real target, e.g. a temp
file written once / a fake-bot message sent once), and the resumed run produced a
coherent final answer.

---

## 2. The design

### 2.1 Integration approach — iteration ↔ checkpoint ↔ ledger

Introduce a thin **`DurableReActContext`** object, created in `execute._run_with_tools`
when `state.task_id is not None`, and threaded into the provider loop via two
new optional callbacks on `complete_with_tools` (additive kwargs, default `None`
→ exact current behavior for every ephemeral turn and every existing test):

1. **`ledger_guard`** — wraps tool execution inside `_dispatch`. Already-present
   `name`, `args`, `t.manifest.action_severity` make this a local change at the
   `tr = await t(**args)` seam (`execute.py:178`). Logic:
   ```
   if task_id is None or not is_side_effecting(t.manifest.action_severity):
       tr = await t(**args)                      # read / ephemeral: run, no ledger
   else:
       step_index = ctx.current_iteration        # see idempotency-key stability §2.4
       decision = await ledger.begin(task_id, step_index, name, args)
       if decision.outcome == "already_committed":
           return decision.result                # replay — DO NOT re-execute
       if decision.outcome == "uncertain":
           ctx.mark_uncertain(name); ... (park signal — see §2.3)
       tr = await t(**args)                      # proceed: run exactly once
       await ledger.commit(task_id, step_index, name, str(tr.output), args)
   ```
   This is the **only** place a side-effecting tool runs, so exactly-once is
   total. Consent still runs *before* the ledger (so a not-yet-approved
   consequential action never even opens an intent row).

2. **`on_iteration_complete(iteration_index, messages_snapshot, calls_snapshot)`**
   — the provider calls this once at the **bottom of each for-loop iteration**
   (anthropic: after line 261; openai: after the dispatch/observe block), passing
   the current `messages` array and `all_calls`. The callback:
   - bumps `ctx.current_iteration += 1`;
   - serializes the ReAct working set (messages + all_calls + iteration counter)
     and persists it via the durable store as the **per-iteration checkpoint**;
   - this is the resume cursor that lets the loop re-enter at the right iteration.

**Where the per-iteration checkpoint is written — RECOMMENDATION:** a **custom
per-iteration write to the durable `tasks` row** (via
`DurableTaskStore.update_status(task_id, "running", current_step=iteration)`),
plus the serialized ReAct working set persisted as the task `result` blob (or a
small sidecar column/table — see Sub-story 1). **Reuse the existing LangGraph
checkpointer unchanged** for the coarse pipeline resume, but do **not** try to
key per-iteration state into it.

*Justification:* the LangGraph checkpointer only snapshots at node boundaries
(`langgraph_backend.py` nodes), and the loop is *inside* the `execute` node
(§1.3). Bending LangGraph to checkpoint mid-node would mean splitting the ReAct
loop across N dynamically-created LangGraph nodes (one per iteration) — a large,
brittle re-architecture of the graph with an unbounded fan-out and a recursion
limit interaction (`recursion_limit=50`, line 127). The custom seam reuses the
**already-built, already-tested** durable primitives (`DurableTaskStore`,
`SideEffectLedger`) exactly as the `DurableExecutor._drive` loop already uses
them (`executor.py:308`), keeping one durability mechanism, owner-scoped, with
no new infra. The ledger already guarantees side-effect exactly-once regardless
of checkpoint granularity, so the per-iteration checkpoint only needs to restore
*context* (messages), not re-prove side-effect safety.

### 2.2 Resume semantics

On restart, a durable ReAct task is re-driven by a **`ReActTaskStep`** — a
concrete `TaskStep` (`executor.py:40`) whose `run()` re-enters
`execute._run_with_tools` for that task, seeded from the checkpoint:

1. Load `DurableTask` (`store.get`, `store.py:69`); read `current_step` (the last
   completed iteration) and the serialized ReAct working set.
2. Rebuild `PipelineState` with `task_id` set and `history` / messages restored
   from the checkpoint blob (so the LLM sees the same conversation + prior
   observations it had pre-crash).
3. Re-enter the provider loop. `ctx.current_iteration` is seeded to the persisted
   value so the **first new iteration's `step_index` matches the pre-crash
   numbering** (§2.4).
4. The LLM, given the restored context, **re-proposes its next tool call**. If
   that call's `(task_id, step_index, tool_name, args)` was already committed,
   `ledger.begin` returns `already_committed` and `_dispatch` returns the recorded
   result **without re-executing** — the side effect is not repeated. If the
   crash happened *between* `begin` (intent written) and `commit`, the ledger
   returns `uncertain` → park for human review (never blind re-run of a possibly
   half-done effect), matching `executor._run_side_effecting` (`executor.py:357`).
5. The loop continues to its natural terminal (final answer / max-iterations
   wrap-up), then the task is marked `completed` with the final answer as
   `result`.

The crucial property: **the LLM re-deriving the same plan from the same restored
context produces the same `(tool_name, args)`, which produces the same
idempotency key**, which is what makes the ledger's exactly-once fire on resume.
§2.4 makes the *step_index* component of that key stable; §2.5/R3 covers the case
where the LLM does NOT re-derive identically.

### 2.3 Task lifecycle + trigger + recover() integration

**Creation (FR13):** in the recommended Phase-1 boundary,
`GoalExecutionHandler.execute` (`goal_execution.py:122`) — before building
`PipelineState` — calls `DurableTaskStore.create(DurableTask(status="running",
current_step=0, goal=...))`, then sets `state = state.evolve(task_id=task.task_id)`.
Interactive chat stays ephemeral (`task_id=None`). A future explicit
"assign goal to owl" command/tool routes through the same creation path.

**Status transitions:**
`pending`→`running` on first drive; `running`→`running` on every iteration
checkpoint (`update_status(current_step=i)`); `running`→`parked` on an
`uncertain` ledger outcome or on `recover()` of an orphan that needs re-drive;
`parked`→`running` on `resume()`; `running`→`completed` on terminal answer;
`running`/any→`failed` on an unrecoverable exception (mirrors `executor._drive`
fail path, `executor.py:300`).

**recover() integration (closes the parked-orphans gap):** today `recover()`
(`executor.py:190`) can only park orphaned `running` tasks because it has no step
definitions (documented limitation, lines 200–208). With this design **there IS a
step definition for a ReAct task — re-entering the loop** (`ReActTaskStep`).
Wiring: the startup recovery call (`startup/orchestrator.py:1003`) is upgraded so
that for each orphaned `running`/`parked` ReAct task it constructs the
`ReActTaskStep` (which needs the live `StepServices`/backend — available at
startup) and calls `executor.resume(task_id, [step])`. Because `resume` drives
through the ledger (`executor.py:158`→`_drive`→`_run_side_effecting`), committed
side-effects are skipped and the loop continues exactly-once. The pure
park-only `recover()` remains the fail-safe for tasks whose step definitions
are genuinely unavailable.

### 2.4 Idempotency-key step_index stability (the hard part)

The ledger key folds in `step_index` (`ledger.py:64`). For exactly-once across
replays the *same logical tool call* must compute the *same step_index* on the
resumed pass. Design rule:

- **`step_index` = the monotonic ReAct iteration counter**, persisted on
  `DurableTask.current_step` and mirrored in `DurableReActContext.current_iteration`.
- The counter increments **once per completed LLM round** (in
  `on_iteration_complete`), NOT per tool call. All tool calls dispatched *within
  one iteration* share that iteration's index.
- On resume, `current_iteration` is seeded from the persisted `current_step`, so
  the first replayed iteration reuses the exact index it had pre-crash.

**Caveat — multiple side-effecting calls in one iteration:** Anthropic can emit
several `tool_use` blocks in a single response (anthropic:235 loops over blocks).
If two are side-effecting, a bare `step_index` collides their keys. The args are
part of the key (`ledger.py:64`), so *distinct* args already disambiguate; for
*identical* args in the same iteration, extend the key with an **intra-iteration
ordinal** (a sub-counter reset each iteration), folded into the `step_index`
component deterministically (e.g. `index*1000 + ordinal`) so replays reproduce
it. This stays inside the existing key function — no schema change. (See OD3.)

### 2.5 max_iterations / loop-guard interaction with resume

- `resolved_iterations` (anthropic:130) bounds **one drive**. On resume the loop
  restarts its own `for _ in range(resolved_iterations)` — so the *budget is
  per-drive*, not global. Recommendation: treat the per-drive budget as
  acceptable for Phase 1 (a resumed task gets a fresh iteration budget, which is
  the desired behavior — it should be allowed to finish). Flag a global cap
  (sum across drives) as a Phase-2 hardening if cost runaway is observed (OD2).
- `LoopGuard` (`_react.py:49`) is in-memory per call, so it resets on resume.
  This is benign: the restored context already contains the prior identical-call
  history as observations, and the ledger will short-circuit a re-proposed
  committed call (so it cannot actually re-spin a side effect). The guard's job
  (stop wasted *read* spinning) is unaffected by durability.

---

## 3. Reuse-vs-build table

| Concern | Reuse (already built) | Build (new) |
|---|---|---|
| Exactly-once side-effects | `SideEffectLedger.begin/commit`, `idempotency_key`, `is_side_effecting` (`ledger.py`) | call sites in `_dispatch` |
| Per-iteration checkpoint store | `DurableTaskStore.update_status(current_step=…)` (`store.py:117`) | serialize/restore the ReAct working set (messages+calls); a sidecar column/table for the blob |
| Task record + lifecycle | `DurableTask`, `TaskStatus` (`task.py`) | none |
| Resume / recover driver | `DurableExecutor.resume/recover/_drive/_run_side_effecting` (`executor.py`) | `ReActTaskStep(TaskStep)` whose `run()` re-enters the loop |
| Checkpoint thread isolation | `task_id`→`thread_id` already wired (`langgraph_backend.py:118`); `PipelineState.task_id` (`state.py:57`) | nothing |
| Tool severity taxonomy | `ToolManifest.action_severity` (`tools/base.py:33`), read at `execute.py:142` | nothing |
| Provider loop seam | `complete_with_tools` for/loop (anthropic:182; openai:201) | two optional callbacks: `ledger_guard`, `on_iteration_complete` |
| Assignment trigger (FR13) | `GoalExecutionHandler` (`goal_execution.py`) | create `DurableTask` + set `state.task_id` there |
| Startup recovery | `DurableExecutor.recover()` call (`startup/orchestrator.py:1003`) | upgrade to `resume(task_id, [ReActTaskStep])` |
| DB schema | migration 0045 (`tasks`, `side_effect_ledger`) | (only if a checkpoint-blob column is added) one new additive migration |

**Exact files to touch:**
- `src/stackowl/providers/base.py` — extend `complete_with_tools` signature
  (two optional callbacks), document default-None no-op.
- `src/stackowl/providers/anthropic_provider.py` — call `on_iteration_complete`
  at loop bottom; route side-effecting dispatch through `ledger_guard` (the
  guard is applied *inside* `_dispatch`, so this provider change is just invoking
  the callback at iteration end).
- `src/stackowl/providers/openai_provider.py` — same.
- `src/stackowl/pipeline/steps/execute.py` — build `DurableReActContext` when
  `task_id` set; wrap the `tr = await t(**args)` seam (line 178) with the ledger
  guard; pass callbacks to `complete_with_tools` (lines 257/266).
- `src/stackowl/pipeline/durable/` — add `ReActTaskStep` (new module) and a
  small serialize/restore helper for the ReAct working set; optionally a
  `checkpoint_blob` accessor on the store.
- `src/stackowl/scheduler/handlers/goal_execution.py` — create the `DurableTask`,
  set `state.task_id`.
- `src/stackowl/startup/orchestrator.py` — upgrade durable recovery (line 1003)
  to re-drive ReAct tasks via `resume`.
- (maybe) `src/stackowl/db/migrations/00XX_react_checkpoint.sql` — additive
  column/table for the serialized working set if `result` is unsuitable.

---

## 4. Sequenced sub-story plan (small, green, bisectable)

Each sub-story ends green with its own test and is independently committable
(stage `v2/` only). Two review subagents (QA + dev-regression) before each
commit; per-story smoke + party-mode per standing rules.

1. **S1 — Checkpoint persistence primitive.** Add the serialize/restore helper
   for the ReAct working set (messages + completed calls + iteration counter) and
   the store accessor (reuse `result` blob, or add one additive migration column).
   *Test (unit):* round-trip serialize→persist→restore yields an equal working
   set; owner-scoped read isolation holds.

2. **S2 — Ledger-guarded dispatch seam (no checkpointing yet).** Wrap the
   `tr = await t(**args)` seam in `execute._dispatch` with `is_side_effecting`
   + `ledger.begin/commit`, gated on `task_id is not None`. Read tools and
   ephemeral turns unchanged.
   *Test (gateway):* with a stub provider that dispatches one `write` tool twice
   with identical args under a fixed `task_id`+`step_index`, the real tool runs
   **once**; the second call returns the committed result (probe the real side
   effect). Ephemeral turn (`task_id=None`) runs the tool every time (no regression).

3. **S3 — Provider `on_iteration_complete` callback (additive).** Extend
   `complete_with_tools` in base + both providers to invoke an optional
   `on_iteration_complete` at each loop-bottom; default `None` = exact current
   behavior.
   *Test (unit):* a scripted provider runs N iterations → callback fires N times
   with monotonically increasing index and the growing messages snapshot;
   existing provider tests still pass with no callback.

4. **S4 — Per-iteration checkpoint wiring.** In `execute._run_with_tools`, when
   `task_id` set, build `DurableReActContext` and pass `on_iteration_complete`
   that writes `update_status(current_step=i)` + the working-set blob (S1).
   *Test (gateway):* drive a 3-iteration scripted task → after each iteration the
   `tasks` row's `current_step` advanced and the blob restores the right messages.

5. **S5 — `ReActTaskStep` + resume re-entry.** Add the `TaskStep` that rebuilds
   `PipelineState` from the checkpoint and re-enters the loop; seed
   `ctx.current_iteration` from `current_step` (idempotency-key stability §2.4).
   *Test (unit):* `executor.resume(task_id, [ReActTaskStep])` over a pre-seeded
   half-done task continues from the right iteration and completes.

6. **S6 — Assignment trigger (FR13).** `GoalExecutionHandler` creates the
   `DurableTask` and sets `state.task_id`; interactive chat stays ephemeral.
   *Test (gateway):* a goal run creates a `tasks` row (status running→completed),
   `task_id` flows into `PipelineState`; an interactive CLI turn creates no task.

7. **S7 — Startup recovery re-drive.** Upgrade `startup/orchestrator.py:1003` to
   construct `ReActTaskStep` for orphaned ReAct tasks and `resume` them (fail-open).
   *Test (gateway):* an orphaned `running` ReAct task with a committed side-effect
   in the ledger is re-driven on "restart" and completes without re-running it.

8. **S8 — J-DURABLE journey (kill + resume + exactly-once at user-outcome level).**
   Reuse the `_ScriptedSecretary`/`_FakeBot` scaffold: iter-1 read, iter-2
   side-effect (`write`/consequential — e.g. send a fake-bot message / write a
   temp file), iter-3 final answer. Simulate restart by cancelling after the
   commit but before terminal, rebuild backend+executor over the same DB, resume.
   *Asserts (business outcomes):* the side effect happened **exactly once** (probe
   the real target), and the resumed turn delivered a coherent final answer.

9. **S9 — Hardening + open-decision resolution.** Intra-iteration ordinal for
   multiple identical side-effecting calls (§2.4 caveat / OD3); document/park the
   per-drive vs global iteration budget choice (OD2); `uncertain`→park surfacing
   to the user. *Test:* an iteration emitting two identical-args side-effecting
   calls keys them distinctly and replays both exactly-once.

---

## 5. Risks + open sub-decisions

**Risks**

- **R1 — Provider divergence.** Three `complete_with_tools` implementations
  (anthropic/openai/base default) must call the callbacks consistently; the base
  default has no loop. *Mitigation:* callbacks default `None`; durable behavior
  only engages on providers with a real loop; base-default providers can't run
  multi-iteration durable tasks (acceptable — weak/mock only).
- **R2 — Checkpoint-granularity mismatch (the core architectural risk).** The
  ReAct loop lives inside one LangGraph node (§1.3), so the LangGraph checkpointer
  cannot capture per-iteration state. *Mitigation:* the custom durable-store seam
  (recommended) handles fine granularity; LangGraph stays for coarse resume.
  Rejected alternative (split loop across LangGraph nodes) documented as OD-rej.
- **R3 — LLM non-determinism on resume.** Exactly-once relies on the LLM
  re-proposing the *same* `(tool_name, args)` from restored context. If it
  proposes a *different* call, that's a genuinely new logical action with a new
  key — correctly executed once (no double-run of the original), but the original
  may be skipped/abandoned. This is acceptable (no *duplicate* side-effect, which
  is the J2 guarantee); flag for a determinism note. The `uncertain`/park path
  covers the dangerous crash-mid-commit window.
- **R4 — Working-set blob size.** Long loops produce large messages arrays;
  persisting them every iteration has cost. *Mitigation:* reuse
  `trim_messages_to_budget` output (already bounded, anthropic:186) as the
  serialized form; store only the trimmed array.

**Open sub-decisions (need Boss input)**

- **OD1 — Assignment trigger boundary (FR13).** Recommended: *owl-directed goal =
  durable, interactive chat = ephemeral* (one-handler change, purely additive).
  Alternatives: an explicit `/task` or "make this durable" command; a heuristic
  (long-running / unattended detection). **Needs a decision before S6.**
- **OD2 — Iteration budget on resume.** Per-drive budget (simple, lets resumed
  tasks finish) vs a global cap summed across drives (bounds total cost).
  Recommended Phase-1: per-drive; revisit if cost runaway observed.
- **OD3 — Multiple identical side-effecting calls per iteration.** Add the
  intra-iteration ordinal to the key now (S9) vs accept the rare collision and
  defer. Recommended: add the ordinal (cheap, inside the existing key function,
  no schema change).
- **OD-rej (documented, not chosen) — checkpoint inside LangGraph vs custom
  seam.** Recommendation is the custom durable-store seam (R2). Surfacing for
  Boss visibility in case a single-checkpointer constraint is preferred despite
  the graph-fan-out cost.

---

## 6. Placement note (per the placement-voting rule)

This artifact describes **scope**, not final placement. The new `ReActTaskStep`
and checkpoint helper land under `src/stackowl/pipeline/durable/` (alongside the
executor/ledger/store they reuse), and the provider-callback change is in
`src/stackowl/providers/`. Final placement of the serialize/restore helper and
whether to add a checkpoint-blob column should be confirmed via a placement vote
+ BMad v2 architecture-boundary check (B-boundaries on `pipeline` vs `providers`)
at implementation kickoff, before S1.

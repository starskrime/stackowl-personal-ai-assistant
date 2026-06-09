# Story D1 — Durable Delegated Children (Design Spec)

**Status:** Approved design (2026-06-09), pre-plan.
**Arc:** Phase-2 owl-capability arc, final story (the heavy piece deferred from Story D's light slice).
**Branch:** `feat/agentic-os-stage1`.

---

## 0. Context

Two subsystems are today completely disjoint:

- **Delegation** (`tools/agents/delegate_task.py` + `owls/a2a_delegation.py`): a parent owl calls `delegate_task`, which runs the child as a **synchronous in-process nested pipeline** — the parent awaits the child inline on an in-memory `A2AQueue` (default 30s timeout, then it **cancels** the child task). At `delegate_task.py:378` the parent builds a **fresh, non-durable** `PipelineState` for the child (no `task_id`, no side-effect ledger). So even when the parent is a durable task, the child runs with no checkpoint and no exactly-once protection. On timeout the parent can only return `honest_uncertain` ("a write-capable child may have acted, I cannot confirm, do not auto-retry"); on a process crash the child's work vanishes with no row to recover.

- **Durable tasks** (`pipeline/durable/*`): one `tasks` row per durable goal, checkpointed per ReAct iteration (transcript replay) + a `side_effect_ledger` giving exactly-once per side-effecting tool call (idempotency key = `sha256(task_id, step_index=iteration, tool_name, canonical_args)`, `intent`→`committed`). Startup recovery (`recovery.py`) resumes orphaned `running`/`recovering` rows from their checkpoint. The durable subsystem has **zero awareness of delegation** — no `parent_task_id`, no return-to-parent path.

**D1 extends durability across the delegation seam** so that a write-capable child executes exactly-once across timeout and process-crash, the parent's overall goal completes end-to-end after a crash, and the parent's timeout answer is grounded in something it can actually witness — without re-introducing the overclaim Story D removed.

This spec incorporates the findings of the 2026-06-09 party-mode stress-test (Winston/architecture, Murat/correctness, Amelia/implementation, Dr. Quinn/guarantee-boundary). The convergent findings reshaped two load-bearing pieces (§5 deterministic id, §6 honesty axis) and added four mandatory safety mechanisms (§7 lease + terminalization + reaper, §8 child-backend assembly, §9 cancel-survival + supersession).

---

## 1. Goal & the guarantee statement (the acceptance criterion)

The guarantee D1 makes to the parent owl — stated so it **cannot** overclaim. If the implementation returns a definite answer in any case where this sentence would be a lie, the implementation is wrong:

> **D1 guarantees:** "I will never silently lose a child's durable work to a crash. I will replay only effects that are safe to replay. When I tell you 'done' or 'safe to retry', I am asserting something I can actually witness. When I cannot witness it — an unconfirmed external effect that was in-flight — I will still say `honest_uncertain`, and that is correct, not a degradation."

Two propositions must never be conflated (Dr. Quinn):

- **L (Local commit):** "We durably recorded that we performed tool-call X and will not re-issue it."
- **E (External effect):** "The real-world side effect of X occurred, exactly once, and is correct."

Durability gives us **L** and removes *crash-induced* loss of the work record. It does **not** remove *ack-induced* uncertainty about **E** for effects that cross a process/trust boundary with a lossy acknowledgement (two-generals / lost-ack). D1 must assert **E** only where L ⟺ E (or L ⟹ E under an idempotency contract we own); everywhere else it asserts only L and keeps `honest_uncertain`.

---

## 2. Scope

**In scope (only when the parent is already a durable task — `state.task_id` set):**
- Child runs as its own durable sub-task linked to the parent.
- Exactly-once for replay-safe child side effects across timeout + crash.
- Full end-to-end resume: recovery resumes the parent, which re-resolves the child reusing its exactly-once effects.
- Live-timeout resolution grounded in the durable record, bounded by per-effect `commit_coupling` honesty (§6).

**Out of scope / unchanged:**
- **Non-durable (interactive) parents** — `state.task_id` is `None`. D1 is a complete no-op here: the child runs non-durably exactly as today, and `honest_uncertain` remains the timeout answer. This is the fail-open boundary (§10).
- Out-of-process / queued child execution. Children remain synchronous in-process nested pipelines; D1 only makes them *durable*, not *distributed*.
- Changing the ladder shape (initial → retry-same → fallback-to-secretary) or the existing width/depth/budget caps.

---

## 3. Architecture overview

When a durable parent delegates:

1. The parent's durable scope (`task_id`, `durable_owner_id`) reaches the delegation seam via `TraceContext` (an ambient, **losable** signal — §8). The identity-determining inputs are **explicit parameters**, never ambient reads (Winston's asymmetry: *durability ambient and optional; identity explicit and mandatory*).
2. The parent assigns a durable, monotonic **`delegate_seq`** to this delegation, recorded at delegate-intent time (§5). `child_task_id = derive(parent_task_id, delegate_seq)` — a pure function of parent-assigned, durable quantities, **never** of LLM-influenced position/args/owl.
3. The parent **claims-or-creates** the child `DurableTask` row (`parent_task_id`, `parent_owl`), via an atomic `INSERT … ON CONFLICT DO NOTHING` + re-`SELECT` with a **single-owner lease** (§7). The claim winner executes the child; a loser (e.g. startup recovery racing the live parent) **polls** the record instead of executing.
4. The child sub-pipeline runs **durably** under its own `child_task_id` — `_run_specialist` assembles a real `DurableSession` + checkpoint callback (§8), so the child checkpoints + ledgers under its own clean namespace. If the durable session cannot be assembled, it **fails loud** — never silently non-durable.
5. The parent resolves its `delegate_task` result from the durable record, bounded by `commit_coupling` (§6).
6. Child terminal status is a **projection of the parent's ledger commit** (one commit, two rows — §7), so the child can never be left a permanently-`running` zombie when the parent advances past the delegation.

On crash, startup recovery resumes **roots only** (`parent_task_id IS NULL`); children are re-resolved transitively when the parent re-executes its `delegate_task` and re-derives the **same** `child_task_id` → the child's writes replay (`already_committed`) instead of double-firing. A **reaper** (§7) handles the residual case of a `running` child under an already-terminal parent. Depth is reconstructed from the `parent_task_id` chain on resume, not from a fresh ContextVar (§9).

---

## 4. Schema — migration 0053

`db/migrations/0053_durable_delegation_link.sql` (next free number; durable family is 0045–0049, 0050–0052 are unrelated). **No semicolons inside comments** (the `_split_sql` runner gotcha). Idempotency via the `schema_migrations` version gate (SQLite has no `ADD COLUMN IF NOT EXISTS`).

```sql
ALTER TABLE tasks ADD COLUMN parent_task_id TEXT
ALTER TABLE tasks ADD COLUMN parent_owl TEXT
ALTER TABLE tasks ADD COLUMN delegate_seq INTEGER
ALTER TABLE tasks ADD COLUMN lease_owner TEXT
ALTER TABLE tasks ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(owner_id, parent_task_id)
CREATE INDEX IF NOT EXISTS idx_tasks_roots ON tasks(owner_id, status) WHERE parent_task_id IS NULL
```

- `parent_task_id` — NULL ⇒ root goal; non-NULL ⇒ a delegated child, links to the parent's `task_id`. Self-referential within `tasks`.
- `parent_owl` — the delegating owl name (audit + return-path legibility).
- `delegate_seq` — the parent-assigned monotonic delegation sequence this child was minted from (§5); NULL for roots.
- `lease_owner` — single-owner execution lease holder (§7); NULL ⇒ unclaimed.
- `superseded` — set when a timed-out child is tombstoned so a slow eventual commit is neutralized and the next ladder rung gets a fresh id (§9).
- `idx_tasks_roots` — partial index so roots-only recovery never full-scans as the tree grows (Winston). If the store's SQLite build does not honor the partial index for the recovery query, fall back to `idx_tasks_parent`; validate during planning.

`DurableTask` (`pipeline/durable/task.py`) gains `parent_task_id`, `parent_owl`, `delegate_seq`, `lease_owner`, `superseded` (all optional, defaulting to root/unleased/not-superseded). `DurableTaskStore` SELECT/INSERT/UPDATE column lists carry them.

---

## 5. The deterministic child id — keyed on durable identity, guarded against replay drift

**The linchpin, and the party-mode's #1 finding.** The child id must be stable across re-delegation so a resumed parent finds the *same* child and the child's writes replay exactly-once. The original design derived it from `(parent_task_id, parent_step_index, ladder_rung, target_owl)` — but **`parent_step_index`, the delegate's position-in-iteration, and `canonical_args` are all LLM-influenced and non-deterministic across replay** (Murat SEV-1). A re-sampled parent iteration can emit the delegate at a different ordinal, or phrase the sub-goal differently → a different id → the child re-runs and its writes **double-fire, silently**.

### 5.1 `delegate_seq` — parent-assigned, durable, monotonic

`delegate_seq` is a monotonic counter **per `parent_task_id`**, assigned to a delegation at **intent time** (before the child performs any side effect) and **persisted durably** as part of the parent's delegation intent record. It is *not* derived from iteration position, args, target owl, or any LLM output. Two delegations in the same parent iteration (parallel fan-out, §8) get distinct seqs; the *same* delegation re-encountered on resume reuses its recorded seq.

```
child_task_id = derive(parent_task_id, delegate_seq)
```

A pure helper (`pipeline/durable/delegation_link.py::derive_child_task_id`). Deterministic, collision-free by construction (distinct seqs ⇒ distinct ids; same target twice ⇒ distinct seqs ⇒ distinct ids — resolves Winston's same-target-sibling collision without a convention).

### 5.2 Replay-divergence guard — fail to honest, never silently fork

The existing durable-ReAct system checkpoints completed iterations and replays recorded tool-call decisions; the non-determinism window is an iteration that **crashed before it was checkpointed** and is re-sampled on resume. For that window:

- On resume, before acting on a re-sampled `delegate_task`, the parent looks up whether a delegation intent (and thus a `delegate_seq` + `child_task_id`) was already recorded for this durable position. If present, it **reuses** the recorded seq → same child id → the child is resumed/reused, not forked.
- If the re-sampled iteration's delegation decision **diverges** from what was recorded (different target, materially different sub-goal), the parent **fails to `honest_uncertain`** and logs loudly — it must **never** silently create a divergent second child. (Murat's mandatory guard; this also retroactively hardens the base durable system, a "fix architecture not examples" win.)

**Planning note:** the exact seq-assignment-and-replay mechanism must be validated against the real `ReActCheckpoint` / `side_effect_ledger` / provider-resume code during planning (a recon task), because it depends on whether the base system replays-vs-re-samples a completed iteration. The replay-divergence guard is mandatory **regardless** of what that recon finds.

---

## 6. `commit_coupling` — the honesty axis (Dr. Quinn's reframe, full version)

The decision of *definite-answer vs `honest_uncertain`* is **not** keyed on "is the parent durable" (a property of our bookkeeping). It is keyed on a property each side-effecting tool **declares about itself** — how tightly its real-world effect is coupled to our local commit record:

- **`transactional`** — effect and ledger entry are atomic (e.g. a write to our own SQLite in one transaction). **L ⟺ E.** "Committed → done" is honest.
- **`idempotent_keyed`** — effect is replay-safe under a key we own (our `child_task_id`/ledger key) **and the downstream contractually honors it** (e.g. an endpoint that dedupes on our idempotency token). **L ⟹ E, conditional on the downstream contract.** Honest only because the tool *asserted* the contract at registration.
- **`unconfirmed`** — effect crosses a boundary with a lossy ack (SMTP send, a POST to a non-deduping endpoint, a remote-FS write, a Telegram message). **L and E can diverge irreducibly.** No ledger read can produce a definite answer mid-flight.

### 6.1 Declaration

`commit_coupling` is declared on the tool (alongside `action_severity`), defaulting **fail-safe to `unconfirmed`** for any tool that does not declare it (an undeclared external write is treated as the most uncertain class — never silently "safe"). Planning includes an **audit of every write/consequential tool** to assign its coupling, mirroring the `tests/tools/test_tool_severities.py` pin from Story D. The classification is a small closed enum + a per-tool map, in the spirit of the existing severity map — **not** a heavyweight framework.

### 6.2 Timeout / recovery resolution (replaces the naive "consult the ledger → definite")

Given the child's durable record at the moment the parent must answer:

| Child durable state | Answer |
|---|---|
| Never entered any side-effecting span (no ledger `intent` rows) | **Definite: "not started → safe to retry."** *(The pure-profit leg — durability genuinely converts "I don't know if it ran" into "I can prove it didn't start." This is the half Story D was missing.)* |
| Terminal, and **every** recorded effect was `transactional` or `idempotent_keyed` | **Definite: "done"** — reuse the child's persisted result. |
| In-flight or terminal-with-an `unconfirmed` effect that lacks a witnessed commit | **`honest_uncertain` remains** — exactly as Story D mandated. Durability bought nothing here; do not pretend it did. |
| Any `intent`-state row not yet `committed` for a non-`transactional` effect | **`honest_uncertain`** — `intent` does **not** mean "didn't happen" (Murat SEV-2a). |

This narrows `honest_uncertain` to the provably-uncertain subset rather than "replacing" it. For non-durable parents, `honest_uncertain` is unchanged.

---

## 7. Child sub-task lifecycle — claim-with-lease, parent-driven terminalization, reaper

### 7.1 Claim-with-lease (single-owner execution)

A deterministic `child_task_id` makes **creation** idempotent but does **not** make **execution** single-owner — both a live parent and startup recovery can derive the same id and each think it should run the child (Amelia #2). Required:

- `create_child_task(child_task_id, parent_task_id, parent_owl, delegate_seq, owner_id, …)` performs `INSERT … ON CONFLICT(task_id) DO NOTHING` then re-`SELECT`. The DB unique constraint is the only safe creator; the loser of the create race reads the winner's row. (Do **not** change the root-task INSERT — a duplicate root id is a real bug we want surfaced; add a distinct child method.)
- The same call **claims a lease** (`lease_owner` stamped atomically, e.g. via `execute_returning_rowcount` CAS like the existing `claim_for_recovery`). **The claim winner executes the child; a non-winner polls the durable record** until terminal, then resolves per §6. No double-execution of a side-effecting child.

### 7.2 Parent-driven terminalization (closes Winston's far-side leak)

The child's terminal status is a **projection of the parent's ledger commit**, not a write the child makes about itself. When the parent commits its `delegate_task` ledger entry (after the child returns), **that same commit stamps the child row terminal** (`completed`/`failed`) — one commit, two rows. This collapses the far-side window (parent advanced past the delegation, child row still `running`) into the near-side window already handled, and is the **base case that makes the depth-2 induction sound** (every interior tree node terminalizes its children the same way).

### 7.3 Orphan-child reaper (the residual)

Defensively, a sweep (folded into startup recovery) handles any `running`/`recovering` child whose `parent_task_id` resolves to a **terminal** (`completed`/`failed`) parent — these are unreachable by transitive resolution (the parent will never re-delegate). The reaper marks them `failed`/`abandoned` (loudly logged), never leaving a zombie row. With §7.2 in place this should be empty in the normal path; the reaper is the belt-and-suspenders for crash interleavings.

---

## 8. The delegation seam — ambient durability, explicit identity, real child backend

### 8.1 Threading durable scope (TraceContext, the losable signal)

Add `durable_task_id: str | None` and `durable_owner_id: str | None` to `TraceContext` (`infra/observability/context.py`). The durable execute step (`pipeline/steps/execute.py:426`, which already knows `state.task_id`) sets them for the **scope of the tool execution** and resets after (the set/reset discipline below). `delegate_task` reads them to decide durable-vs-fail-open. **Only the fail-open durability flag rides the ContextVar** — it is safe to lose (you degrade to the non-durable path + `honest_uncertain`). **Identity-determining inputs (`delegate_seq`, derived `child_task_id`) are explicit parameters**, never ambient reads (Winston's asymmetry).

### 8.2 ContextVar isolation across 4 parallel children (Amelia #1, Murat SEV-4)

`asyncio.create_task` snapshots `contextvars.copy_context()` at creation, so per-child isolation is correct **by default** — but three seams break it and are **ACs, not footnotes**:

- **Child scope is passed as a plain value into the child coroutine and `.set()` inside the child frame — never `.set()` on the parent coroutine.** (Else the parent's ContextVar leaks across rungs — "Break A".)
- **Never read `TraceContext` below a thread boundary.** SQLite is sync; if any durable write hops `loop.run_in_executor`, the ContextVar does not propagate — read `task_id`/`owner_id` in the async frame and pass as plain args into the sync store call ("Break B").
- **Every child spawn is `asyncio.create_task`, never a raw coroutine awaited in the parent's frame** ("Break C"). Audit the `MAX_CONCURRENT_DELEGATIONS=4` governor path.

Test: 4 concurrent children each record their observed `task_id`; assert **zero crossover**, including one child that raises and one timeout-cancellation.

### 8.3 Child durable backend assembly (Amelia #4 — the actual work of D1, riskiest change)

`a2a_delegation._run_specialist` currently hand-rolls a bare `AsyncioBackend`. Setting `task_id` on the child state makes `execute.py:426` *try* the durable branch, but **a durable branch with no `DurableSession`/checkpoint store is silent non-durability** — the worst outcome. `_run_specialist` must assemble the child backend through the **same durable assembly the root runner uses** (construct a child `DurableSession(child_task_id, …)`, wire the `react_checkpoint` callback, hand it to the backend). If the session cannot be assembled, it **fails loud** (never silently non-durable — honoring the no-hidden-errors rule). ACs: `test_child_pipeline_persists_checkpoint` (child runs with `task_id` set ⇒ a checkpoint row exists for `child_task_id`); `test_child_durable_branch_without_session_fails_loud`.

---

## 9. Recovery — roots-only, depth-from-tree, cancel-survival, supersession

- **Roots-only orphan listing** (`recovery.py`): filter to `parent_task_id IS NULL`. Children are resolved transitively by the parent's resume re-deriving the same `child_task_id`. (Plus the §7.3 reaper for residual orphans.)
- **Depth reconstructed from the tree** (Winston): `MAX_DELEGATION_DEPTH=2` must be recomputed by counting ancestors via the `parent_task_id` chain on resume, **not** from a fresh ContextVar starting at 0 — else a resumed interior node delegates a 4th level.
- **Cancel-vs-durable-survival** (Amelia): the a2a parent **cancels** the child asyncio task on the 30s timeout. For a *durable* child, cancellation must **not** mark the durable child `failed` — the durable runner keeps it `running`/`recovering` so recovery (or the next turn) resumes it from its checkpoint. AC: `test_a2a_timeout_cancels_task_but_durable_child_survives_as_recovering`.
- **Timeout supersession** (Winston): when the parent *gives up* on a child (timeout → ladder advances to the next rung), it stamps the child `superseded=1`. A slow child's eventual commit is then idempotently neutralized, and the next rung derives a **different** `child_task_id` (distinct `delegate_seq`), so "definite answer under timeout" does not race the very child it is adjudicating at the *decision* layer.

---

## 10. Error handling & fail-open

- **No durable scope** (`durable_task_id` absent — non-durable parent, or the ambient signal was lost): fall back to **today's** non-durable delegation + `honest_uncertain`. D1 never *breaks* delegation; the durable path is purely additive. Loudly logged at debug (decision point), not silently.
- **Store error during claim/create/terminalize:** fail-open to the non-durable path for *this* delegation, loudly logged — never crash the parent's turn, never silently drop durability without a log (no-hidden-errors).
- **Replay divergence** (§5.2): fail to `honest_uncertain`, loudly — never silently fork a divergent child.
- **Ledger-key collisions** between parent and child are structurally impossible (different `task_id` namespaces).
- D1 inherits the base durable-ReAct deterministic-replay assumption; §5.2's divergence guard is the safety net that makes a violation *loud and safe* rather than a silent double-fire.

---

## 11. Testing

### 11.1 Unit
- `derive_child_task_id` determinism: same `(parent_task_id, delegate_seq)` ⇒ same id; distinct seqs ⇒ distinct ids; **independent of args/owl/iteration** (assert the inputs are *only* parent_task_id + delegate_seq).
- `delegate_seq` monotonic-per-parent + persisted at intent.
- Store round-trip of `parent_task_id`/`parent_owl`/`delegate_seq`/`lease_owner`/`superseded`.
- `create_child_task` idempotent under race: two coroutines, same id ⇒ one row, both return the same record; lease single-owner (winner runs, loser polls).
- Recovery root-filter: children (`parent_task_id` non-NULL) excluded from the orphan list.
- Depth-from-tree reconstruction at depth 2.
- `commit_coupling` map: every write/consequential tool has a declared coupling; undeclared ⇒ `unconfirmed` (fail-safe) — pinned like `test_tool_severities.py`.

### 11.2 Interleaving / correctness (Murat's demanded scenarios — assert the failure modes, not just the happy path)
- **Merge-gate journey** (mirrors `tests/journeys/test_j1_j2_durable_kill_resume.py`, real components): durable parent → write-capable child performs write W (`transactional`) → process crash **before** `delegate_task` commits → recovery resumes the parent → W issued **exactly once** → parent goal completes.
- **Replay-divergence:** resume a parent whose re-sampled iteration emits a *different* delegate than recorded ⇒ assert `honest_uncertain` + loud log, **never** a second child / double-fire.
- **Two delegates in one iteration** ⇒ distinct stable `child_task_id`s (distinct `delegate_seq`).
- **4-way concurrent children:** zero `task_id` crossover (incl. one child raising, one cancelled).
- **Timeout-cancel mid-`intent`:** durable child survives as `recovering` (not `failed`); parent consult returns `honest_uncertain` (not false-safe); a slow eventual commit is neutralized by `superseded`.
- **`commit_coupling` honesty:** an `unconfirmed` effect in-flight at timeout ⇒ `honest_uncertain`; a `transactional` committed effect ⇒ definite "done"; a never-started child ⇒ definite "safe retry".
- **Zombie reaper:** a `running` child under a `completed` parent ⇒ reaped (failed/abandoned + logged), never left running.
- **Non-durable parent unchanged:** delegation runs non-durably, `honest_uncertain` on timeout, no `tasks` row created.

### 11.3 Scaffold
Real `DbPool`(tmp) + `MigrationRunner`, real `DurableTaskStore`/`DurableTaskRunner`/`AsyncioBackend`/`ToolRegistry`, scripted crashing/recovering provider (only the AI provider mocked) — the `test_j1_j2_durable_kill_resume.py` pattern. `TestModeGuard._active = False` autouse where the durable drive asserts not-test-mode.

---

## 12. Cuts & Phase-2 backlog

- **Out-of-process / distributed child execution** — children stay synchronous in-process; D1 makes them durable, not distributed. (Revisit if multi-host owl execution lands.)
- **Push-based return-to-parent** — explicitly cut; the pull mechanism (parent's durable resume re-delegates and finds the completed child) is the design. A child does not push to a dead parent turn.
- **Cross-restart de-leasing UI / task tree visualization** — `parent_task_id` makes the tree queryable, but a `/tasks` tree view is out of scope.
- **Generalizing `commit_coupling` into a downstream-contract registry** — D1 ships the closed enum + per-tool map + fail-safe default; a richer contract-assertion system (verifying a remote actually honors an idempotency key) is future work. The honesty boundary is correct regardless: unverified ⇒ `unconfirmed`.

---

## 13. File map (responsibilities)

| File | Change |
|---|---|
| `db/migrations/0053_durable_delegation_link.sql` | Create — columns + indexes (§4) |
| `pipeline/durable/task.py` | `DurableTask` + 5 fields |
| `pipeline/durable/store.py` | SELECT/INSERT/UPDATE carry fields; new `create_child_task` (ON CONFLICT + lease); terminalize-child; reaper query |
| `pipeline/durable/delegation_link.py` | Create — `derive_child_task_id`, `delegate_seq` assignment, ancestor-depth helper (pure) |
| `infra/observability/context.py` | `TraceContext` + `durable_task_id`/`durable_owner_id` (ambient, losable) |
| `pipeline/steps/execute.py` (~:426) | Set/reset durable TraceContext fields around the tool scope |
| `tools/agents/delegate_task.py` (~:378) | Read TraceContext; derive id (explicit params); claim child; inject `task_id` into the fresh child state; resolve per §6 + replay-divergence guard |
| `owls/a2a_delegation.py` (`_run_specialist`) | Assemble child `DurableSession` + checkpoint callback (fail-loud); pass child scope as a value; cancel-survival |
| `pipeline/durable/recovery.py` | Roots-only filter; depth-from-tree; orphan reaper |
| `tools/<write tools>` + `tools/.../severities` map | Declare `commit_coupling` (audit; fail-safe `unconfirmed`) |

---

## 14. The load-bearing invariants (sign-off summary)

1. **Identity is durable, not LLM-derived:** `child_task_id = derive(parent_task_id, delegate_seq)`; `delegate_seq` assigned + persisted at intent; replay-divergence guard fails to `honest_uncertain`, never silently forks (§5).
2. **Honesty is per-effect, not per-parent:** `commit_coupling` decides definite-vs-`honest_uncertain`; the "not-started → safe-retry" leg is the pure-profit win; `unconfirmed` in-flight stays `honest_uncertain` by design (§6).
3. **Single-owner execution + parent-driven terminalization + reaper:** lease (winner runs, loser polls); child terminal = projection of parent's ledger commit; reaper for residual zombies (§7).
4. **Real child durability or loud failure:** `_run_specialist` assembles a real `DurableSession`; never silently non-durable (§8.3).
5. **Asymmetric coupling:** durability ambient and losable (fail-open); identity explicit and mandatory; ContextVar isolation across parallel children is an AC (§8).
6. **Recovery topology:** roots-only + transitive children + depth-from-tree + cancel-survival + supersession (§9).

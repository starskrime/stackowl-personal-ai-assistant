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
2. The child id is derived from the parent's own resume-stable `delegate_task` ledger key (§5): `child_task_id = derive_child_task_id(delegate_key)`, where `delegate_key = idempotency_key(parent_task_id, ctx.iteration, "delegate_task", canonical_args)` — the same coordinate the base ledger already computes for this write-tool call. No LLM-derived counter or position.
3. The parent **claims-or-creates** the child `DurableTask` row (`parent_task_id`, `parent_owl`, `delegate_key`), via an atomic `INSERT … ON CONFLICT(owner_id, task_id) DO NOTHING` + re-`SELECT` with a **single-owner lease** (§7). The claim winner executes the child; a loser (e.g. startup recovery racing the live parent) **polls** the record instead of executing.
4. The child sub-pipeline runs **durably** under its own `child_task_id` — recon confirmed durability is `task_id`-driven (the execute step assembles the `DurableSession` inline from `state.task_id` + services `db_pool`), so D1 only needs to **set `child_task_id` on the child state** and ensure the child does not inherit the parent's `task_id` (§8.3). `_call_durable` already fails loud if `task_id` is set with no `db_pool`.
5. The parent resolves its `delegate_task` result from the durable record, bounded by `commit_coupling` (§6).
6. Child terminal status is a **projection of the parent's ledger commit** (one commit, two rows — §7), so the child can never be left a permanently-`running` zombie when the parent advances past the delegation.

On crash, startup recovery resumes **roots only** (`parent_task_id IS NULL`); children are re-resolved transitively when the parent re-executes its `delegate_task` and re-derives the **same** `child_task_id` → the child's writes replay (`already_committed`) instead of double-firing. A **reaper** (§7) handles the residual case of a `running` child under an already-terminal parent. Depth is reconstructed from the `parent_task_id` chain on resume, not from a fresh ContextVar (§9).

---

## 4. Schema — migration 0053

`db/migrations/0053_durable_delegation_link.sql` (next free number; durable family is 0045–0049, 0050–0052 are unrelated). **No semicolons inside comments** (the `_split_sql` runner gotcha). Idempotency via the `schema_migrations` version gate (SQLite has no `ADD COLUMN IF NOT EXISTS`).

```sql
ALTER TABLE tasks ADD COLUMN parent_task_id TEXT
ALTER TABLE tasks ADD COLUMN parent_owl TEXT
ALTER TABLE tasks ADD COLUMN delegate_key TEXT
ALTER TABLE tasks ADD COLUMN lease_owner TEXT
ALTER TABLE tasks ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(owner_id, parent_task_id)
```

- `parent_task_id` — NULL ⇒ root goal; non-NULL ⇒ a delegated child, links to the parent's `task_id`. Self-referential within `tasks`.
- `parent_owl` — the delegating owl name (audit + return-path legibility).
- `delegate_key` — the parent's `delegate_task` idempotency key this child was minted from (§5; audit + reaper). NULL for roots.
- `lease_owner` — single-owner execution lease holder (§7); NULL ⇒ unclaimed.
- `superseded` — set when a timed-out child is tombstoned so a slow eventual commit is neutralized and the next ladder rung gets a fresh id (§9).
- `idx_tasks_parent` — covers both the child-lookup (`parent_task_id = ?`) and the roots-only recovery scan. Recon (§5) confirmed recovery lists orphans via `store.list(status=...)`, then D1 filters to `parent_task_id IS NULL` in-query; the composite index serves the `IS NULL` predicate. (A partial `WHERE parent_task_id IS NULL` index was considered and cut as premature — the durable task table is small.)

`DurableTask` (`pipeline/durable/task.py`) gains `parent_task_id`, `parent_owl`, `delegate_key`, `lease_owner`, `superseded` (all optional, defaulting to root/unleased/not-superseded). `DurableTaskStore` `_SELECT_FIELDS` / `_row_to_task` / `create()` carry them.

---

## 5. The deterministic child id — derived from the parent's own ledger coordinate

**The linchpin, and the party-mode's #1 finding.** The child id must be stable across re-delegation so a resumed parent finds the *same* child and the child's writes replay exactly-once. The original design derived it from `(parent_task_id, parent_step_index, ladder_rung, target_owl)` — but **the delegate's position-in-iteration is LLM-influenced and non-deterministic across replay** (Murat SEV-1). Planning recon (2026-06-09) confirmed the base durable system **re-samples the LLM on resume** (it does *not* replay recorded tool-call decisions; the only thing preventing duplicate side effects is the `SideEffectLedger`, keyed on `sha256(task_id, ctx.iteration, tool_name, canonical_args)`). So a monotonic counter assigned at runtime is also wrong — a re-sampled parent could assign a different counter value to the same logical delegation.

### 5.1 Derive the child id from the parent's `delegate_task` idempotency key

`delegate_task` is **itself** an `action_severity="write"` tool that already passes through `ledger_guard`, so the base system already computes a resume-stable coordinate for every delegation call: `delegate_key = idempotency_key(parent_task_id, ctx.iteration, "delegate_task", canonical_args)`. The child id is derived from exactly that:

```
child_task_id = derive_child_task_id(delegate_key)   # e.g. f"child-{delegate_key[:32]}"
```

A pure helper (`pipeline/durable/delegation_link.py::derive_child_task_id`). On resume, a re-sampled parent that re-emits **the same delegation at the same iteration with the same args** computes the **same** `delegate_key` → the same `child_task_id` → it re-attaches to the existing child row (claim-or-create is a no-op, §7) instead of forking. A delegation the model *drops* on resume is never re-derived, and its result is already in the parent's ledger via the `delegate_task` tool's own commit. **This piggybacks on the exact-once guarantee that already works for every other write tool — D1 adds no new divergence machinery; it inherits the base ledger's semantics verbatim.**

### 5.2 The determinism boundary (inherited, stated honestly)

The child id is **exactly as resume-stable as the base ledger's exactly-once guarantee** — no more, no less. Two inherited caveats, documented rather than re-solved (they are properties of the base durable system, not introduced by D1):

- **Args-rephrasing on resume:** if the re-sampled parent phrases the sub-goal differently, `canonical_args` differs → a different `delegate_key` → a different child. This is identical to the exposure the base ledger already has for *every* write tool (the `delegate_task` call's own ledger entry would also re-key and re-run). D1 does not worsen it; hardening it (a transcript-divergence guard on the base provider-resume seam) is a base-system improvement tracked separately (§12).
- **Same-args twice in one iteration (the S9 caveat):** two identical `delegate_task` calls in one parent iteration collide on one `delegate_key` — for delegation this is *desirable* dedup. Distinct-but-identical-arg delegations needing to coexist would require an intra-iteration ordinal, the same S9 hardening the base ledger defers. Documented as the D1 equivalent of the S9 caveat (§12), not solved now.

Within those inherited boundaries the derivation is deterministic and collision-free (distinct `delegate_key`s ⇒ distinct ids; same target with distinct args ⇒ distinct keys ⇒ distinct ids — resolves Winston's same-target-sibling collision).

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

- `create_child_task(child_task_id, parent_task_id, parent_owl, delegate_key, owner_id, …)` performs `INSERT … ON CONFLICT(owner_id, task_id) DO NOTHING` (via `execute_returning_rowcount` directly, **not** `_insert_owned`, which builds a plain INSERT) then re-`SELECT`. The composite-PK conflict clause is the only safe creator; the loser of the create race reads the winner's row. (Do **not** change the root-task INSERT — a duplicate root id is a real bug we want surfaced; add a distinct child method.)
- The same call **claims a lease** (`lease_owner` stamped atomically, e.g. via `execute_returning_rowcount` CAS like the existing `claim_for_recovery`). **The claim winner executes the child; a non-winner polls the durable record** until terminal, then resolves per §6. No double-execution of a side-effecting child.

### 7.2 Parent-driven terminalization (closes Winston's far-side leak)

The child's terminal status is a **projection of the parent's ledger commit**, not a write the child makes about itself. When the parent commits its `delegate_task` ledger entry (after the child returns), **that same commit stamps the child row terminal** (`completed`/`failed`) — one commit, two rows. This collapses the far-side window (parent advanced past the delegation, child row still `running`) into the near-side window already handled, and is the **base case that makes the depth-2 induction sound** (every interior tree node terminalizes its children the same way).

### 7.3 Orphan-child reaper (the residual)

Defensively, a sweep (folded into startup recovery) handles any `running`/`recovering` child whose `parent_task_id` resolves to a **terminal** (`completed`/`failed`) parent — these are unreachable by transitive resolution (the parent will never re-delegate). The reaper marks them `failed`/`abandoned` (loudly logged), never leaving a zombie row. With §7.2 in place this should be empty in the normal path; the reaper is the belt-and-suspenders for crash interleavings.

---

## 8. The delegation seam — ambient durability, explicit identity, real child backend

### 8.1 Threading durable scope (TraceContext, the losable signal)

`TraceContext` (`infra/trace.py`) is a plain class of per-field `ContextVar`s (not a Pydantic model), populated by `TraceContext.start(...)` and read via `TraceContext.get()`. Recon found `task_id` is **not** propagated to the tool layer today. Add `_task_id` and `_durable_owner_id` ContextVars, stamped in `TraceContext.start(...)` from `state.task_id` / `state.durable_owner_id` at the seam that already maps state→trace: **`AsyncioBackend.run`** (`pipeline/backends/asyncio_backend.py`), exactly as `delegation_depth` / `creation_ceiling` already propagate state→trace→tool. Expose `task_id` (a safe string) in `get()`; keep `durable_owner_id` readable via a dedicated accessor. `delegate_task` reads them to decide durable-vs-fail-open. **Only the fail-open durability signal rides the ContextVar** — safe to lose (you degrade to the non-durable path + `honest_uncertain`). **Identity-determining values (the derived `delegate_key` / `child_task_id`) are computed explicitly at the seam from the parent's task_id + the ledger coordinate**, never inferred from ambient mutable state (Winston's asymmetry).

### 8.2 ContextVar isolation across 4 parallel children (Amelia #1, Murat SEV-4)

`asyncio.create_task` snapshots `contextvars.copy_context()` at creation, so per-child isolation is correct **by default** — but three seams break it and are **ACs, not footnotes**:

- **Child scope is passed as a plain value into the child coroutine and `.set()` inside the child frame — never `.set()` on the parent coroutine.** (Else the parent's ContextVar leaks across rungs — "Break A".)
- **Never read `TraceContext` below a thread boundary.** SQLite is sync; if any durable write hops `loop.run_in_executor`, the ContextVar does not propagate — read `task_id`/`owner_id` in the async frame and pass as plain args into the sync store call ("Break B").
- **Every child spawn is `asyncio.create_task`, never a raw coroutine awaited in the parent's frame** ("Break C"). Audit the `MAX_CONCURRENT_DELEGATIONS=4` governor path.

Test: 4 concurrent children each record their observed `task_id`; assert **zero crossover**, including one child that raises and one timeout-cancellation.

### 8.3 Child durable execution (recon-simplified — durability is `task_id`-driven)

Recon corrected the original worry: the `DurableSession` is **not** held by the backend or `StepServices` — it is assembled **inline inside the execute step** (`execute.py::_call_durable`) from `state.task_id` + `get_services().db_pool`, and the `DurableReActContext` is published via a ContextVar that `ledger_guard` reads. So **any sub-pipeline whose `state.task_id` is set and whose services carry a `db_pool` runs durably automatically.** `_run_specialist` already passes the same `StepServices` (hence the same `db_pool`) to its child `AsyncioBackend`, so the *only* required change is to **set `task_id=child_task_id` (and `durable_owner_id`) on the child state** — the execute step does the rest.

Two correctness points remain: **(a)** the gap recon flagged — `parent_state.evolve(...)` in `_run_specialist` does **not** reset `task_id`, so a durable parent's child would otherwise inherit the *parent's* `task_id` and collide on the parent's ledger step-index space; D1 must **explicitly set the child's own `child_task_id`** (via the fresh state built at `delegate_task.py:378`, which today sets no `task_id`). **(b)** Fail-loud is already enforced: `_call_durable` raises `RuntimeError` if `task_id` is set but `db_pool` is `None` — D1 inherits that, never silently non-durable. ACs: `test_child_pipeline_persists_checkpoint_under_child_task_id` (child runs ⇒ checkpoint + ledger rows exist under `child_task_id`, **not** the parent's); `test_durable_parent_child_does_not_inherit_parent_task_id`.

---

## 9. Recovery — roots-only, depth-from-tree, cancel-survival, supersession

- **Roots-only orphan listing** (`recovery.py`): filter to `parent_task_id IS NULL`. Children are resolved transitively by the parent's resume re-deriving the same `child_task_id`. (Plus the §7.3 reaper for residual orphans.)
- **Depth reconstructed from the tree** (Winston): `MAX_DELEGATION_DEPTH=2` must be recomputed by counting ancestors via the `parent_task_id` chain on resume, **not** from a fresh ContextVar starting at 0 — else a resumed interior node delegates a 4th level.
- **Cancel-vs-durable-survival** (Amelia): the a2a parent **cancels** the child asyncio task on the 30s timeout. For a *durable* child, cancellation must **not** mark the durable child `failed` — the durable runner keeps it `running`/`recovering` so recovery (or the next turn) resumes it from its checkpoint. AC: `test_a2a_timeout_cancels_task_but_durable_child_survives_as_recovering`.
- **Timeout supersession** (Winston): when the parent *gives up* on a child (timeout → ladder advances to the next rung), it stamps the child `superseded=1`. A slow child's eventual commit is then idempotently neutralized, and the next rung derives a **different** `child_task_id` (the next rung's `delegate_task` call has different `canonical_args`/iteration → a different `delegate_key`), so "definite answer under timeout" does not race the very child it is adjudicating at the *decision* layer.

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
- `derive_child_task_id` determinism: same `delegate_key` ⇒ same id; distinct keys ⇒ distinct ids; the function takes **only** `delegate_key` (no owl/iteration/args passed separately — they're already folded into the key).
- `derive_child_task_id` is a pure function of `delegate_key` only (same key ⇒ same id; different keys ⇒ different ids).
- Store round-trip of `parent_task_id`/`parent_owl`/`delegate_key`/`lease_owner`/`superseded`.
- `create_child_task` idempotent under race: two coroutines, same id ⇒ one row, both return the same record; lease single-owner (winner runs, loser polls).
- Recovery root-filter: children (`parent_task_id` non-NULL) excluded from the orphan list.
- Depth-from-tree reconstruction at depth 2.
- `commit_coupling` map: every write/consequential tool has a declared coupling; undeclared ⇒ `unconfirmed` (fail-safe) — pinned like `test_tool_severities.py`.

### 11.2 Interleaving / correctness (Murat's demanded scenarios — assert the failure modes, not just the happy path)
- **Merge-gate journey** (mirrors `tests/journeys/test_j1_j2_durable_kill_resume.py`, real components): durable parent → write-capable child performs write W (`transactional`) → process crash **before** `delegate_task` commits → recovery resumes the parent → W issued **exactly once** → parent goal completes.
- **Resume re-attaches, not re-forks:** resume a parent that re-emits the *same* delegation (same args, same iteration) ⇒ the same `delegate_key` ⇒ the same `child_task_id` ⇒ `create_child_task` is a no-op and the child's writes replay `already_committed` (child runs exactly once).
- **Two distinct delegates in one iteration** (different args) ⇒ distinct `delegate_key`s ⇒ distinct `child_task_id`s.
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
- **Base-system transcript-divergence guard** — recon found the base durable provider-resume seam re-samples the LLM with **no** prefix-divergence check. D1 inherits this exposure (§5.2) but does not worsen it; a guard on `validate_resume_transcript` that rejects a re-sampled prefix diverging from the checkpoint is a base-system hardening tracked separately (benefits all durable tasks, not just delegated children).
- **Intra-iteration ordinal for the ledger key (the S9 caveat)** — two distinct-but-identical-arg `delegate_task` calls in one parent iteration collide on one `delegate_key`. For delegation this is desirable dedup; coexisting identical-arg delegations would need the same intra-iteration ordinal the base ledger defers (S9). Documented, not solved.

---

## 13. File map (responsibilities)

| File | Change |
|---|---|
| `db/migrations/0053_durable_delegation_link.sql` | Create — columns + index (§4) |
| `pipeline/durable/task.py` | `DurableTask` + `parent_task_id`/`parent_owl`/`delegate_key`/`lease_owner`/`superseded` |
| `pipeline/durable/store.py` | `_SELECT_FIELDS`/`_row_to_task`/`create()` carry fields; new `create_child_task` (`execute_returning_rowcount` ON CONFLICT DO NOTHING + re-SELECT); `claim_child_lease` CAS; `terminalize_child`; `list_children`/reaper query |
| `pipeline/durable/delegation_link.py` | Create — `derive_child_task_id(delegate_key)`, ancestor-depth-from-tree helper (pure) |
| `infra/trace.py` | `TraceContext` + `_task_id`/`_durable_owner_id` ContextVars; `start(...)` stamps them; `get()` exposes `task_id` |
| `pipeline/backends/asyncio_backend.py` | `run()` passes `state.task_id`/`state.durable_owner_id` into `TraceContext.start(...)` |
| `tools/agents/delegate_task.py` (~:378) | Read durable scope from TraceContext (fail-open); compute `delegate_key` + `child_task_id`; claim-or-create child; set `task_id=child_task_id`/`durable_owner_id` on the fresh child state; parent-driven terminalize on commit; resolve per §6 (commit_coupling); supersede on timeout |
| `owls/a2a_delegation.py` (`_run_specialist`) | Pass child scope as a value (Break-A); cancel-survival (a2a timeout must not mark a durable child `failed`) |
| `pipeline/durable/recovery.py` | Roots-only filter (`parent_task_id IS NULL`); depth-from-tree on resume; orphan-child reaper |
| `tools/base.py` + each write/consequential tool + `tests/tools/test_tool_severities.py` sibling | `commit_coupling` field on `ToolManifest`; declare per tool (audit; fail-safe default) |

---

## 14. The load-bearing invariants (sign-off summary)

1. **Identity is durable, not LLM-derived:** `child_task_id = derive_child_task_id(delegate_key)` where `delegate_key` is the parent's own resume-stable `delegate_task` ledger idempotency key; inherits the base ledger's exactly-once semantics verbatim, adds no new divergence machinery (§5).
2. **Honesty is per-effect, not per-parent:** `commit_coupling` decides definite-vs-`honest_uncertain`; the "not-started → safe-retry" leg is the pure-profit win; `unconfirmed` in-flight stays `honest_uncertain` by design (§6).
3. **Single-owner execution + parent-driven terminalization + reaper:** lease (winner runs, loser polls); child terminal = projection of parent's ledger commit; reaper for residual zombies (§7).
4. **Real child durability or loud failure:** durability is `task_id`-driven — setting `child_task_id` on the child state makes the execute step assemble the `DurableSession` inline; the child must not inherit the parent's `task_id`; `_call_durable` already fails loud if `task_id` is set with no `db_pool` (§8.3).
5. **Asymmetric coupling:** durability ambient and losable (fail-open); identity explicit and mandatory; ContextVar isolation across parallel children is an AC (§8).
6. **Recovery topology:** roots-only + transitive children + depth-from-tree + cancel-survival + supersession (§9).

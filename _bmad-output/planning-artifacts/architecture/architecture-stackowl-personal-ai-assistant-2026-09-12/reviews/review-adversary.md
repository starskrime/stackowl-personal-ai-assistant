---
review: adversarial
lens: 'Two units one level down that obey every AD to the letter and still build incompatibly'
target: ARCHITECTURE-SPINE.md (status draft, updated 2026-09-13)
reviewer: adversary
date: 2026-09-13
verdict: 'Not ready to bind epics. The read model is well specified, but the write lane has three entry paths and the durable Needs-you row is not tied to the ephemeral consent wait. Cross-process version skew covers frames only, not schema or registry.'
counts: {critical: 3, high: 9, medium: 13, low: 5}
---

# Adversarial review: StackOwl Bridge architecture spine

## Method

For each hole, two units (epics, or stories in different epics) each follow every quoted AD word for word and still build incompatible systems. Code claims were spot-checked in `src/stackowl/` on 2026-09-13; each one names its file. Tiers:

- **Critical:** wrong authority or lost decisions, or the Bridge breaks on the normal upgrade path.
- **High:** a guaranteed integration failure, or a broken product principle.
- **Medium:** divergence that costs rework or produces visible wrongness.
- **Low:** polish or edge cases.

Code facts this review relies on:

| Fact | Where |
| --- | --- |
| The `cronjob` and `owl_build` tools call `scheduler.pause/resume/run_now` directly | `tools/scheduling/cronjob.py:634,671,673`, `tools/meta/owl_build.py:1966,1968` |
| `CommandRegistry` is a text-args singleton; `dispatch(name, args, state)` runs the handler inline | `commands/registry.py:60-96` |
| `TaskLoop` defaults: `max_parallel=5`, `tick_seconds=5.0`, `lease_seconds=900`, `prune_after_days=1`; the wake is an in-process `asyncio.Event` | `pipeline/durable/loop.py:88-124` |
| `prune_completed` deletes completed task rows older than N days (default 1) | `pipeline/durable/store.py:1971-2005` |
| `job_runs.job_id` is `ON DELETE CASCADE` (migration 0080); one-shot jobs are deleted | `scheduler/scheduler_helpers.py:158`, `scheduler/scheduler.py:719,790` |
| Pending consent is an in-memory `asyncio.Future` per prompt; a timeout resolves to DENY | `channels/telegram/consent.py:76-211`, `runtime/socket_consent.py:41-88` |
| An unknown channel routes to `AutonomousPrompter`, which grants ordinary consequential actions | `tools/consent.py:641-683` |
| `HelloFrame` goes core → gateway only; `protocol_version: int = 1`; nothing carries schema or registry versions | `ipc/frames.py:34-39` |
| Migrations run in orchestrator phase 1, not gated by role, so a core-only `os.execv` migrates under a running gateway | `startup/orchestrator.py:515-533,629-632` |
| The setup code and password hash live in the platform **secret store**, not SQLite | `control_plane/password.py:62-65,304-446` |
| `IdentityResolver` resolves handles only from the `identity.aliases` setting | `tenancy/identity.py:14-65` |
| The owl display name is a column in `owls`, hydrated into core's in-memory registry | `owls/store.py:38-42`, `startup/orchestrator.py:1024-1108` |

---

## Critical

### C1. Three mutation entry paths: typed COMMAND tasks, inline slash commands, and model tool calls into the same mutators

**Units:** *Epic: Missions station* (pause, resume and run-now buttons) vs *Epic: Voice orders and anticipation* (Owl pauses a failing job; the owner says "pause the backup").

**ADs both obey:**
- AD-1: "Binds: every Bridge, voice, Telegram and TUI action … Each mutation is one typed command (AD-26) … `CommandRegistry.dispatch` … → `DurableTaskStore.enqueue` as a COMMAND task."
- AD-26: "A deterministic command-handler registry maps each command type to the existing subsystem mutator … LLM goal tasks are unchanged."
- AD-3: "Subsystems call `journal.record(TypedEvent)` at the exact site where the action happens."
- Conventions, State: mutation: "Only through AD-1, as COMMAND tasks."

**Incompatible legal choices:**
- *Missions:* declares `job.pause` (WRITE, reversible, undo `job.resume`) in the new durable handler registry. The handler records `job.paused` with `actor=device` (AD-1: "the device principal is the actor").
- *Voice/anticipation:* the utterance becomes an LLM goal task, which AD-26 leaves "unchanged". The model calls the existing `cronjob` tool, which calls `scheduler.pause` directly (`cronjob.py:671`). No AD binds tool calls inside a goal task, so this is legal. Owl's autonomous pause (full picture §1: "Owl paused it; undo is on its card") takes the same route through `owl_build.py:1968`.
- *Telegram epic:* keeps `/jobs`-style slash commands inline in `CommandRegistry.dispatch` (`registry.py:93`), because the only thing AD-1 fixes there is a severity check.
- *Recording:* Missions records in the handler. The scheduler epic obeys AD-3 by recording in `JobScheduler.pause`, the "exact site". The result is either two `job.paused` rows per button press, or none on the tool path.

**Failure:**
- One action ends up with three severities, two or three actor shapes, and undo on only one path.
- The AD-27 gate never sees Owl's actions, although Owl is exactly the requester it exists to gate ("A request from an Owl or the crew needs a read-back").
- A Q52 correction cannot find the misheard action, because it never became a COMMAND task with a `trace_id`.
- The AD-3 tripwire ("fails any state-mutation path that records nothing") passes or fails depending on which layer happened to record.

**Smallest fix (new AD-1a, "one declaration, one entry, one recorder"):**
- Every mutating action has exactly one `CommandSpec` in `commands/`: type, payload model, severity, reversibility, undo type. The slash parser, the Bridge API, voice and **tools** all produce that spec.
- A tool whose effect matches a registered spec submits the command with `requester=owl:<name>` through the same gate. It never calls the mutator.
- The subsystem mutator is the only recorder of the domain event. It takes an explicit `CommandContext` (actor, requester, trace_id, command task id). The COMMAND handler records only `command.*` lifecycle events.
- Tripwire: a mutator that has a registered spec is called only from the COMMAND handler registry.
- Amend AD-26: "LLM goal tasks are unchanged *except that their mutating tool calls submit commands*."

### C2. First-answer-wins is not atomic, and a durable Needs-you row outlives the ephemeral consent wait it answers

**Units:** *Epic: Needs-you strip* (approve or deny on the strip) vs *Story in Epic: Telegram consent mirroring* (inline buttons), plus *Epic: core restart and self-healing*.

**ADs both obey:**
- AD-28: "Items survive core restarts. The first answer from any surface resolves the item, and every other surface is updated."
- AD-18: "An approval mirrored to Telegram buttons and the Needs-you strip resolves first-answer-wins."
- AD-1: "Binds: … approvals …", so an approval is a typed command enqueued as a COMMAND task.
- AD-2: "nothing derives or decides subsystem state from the journal", while AD-28 has the item table "owned by `journal/`".

**Incompatible legal choices:**
- *Telegram story:* keeps today's mechanism. The button completes the in-memory `Future` in the Telegram prompter (`channels/telegram/consent.py:180-211`). A second copy of the resolution is then written to the item row.
- *Strip epic:* the approval is a COMMAND task (AD-1). The handler sets `resolved_cursor` on the row, then signals core.
- Neither AD requires a compare-and-set on `resolved_cursor IS NULL` in the same transaction as the resolve event. Each epic reads "first answer" against its own store: the Future, or the row.

**Failure:**
1. **Split decision.** The owner taps Deny on the phone while Approve lands in Telegram. The Future takes Telegram's answer and the row takes the strip's answer. The audit trail and the action disagree.
2. **Orphans.** `HUMAN_DECISION_TIMEOUT_SECONDS` resolves the Future to DENY, and a core `os.execv` drops `_pending` entirely (`runtime/socket_consent.py:45`). The AD-28 row stays open for days. The owner later taps Approve: the item resolves "approved", nothing is waiting, and nothing runs. That is a recorded approval with no effect.
3. **Deadlock.** An approval enqueued as a COMMAND task needs a free loop worker. Up to 5 workers (`max_parallel=5`) can each be blocked awaiting consent. The approval that would unblock them queues behind them until timeout.
4. **Authority in the read model.** The package AD-2 calls "derived" now holds the authoritative decision.

**Smallest fix (tighten AD-28):**
- One resolver, `needs_you.resolve(item_id, answer, surface, principal)`. It runs `UPDATE … SET resolved_cursor=?, answer=?, resolved_by=? WHERE id=? AND resolved_cursor IS NULL` in the same transaction as the `needs_you.resolved` event.
- Every surface (Telegram callback, strip, voice) calls only this resolver. The waiter (consent Future or task) is completed only from the winning resolve, delivered to core by frame.
- An answer to an item is **not** a COMMAND task, because it resolves a wait rather than mutating a subsystem.
- Each item row carries `waiter_kind` + `waiter_id` and `expires_at`. A waiter timeout, or the core boot sweep for waiters that no longer exist, resolves the item as `expired` through the same resolver.
- An answer to a resolved or expired item returns the winning outcome, and for `expired` offers "re-ask".
- State the decision's owner: the answer is authoritative on the item row, and AD-2's "derived" covers only the journal table.

### C3. Protocol-version enforcement covers frames, but schema and event-registry skew between gateway and core is unguarded

**Units:** *Epic: Voice*, which adds a migration (a new NOT NULL journal column or a new identity column), new event types and an `attrs` v2, vs *Epic: Sign-in and devices*, whose gateway action sites write sessions and device events through the gateway DbPool.

**ADs both obey:**
- AD-33: "Both ends check the Hello `protocol_version` … Every new frame bumps `protocol_version`."
- AD-9: "The gateway records its own action sites … through its own DbPool into the same table."
- AD-3: "`record` accepts only registered types with valid `attrs`."
- Conventions: "New tables come only by migration, numbered at merge."

**Incompatible legal choices:**
- *Voice epic:* adds event types, an `attrs` version and a migration, but no new frame, so AD-33 requires no `protocol_version` bump.
- *Sign-in epic:* the gateway validates with the registry it imported at gateway start.
- An upgrade then restarts only core (the normal `os.execv` path). Migrations run in orchestrator phase 1 for every role (`startup/orchestrator.py:629-632`), so the new core migrates the shared database under the old, still-running gateway.

**Failure:**
- The old gateway's session-rotation insert breaks on the changed schema. Every device gets a 401, which is the Bridge going dark on exactly the path AD-8 exists to protect.
- The old gateway receives core-pushed events of types its registry does not know. AD-3 says `record` rejects them, but fan-out has no rule: it drops them (a silent gap), crashes, or forwards unvalidated.
- The attention policy runs in both processes (device events are gateway-recorded), so the two classify with different policy versions.
- `HelloFrame` today is one-way and only core sends a version (`ipc/frames.py:34-39`). A gateway-side check has nothing of its own to compare.

**Smallest fix (widen AD-33):**
- Hello is exchanged both ways and carries `protocol_version`, `schema_head` (the highest applied migration number) and `registry_digest` (a hash of registered (type, version) pairs plus the attention policy version).
- Any mismatch refuses the link. The gateway pauses its own journal and identity writes until its code matches `schema_head`, then re-execs.
- The gateway re-checks `schema_head` (`PRAGMA user_version` or the migrations table) before its first write after any link loss.

---

## High

### H1. Consent runs in the gateway for Bridge commands and in core for everything else, and the gate plus the prompter can each open an approval

**Units:** *Epic: Bridge command API* vs *Epic: Anticipation and undo* (the AD-27 gate), with *Telegram consent* as the incumbent.

**ADs both obey:**
- AD-1: "severity check … → action-policy gate (AD-27) → consent through the channel's registered prompter (AD-18) → `DurableTaskStore.enqueue`", which puts consent **before** enqueue.
- AD-7: "`bridge/` … may depend on … `tools/consent`."
- AD-10: active grants are "core-only in-memory state".
- AD-27: "An irreversible action that was not explicitly pre-authorised, in any run the owner is not attending, becomes a Needs-you item of kind `approval`."
- AD-18: "prevents … double approval."

**Incompatible legal choices:**
- *Bridge epic:* consent must finish before enqueue, and `bridge/` may import `tools/consent`. So it builds a `ConsentPolicy` in the gateway. The gateway has no session grants (core memory), and the `web` prompter is wherever config put it. The same action is always prompted on the web but silently allowed on Telegram after a session grant. Worse, if the gateway's router has no `web` entry, today's `RoutingPrompter` sends it to `AutonomousPrompter` (`tools/consent.py:664-682`).
- *Gate epic:* for an irreversible request, the gate opens an `approval` item. On approval the command continues to "consent through the channel's registered prompter", and the `web` prompter opens a second `approval` item for the same action.
- "Attending" is undefined. One epic reads it as a live carrier connected; another reads it as "the request came from a channel adapter", which is the provenance rule in `consent.py:541-551`.

**Failure:** "No back door" breaks in both directions. The same command gets different consent per surface, and the owner is asked twice for one irreversible action.

**Smallest fix (reorder AD-1 and merge the decision):**
- `dispatch` (severity) → `enqueue` (task state `awaiting_decision`) → **one** core-side `authorize(command, requester, attendance)`, which composes the AD-27 gate with ConsentPolicy and grants → handler.
- `authorize` opens at most one Needs-you item per command task. Prompters only render and answer items (C2); they never decide.
- Define `attendance` once in `authz/`, as a function of the requester's origin (live channel adapter plus trigger kind), and forbid other definitions.

### H2. `record_ref` points at rows the owning subsystem deletes within a day, and nobody owns how a record is read

**Units:** *Epic: Archives / flight recorder* vs *Epic: Missions station*, plus the existing scheduler retention.

**ADs both obey:**
- AD-4: "Every openable target is a durable row: opening a record fetches its content from the row named by `record_ref`."
- AD-6: journal retention is 30 days.
- AD-2: "Subsystem tables remain the source of truth."
- AD-10: "record queries (AD-4) … go through the gateway's own DbPool."

**Incompatible legal choices:**
- *Missions:* obeys AD-4 by pointing `task.*` events at `tasks` rows and job events at `job_runs` rows. It keeps the subsystem's own retention, which no AD touches: `TaskLoop` prunes completed tasks after 1 day (`loop.py:96`, `store.py:1971`), and `job_runs` cascade-delete when a one-shot job is removed.
- *Archives:* builds one generic record query, `SELECT * FROM {record_ref.table} WHERE rowid=?`, over the gateway DbPool. Nothing says that is forbidden, or who renders a row.
- *Memory epic:* records md memory writes (AD-24). The row that holds that content is a file, so it sets `record_ref` to a path.
- Kuzu is core-only, so record_refs into it cannot be read from the gateway.

**Failure:** Replaying last week, almost every task and job-run mover is unopenable. That breaks "every mover opens its record" and the §1 narrative. The generic query also lets `bridge/` read any table's raw columns, secrets included, bypassing the subsystem's own content rules.

**Smallest fix (AD-4a):**
- Each table that may appear in a `record_ref` is registered in the AD-3 registry with a `RecordReader` supplied by the owning subsystem (typed output, authority-checked, readable from the gateway DbPool) and a retention promise of at least the journal retention.
- A target that can be deleted earlier resolves to a registered tombstone ("this task's details were pruned on …").
- md memory events reference memory's SQLite index row, never a path.
- Tripwires: (a) every `target_kind` has a reader; (b) no subsystem prune window is shorter than the journal setting unless the kind declares tombstones.

### H3. `schema_version` evolution against rows that are never updated

**Units:** *Epic: Voice* (renames a field in `voice.command_issued` → v2) vs *Epic: Briefing / Archives* (narrates and replays 30 days of rows).

**ADs both obey:**
- AD-2: "Rows are never updated."
- AD-3: "Every event type is declared once in the versioned registry … `record` accepts only registered types with valid `attrs`."
- Conventions: "One registry name per type, versioned by `schema_version`."
- AD-30: "renders each registered event type."
- AD-6: "Retiring an event writer deletes its code, its registry type and its rows."

**Incompatible legal choices:**
- *Voice:* obeys "declared once" by replacing the v1 model with v2.
- *Archives:* validates rows it reads against the registry. For 30 days, v1 rows either fail validation (replay aborts) or are skipped (silent gaps).
- A third epic keeps both models and adds `if version == 1` branches to the narrator. The browser client store, built separately, applies a different upcast.

**Failure:** The flight recorder, the briefing and the narrator diverge on history. Replay is "exactly as recorded" only for the current version.

**Smallest fix (AD-3a):**
- Within a version, `attrs` changes are additive and optional only.
- A breaking change adds a new version and registers an upcaster from the previous version in `journal/`.
- Every consumer (narrator, attention, fan-out, snapshot, browser) receives events already upcast to the latest version by the gateway. The browser never sees old versions.
- The registry keeps each (type, version) model until the oldest row of that version has been pruned. A tripwire checks this against `MIN(cursor)` per version.

### H4. Gap filling can wedge on pruned or retired rows, and open Needs-you items can reference pruned events

**Units:** *Story: seeded journal prune job* vs *Epic: gateway fan-out*, plus *Epic: Needs-you strip*.

**ADs both obey:**
- AD-9: "fills any cursor gap from the table before delivering later rows."
- AD-6: pruning, plus "Retiring an event writer deletes … its rows."
- AD-28: items hold `opened_cursor`.
- AD-19 and AD-30: notification text comes from the narrator at delivery time.

**Incompatible legal choices:**
- *Fan-out:* treats a missing cursor as "not yet visible" and waits or retries before delivering later rows. That is a natural reading of "fill before delivering".
- *Prune job / writer retirement:* deletes rows in the middle of a range, which leaves permanent holes.
- A phone that was offline 35 days resumes from a cursor older than `MIN(cursor)`.
- *Strip epic:* an item such as a job paused 40 days ago keeps `opened_cursor` pointing at a pruned event. Re-narrating it for a new device or push has no event left to narrate.

**Failure:** Fan-out stalls every client behind a hole that will never fill, or a returning client silently skips 35 days while showing live state. The strip holds an item with no text.

**Smallest fix:**
- *Amend AD-9:* SQLite has a single writer, so every cursor at or below the committed maximum is final and a missing one is pruned or retired. Gap fill never waits.
- *Amend AD-11 and AD-31:* a resume cursor below the retained minimum gets `resync_required`, and the client store re-snapshots (AD-29).
- *Amend AD-6:* prune never deletes an event referenced by an unresolved Needs-you item. Prune deletes in bounded batches so it never holds the write lock for long; every state change in the platform needs that lock.

### H5. Who delivers a Needs-you notification, on which channel, per kind

**Units:** *Epic: Phone notifications* vs *Epic: Sign-in and devices*, plus *Story: Telegram consent mirroring*.

**ADs both obey:**
- AD-19: "**Every** Needs-you item notifies through the owner's existing Telegram channel and through standard Web Push."
- AD-17: "never approved from Telegram (Q48)."
- AD-18: "Device approvals are never mirrored."
- Structural seed: `alerts` sits in **core** under `rec`.
- AD-9: device approvals are **gateway** action sites.

**Incompatible legal choices:**
- *Notifications epic:* builds the alerter in core on `journal.record` (per the seed). It sends every item to Telegram plus Web Push, including a device item such as "new device wants access, open to approve".
- *Devices epic:* opens device items from the gateway. Core never sees that commit, because nothing pushes gateway rows to core, so under the notifications design no alert fires at all, and certainly not on Web Push.
- *Mirroring story:* the Telegram prompter already sends a buttons message for an approval. AD-19 sends a second narrator message for the same item.

**Failure:** Device requests are either announced on Telegram (contrary to the intent of Q48) or never announced. Approvals arrive twice on Telegram. A resolved item leaves a stale Web Push notification behind, because AD-28's "every other surface updated" names no retraction.

**Smallest fix (AD-19a):**
- One notifier, in the gateway, driven by fan-out of `needs_you.opened` and `needs_you.resolved` from both origins.
- A fixed delivery matrix:

  | Kind | Telegram | Web Push |
  | --- | --- | --- |
  | `approval` | the prompter's buttons message **is** the notification | yes |
  | `device` | never | yes, to signed-in devices only |
  | `question`, `incident` | narrator text | yes |

- On resolve, edit the Telegram message and replace the push notification using the item `id` as its tag.

### H6. Severity lives in `authz/`, but the gateway's command registry is not the one core assembles

**Units:** *Epic: Bridge command API* (gateway process) vs *Epic: Telegram / TUI commands* (core process in split mode).

**ADs both obey:**
- AD-1: "severity check through the principal in the one shared `CommandRegistry.dispatch`, for every surface."
- AD-7: `bridge/` depends on `commands/`.
- AD-8: the Bridge is in the gateway.

**Incompatible legal choices:**
- *Bridge epic:* calls `CommandRegistry.instance()` in the gateway process. That is a per-process singleton (`commands/registry.py:19-29`). DI-assembled commands, such as those needing `JobScheduler` or `ProviderRegistry`, are registered only where assembly ran.
- *Telegram epic:* keeps dispatching in core.

Each is "the one shared dispatch" inside its own process. The gateway copy lacks core-bound commands, or re-assembles its own services, which is a second instance of the scheduler and the owl registry in the gateway.

**Failure:** Missing commands on the web, or two live `JobScheduler` instances racing the same `jobs` table.

**Smallest fix (fold into C1's `CommandSpec`):**
- `dispatch` needs only the spec table (severity, payload model) and the task store, and it must be constructible in the gateway without subsystem services.
- Handlers exist only in core's durable handler registry.
- Tripwire: the gateway process never instantiates a subsystem mutator.

### H7. Setup-code relocation contradicts where the code lives today and reopens Telegram as a device-enrolment route

**Units:** *Epic: Owner identity (`authz/identity/`)* vs *Story: Q29 relocation of the deliverer hook and migration*, plus *Epic: Sign-in and devices*.

**ADs both obey:**
- AD-17: "`authz/identity/` holds, **in SQLite**, the setup code (relocated from control_plane) … The code goes to the terminal and to the single allowed Telegram user … A damaged record returns the install to setup-code mode (L3)."
- AD-7: "its setup code and password store move to `authz/identity/`."
- AD-17 (Q48): "A new device is approved only from a signed-in Bridge device … or with the recovery code. It is never approved from Telegram."

**Incompatible legal choices:**
- *Identity epic:* follows "in SQLite" and writes the code to a new table.
- *Relocation story:* follows "move … the password store" and moves `ControlPlanePassword` as is. It reads and writes the **platform secret store** (`password.py:62-65`: `stackowl-control-plane-setup-code`, `…-password-hash`), and L3's unreadable-versus-damaged distinction is a secret-store state.

The result is two copies of the setup code (secret store and SQLite), and a relocated password hash that the passkey Bridge never reads, which is dead code.

- *Identity epic, again:* applies L3 as written. A damaged owner or passkey record returns the install to setup-code mode, and L1 sends a fresh code to Telegram. `reset-password` does the same. A Telegram-only attacker who can induce damage, or an owner running reset while a stolen phone holds Telegram, enrols a new passkey via Telegram. Q48 forbids exactly that.

**Failure:** Duplicated secret state, dead code, and a Telegram enrolment path.

**Smallest fix (tighten AD-17 and AD-7):**
- The setup code stays in the secret store; secrets never go to SQLite. Only its **consumer** moves to `authz/identity/`.
- The password hash, the L2 import (`config/control_plane_password_migration.py`) and `reset-password` are deleted with control_plane, not relocated.
- Setup-code mode, and sending the code to Telegram, happen only while **zero** passkeys are enrolled.
- With at least one passkey, a damaged or unreadable record refuses sign-in, opens an incident, and accepts only the recovery code or a signed-in device. The host CLI prints a terminal-only code.

### H8. The actor is the device, the principal is the owner, and no rule makes the web session the same person

**Units:** *Epic: Comms (`web` channel adapter)* vs *Epic: Security station and audit*.

**ADs both obey:**
- AD-1: "The device principal is the actor on the resulting events and audit."
- AD-17: "Every `web:<device>` principal resolves to the owner's default principal in code. No per-device alias is ever written to `stackowl.yaml`."
- The full picture §8.7 requires the web to be "the same person as the Telegram owner".

**Incompatible legal choices:**
- *Security epic:* records `actor_id = owner` after resolution, so the audit trail cannot say "approved from iPhone".
- *Comms epic:* sends ingress as `session_key=web:<device>`. The conversation and memory stack resolves identity through `IdentityResolver`, which knows only `identity.aliases` from settings (`tenancy/identity.py:46-65`). AD-17 forbids writing an alias there, so the web handle resolves to a **new** identity with separate conversation and memory.

**Failure:** Either the audit trail loses the device, or "one mind, many surfaces" breaks: the web Owl does not know the Telegram conversation.

**Smallest fix (AD-17a):**
- The envelope and audit carry `actor_kind=device, actor_id=<device_id>`. Authority and identity use `principal=owner`.
- `IdentityResolver` gains one code-level rule: handles on owner-authenticated channels (`web:*`, `voice:*`) resolve to the owner `identity_key` supplied by `authz/identity/`.
- Tripwire: a web ingress and a Telegram owner ingress resolve to the same `identity_key`.

### H9. Attention is frozen at record time, but "healed" is only known later

**Units:** *Epic: Engineering / self-healing events* vs *Epic: Briefing and Needs-you strip*.

**ADs both obey:**
- AD-5: the policy classifies "using its type, outcome and healed status … A healed failure stays `ambient` …; an unhealed failure is `needs_you` at `high` intensity."
- AD-2: "Rows are never updated."
- AD-5: "A `needs_you` event opens a Needs-you item."
- AD-24: the event is recorded in the transaction of the change.

**Incompatible legal choices:**
- *Engineering:* records `job.run_failed` at the failure site with `outcome=failed`. At that instant the failure is unhealed, so the policy marks it `needs_you/high`, opens an item, and AD-19 pages the phone at 03:00. The heal a minute later cannot reclassify an immutable row.
- *Briefing:* assumes, per Q34, that healed failures never reach the strip, and hides them by joining on later heal events. That derives display state from the journal and still does not stop the page already sent.

**Failure:** Dark-cockpit attention fails. Every failure that later self-heals pages the owner.

**Smallest fix (amend AD-5):**
- A failure event is always `ambient`.
- `needs_you` comes only from explicit give-up event types that the owner of the retry or heal loop emits when it stops trying (`heal.exhausted`, `task.dead_lettered`, `job.parked`). The registry marks those types `needs_you`.
- "Unhealed" must be a recorded event, never the absence of one.
- Each item-opening type also declares its resolving event types (see M3).

---

## Medium

### M1. Standing authority has two owners and two vocabularies

- **Units:** *Epic: Missions* (scheduled irreversible runs) vs *Epic: Crew* (owl grants).
- **ADs:** AD-27, "Standing authority for a pre-authorised irreversible action is stored on the owning row", and AD-26, "Every command declares … its reversibility".
- **Clash:** Missions adds `jobs.preauthorized_actions` as a JSON list of command types. Crew widens `owls.bounds` with consent categories and tool names, as `owl_build` does today. For a scheduled owl run the gate cannot tell which one wins, or how a tool name maps to a command type. Consent grants are also deliberately memory-only (§4), while standing authority is durable, so the two are one concept with opposite lifetimes.
- **Fix:** In AD-27, standing authority is keyed only by `CommandSpec` type (C1). It lives in one `authz/` table, `standing_authority(scope_kind, scope_id, command_type, granted_by, granted_at, revoked_at)`, and is written only by `authority.grant` and `authority.revoke` commands. "Owning row" becomes the `scope_id` foreign key. Session consent grants stay memory-only and never satisfy the irreversible rule.

### M2. Narrator names: `attrs` examples invite frozen and private names, and name lookup creates an import cycle

- **Units:** *Epic: Missions* vs *Epic: Crew rename*.
- **ADs:** Conventions, "attrs … (for example memory kind, tool name, channel, **job name**)". AD-30, "renders … at delivery time, using current names". AD-7, "`journal/` … imports nothing from `bridge/` or `voice/`", with subsystems importing `journal/`.
- **Clash:** Missions puts `job_name` in `attrs`, and the narrator template uses it, so the name is frozen. Crew expects current names, so the narrator imports `owls/`, which imports `journal/`: a cycle. Core's narrator reads the in-memory owl registry while the gateway's reads the `owls` table, so the two disagree until core re-hydrates. User-authored job and owl names are content the leak guard cannot see.
- **Fix:** `attrs` carry ids and closed enums only, with no display names or free text; the leak guard asserts field types. Subsystems register a `NameResolver(target_kind)` into `journal/`, which inverts the dependency. It reads the owning table in either process and falls back to a tombstone ("a retired owl"). The signature is `narrate(event, locale)`, with `en` as the only locale for now.

### M3. Needs-you item identity: no dedupe key and no auto-resolution

- **Units:** *Epic: Missions* vs *Epic: Engineering*.
- **ADs:** AD-5, "A `needs_you` event opens a Needs-you item"; AD-28, the open set is items without `resolved_cursor`.
- **Clash:** One epic opens one item per event, so five failures produce five items. The other keys items by target. Nothing says which event closes an incident item when the job is later resumed or healed, so the strip accumulates stale items or each epic writes its own closer.
- **Fix:** Add `dedupe_key` (kind plus target) with a partial unique index on open items. The registry declares, per opening type, its resolving event types. The recorder resolves through the C2 resolver in the same transaction.

### M4. Heartbeat measures gateway liveness, not platform liveness

- **Units:** *Epic: carriers* vs *Epic: Viewscreen presence*.
- **ADs:** AD-12, "the gateway sends a heartbeat on every open carrier"; AD-8, the Bridge survives core restarts; full picture §1, "in step with the platform's real heartbeat".
- **Clash:** The carrier epic sends heartbeats from a gateway timer. While core is down, re-exec-looping or wedged, the mark keeps breathing: false liveness. The Viewscreen epic expects stale.
- **Fix:** The heartbeat carries `core_link` (`up`, `restarting` or `down`), `core_last_seen_at` and `head_cursor`. Define two states: *link stale* (missed heartbeat) and *ship offline* (`core_link` not `up` past a grace announced in the hello). Breathing requires both to be healthy.

### M5. Browser ↔ gateway version skew

- **Units:** *Epic: front end (PWA and service worker)* vs *Epic: Bridge API*.
- **ADs:** AD-21 (committed build, package data); AD-33 (covers core ↔ gateway only); AD-11, "identical event envelope".
- **Clash:** The PWA service worker caches bundle N. After an upgrade the gateway serves API N+1 (`extra=forbid` payloads, new envelope fields). Nothing in the server hello names a version, so buttons fail with 422s, or the client drops unknown fields.
- **Fix:** The server hello carries `bridge_api_version` and `build_id`. The client store compares them with its own build and forces a service-worker update and reload on mismatch. The gateway serves assets from the build loaded at gateway start.

### M6. Snapshot is cursor-consistent; core query frames and md state are not

- **Units:** *Epic: Engineering* (breakers via AD-10 frames) vs *Epic: Viewscreen*; also *Epic: Crew* (memory kinds from md).
- **ADs:** AD-29, "together with the journal cursor it is consistent with"; AD-10, query frames for breakers, windows and grants; AD-24, "md memory records immediately after its write".
- **Clash:** A breaker query frame returns state with no cursor, so a `provider.breaker_opened` event before or after it makes the UI flip-flop. The md write lands before its event, so a snapshot reads the new md state with the old cursor. A Viewscreen that applies events as deltas (count + 1) double-counts, while Crew re-fetches.
- **Fix:** Every query-frame response carries `as_of_cursor`, core's last committed cursor. Convention: stream events are *invalidations or upserts keyed by target*, never increments.

### M7. One event type emitted from both processes

- **Units:** *Epic: Sign-in* (gateway) vs *Epic: Comms* (core `sessions` table).
- **ADs:** AD-9 (gateway records "sessions"); AD-3 (types declared once); AD-24 (atomic with the change).
- **Clash:** Both emit `session.started`: the gateway for device sessions, core for conversation sessions. They mean different entities with different `record_ref` tables. A gateway voice event has no SQLite state change, so AD-24 atomicity is vacuous there, and its ordering relative to core's turn events is commit order, not causation.
- **Fix:** The registry declares one emitting process and one owning table per type; a type emitted from both is illegal. Events without a SQLite state change are marked `ephemeral_source` and recorded after the action.

### M8. Q52 undo lookup uses the journal as a decision source

- **Units:** *Epic: Voice* vs *Epic: Anticipation and undo*.
- **ADs:** AD-27, "Every voice command carries the `trace_id` of its utterance, so a Q52 correction automatically undoes…"; AD-2, "nothing derives or decides subsystem state from the journal".
- **Clash:** Voice finds the action to undo by querying the journal for `trace_id`, the only place it appears. Anticipation stores `trace_id` on the COMMAND row, which `prune_completed` deletes after one day. One `trace_id` also spans a turn with several commands.
- **Fix:** COMMAND task rows carry `utterance_id` (distinct from `trace_id`). Undo resolves from the task store. Completed COMMAND rows are retained for at least the undo window (exempt from the one-day prune).

### M9. COMMAND idempotency and starvation on the shared loop

- **Units:** *Epic: Missions* (run now) vs *Story: loop retry and lease reclaim*.
- **ADs:** AD-26, "COMMAND tasks share the one store, loop, workers, retry and delivery".
- **Clash:** The handler's mutator commits in its own transaction. The worker dies before marking the task complete, and `reclaim_expired` after `lease_seconds=900` re-runs it, so run-now fires twice. A pause button also waits behind 5 long LLM goal tasks.
- **Fix:** A command `idempotency_key` is written in the mutator's transaction, and a repeat is a no-op. The loop reserves capacity for COMMAND tasks (a minimum of one worker slot), in the same loop.

### M10. Gateway enqueue does not wake core's loop

- **Units:** *Epic: Bridge command API* vs *`pipeline/durable`*.
- **ADs:** AD-10, "command enqueue go[es] through the gateway's own DbPool"; AD-9, "Live events are never polled".
- **Clash:** `TaskLoop.wake()` is an in-process `asyncio.Event` (`loop.py:110-124`). A gateway-side insert waits out `tick_seconds` (5 s), and one epic adds a wake frame while another shortens the tick.
- **Fix:** Enqueue from the gateway is followed by a typed `task_enqueued` frame that calls `wake()`; the tick stays a safety net only. Say so in AD-10.

### M11. Protocol-mismatch restart direction

- **Units:** *Epic: gateway supervision* vs *Story: core rollback*.
- **ADs:** AD-33, "the gateway restarts itself under supervision to load matching code".
- **Clash:** If core is the older side (rollback, or core re-exec failed to load new code), a gateway restart reloads the same newer code, so it restarts in a loop and kills the voice and Bridge sessions AD-8 protects each time.
- **Fix:** The side with the older version restarts. After N failed matches, stop, open an `incident` item and show stale.

### M12. Prompter set "from configuration" vs channels with no config section

- **Units:** *Epic: `web` channel* vs *Story: AD-18 prompter derivation*.
- **ADs:** AD-18, "Every gateway channel registers its consent prompter from configuration, and core derives its prompter channel set from that configuration … A channel with no registered prompter fails closed … and a Needs-you item of kind `incident` opens"; Conventions, "Capabilities ship enabled".
- **Clash:** `web` and `voice` ship enabled with no config section, so the derived set lacks them and every web request is denied. Meanwhile scheduler, MCP and webhook origins (no channel) now open an incident per request, flooding the strip, where today they reach `AutonomousPrompter`.
- **Fix:** Derive the prompter set from the live `ChannelRegistry` (adapters that started). Identify autonomous runs by explicit origin (`trigger_kind`), never by channel absence. Dedupe incidents per channel.

### M13. The drift test cannot be both Node-free and meaningful

- **Units:** *Epic: front end* vs *Story: packaging and CI*.
- **ADs:** AD-21, "A test fails when the source and the committed build drift … needing Node … at install" is prevented.
- **Clash:** One epic's test rebuilds with Vite and diffs, which needs Node, and minified output is not byte-stable across Node versions. The other hashes files, which misses a lockfile change. Two epics merged in parallel each commit rebuilt bundles, so a merge conflict in minified files or a merged build that matches neither source.
- **Fix:** `vite build` writes `bridge/static/build-manifest.json` with a content hash of `web/bridge/` source plus lockfile. The Python tripwire recomputes the source hash and compares it, with no Node needed. A separate Node CI job verifies the build reproduces the manifest. Rule: rebuild after merge, never merge bundles.

---

## Low

- **L1. Revocation and live sessions.** AD-16 authenticates the WebTransport session "in the first message", and AD-23 authenticates signalling by device token. Neither says revocation terminates live carriers or voice sessions. A revoked phone keeps streaming until it reconnects. *(Arguably high for a lost phone; tier low only because a reconnect happens within a heartbeat or two once rotation is enforced.)* **Fix:** a `device.revoked` event closes every carrier, signalling channel and voice session bound to that session row.
- **L2. Rotate-on-use races.** AD-16's "rotates on use" plus AD-31's shared store and a service worker opening a push deep link: concurrent requests present the pre-rotation token and get 401, then a passkey re-prompt that AD-16 promises not to show. **Fix:** single-flight rotation in the client store; the previous token stays valid for a short grace window.
- **L3. Voice worker binding.** The remote worker never sees the device identity; it must accept only SDP relayed by the gateway. **Fix:** the gateway mints a per-session, revocable voice ticket bound to the device session and passes it with the offer; the worker rejects offers without one.
- **L4. WebTransport pre-auth window.** An accepted session before its first message is an unbounded resource. **Fix:** close any session that has not authenticated within the hello timeout.
- **L5. Shared-store heartbeat under background throttling.** Browsers throttle timers in backgrounded shared workers and tabs, producing false stale. **Fix:** stale evaluation runs on visibility and on carrier events, not on a throttled timer alone.

---

## Coverage of the requested probes

| Probe | Holes |
| --- | --- |
| Event registry, `attrs` typing, `schema_version` | C3, H3, M2, M7 |
| `record_ref` vs table ownership | H2 |
| Needs-you lifecycle across Telegram, Web Push and strip | C2, H5, H9, M3 |
| Command handler registry vs existing mutators | C1, H6, M9, M10 |
| Action-policy gate vs consent prompter; standing authority | H1, M1, M12 |
| Snapshot cursor vs stream cursor | M6, H4 |
| Narrator vs renames and localisation | M2 |
| Device tokens vs WebTransport vs voice signalling | L1, L2, L3, L4 |
| Gateway-written vs core-written events | C3, M7, M10 |
| Protocol skew during core restart | C3, M5, M11 |
| Retention prune vs flight recorder | H4, H2, M8 |
| Committed build vs source drift | M13 |
| authz/identity relocation vs Q29 paths | H7, H8 |
| Heartbeat and stale vs fallback carrier | M4, L5 |

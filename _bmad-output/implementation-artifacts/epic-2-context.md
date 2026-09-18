# Epic 2 Context: The ship keeps a truthful log

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the platform one trustworthy, append-only record of everything it does — every task, job, heal, health change, model/tool/delegation call, memory write, consent decision, and delivery — written as metadata only, in the same transaction as the change it records, so nothing shown about the platform's work can disagree with what actually happened. Recording must survive core restarts without loss or duplication, gateway and core must prove version compatibility before trusting each other, split-mode TUI progress must come back to life reading from this journal (replacing the dead `ProgressEventFrame` path), and the journal must stay small on small hardware through measured retention and write budgets. This epic closes the journal half of spike B2.

## Stories

- Story 2.1: The journal records task events in the same transaction as the change
- Story 2.2: Every event reads as a plain sentence and knows whether it needs the owner
- Story 2.3: Gateway and core refuse to talk across versions
- Story 2.4: Only the real core can connect to the gateway
- Story 2.5: Split-mode TUI progress comes back, from the journal
- Story 2.6: Jobs, heals and health have a history
- Story 2.7: Every turn's model calls, tool calls and delegation hops are recorded
- Story 2.8: Memory writes and consent decisions are recorded
- Story 2.9: Deliveries, channels and providers are recorded
- Story 2.10: Nothing escapes the journal
- Story 2.11: The journal stays small on small hardware
- Story 2.12: Journal cost measured on a small host

## Requirements & Constraints

- Every subsystem state change must produce a journal event; a coverage check fails on any migrated table with no registered events or "unjournaled" excuse.
- Events are metadata only — never prompt/message text, tool content, display names, or exception text; only ids, closed enums, bounded labels, error codes. A runtime leak guard redacts secret-shaped strings, verified with canary strings through real emitters.
- Recording is atomic with the change: a forced rollback leaves no event, a commit always leaves exactly one.
- Attention (ambient vs. needs-you, normal/high) is computed once, purely, from an event's registered class — never set by the emitter; only give-up/exhaustion events reach needs-you/high.
- Every event type needs a narration and, for openable targets, a registered reader — both enforced by automated checks.
- Gateway and core refuse to operate across mismatched protocol version, migration level, or registry digest; the older side restarts under supervision; unknown frame types never pass silently.
- Only the core process the gateway itself started may hold the link (peer-credential + per-boot secret checks); the IPC socket/pipe is owner-only.
- Split-mode TUI progress runs end-to-end from the journal stream (never polled), with no lost/duplicated events across a real core restart.
- Rows a journal event references must outlive journal retention; a check fails any subsystem prune window shorter than retention.
- Retention defaults to 30 days (a setting), pruned by exactly one idempotently-seeded job in bounded batches with secure delete and post-prune WAL checkpoints; nothing else deletes journal rows, and retention-held rows are never pruned.
- Per-turn/command write volume and record latency are budgeted; crossing a budget degrades health and warns but never drops an event. Concrete retention/budget values stay provisional until Story 2.12's benchmark reports real measurements from the platform's own hardware.

## Technical Decisions

- **CQRS split:** subsystem tables stay the source of truth; the journal is a derived, append-only read model nothing is computed from — distinct from `audit_log`, the jsonl log, and the in-process EventBus.
- **One recording API:** subsystems call `journal.record(conn, event)` at the action site, in the same transaction as the state change (transactional outbox). Non-SQLite state (md memory) records immediately after the write; a state-less event registers `ephemeral_source`. A failed record degrades journal health rather than being swallowed.
- **One versioned registry:** each event type is declared once with a typed `attrs` model, one emitting process, a record kind, an attention class, and (if applicable) resolving types. Evolution is additive-only via `schema_version` + upcasters, kept until their rows are pruned. The registry digest travels in the gateway↔core Hello.
- **Envelope:** `event_id` (UUIDv7), `cursor` (`INTEGER PRIMARY KEY AUTOINCREMENT`, never reused/updated — the resume point for carriers, fan-out, and snapshots), `type` (dotted, lower-case, past tense, e.g. `task.claimed`), `schema_version`, `occurred_at` (ISO-8601 UTC), `actor_kind`+`actor_id`, `device_id`, `target_kind`+`target_id`, `outcome` (ok/failed/healed/parked/pending/dead_lettered/expired), `attention`+`intensity`, `record_ref` (`{kind, locator}`; kinds `sqlite`/`md`/`graph`, each with one registered authority-checked reader; a missing target opens `expired`), `attrs`, `trace_id`, optional `duration_ms`. Actor/target kinds are a closed list including `device`, `voice_worker`, `autonomous`.
- **Package placement:** `journal/` owns registry, upcasters, recorder, store, attention policy, record-reader registry, narrator, snapshot checkpoints, retention; any subsystem may import it, but it imports nothing from `bridge/`, `voice/`, or any subsystem — names/records reach it only through registered ports.
- **Gateway-core Hello:** exchanged both directions carrying `protocol_version`, highest applied migration number, and registry digest; a mismatch refuses the link loudly, restarts the older side under supervision, and repeated failures open an `incident`. The gateway pauses journal/identity writes after any link loss until migration numbers match. New typed IPC frames live in `ipc/frames.py` with `extra=forbid`. `SteerFrame`, `StopFrame`, `QueryRunningFrame`, `RunningStateFrame`, and `ProgressEventFrame` are deleted outright; every frame change bumps `protocol_version`.
- **Fan-out:** core pushes each event to the gateway only after commit; the gateway records its own action sites via its own DbPool into the same table; fan-out merges both by cursor, drops duplicates, upcasts, and never blocks on a gap (a hole at/below the committed max is permanent and skipped). After a restart or lost link, the gateway resumes from its last delivered cursor before live fan-out.
- **Performance budgets:** bounded outbound queue per client (drop-and-resync on overflow), per-turn/command write budgets, and a gateway memory budget for small hardware — values provisional pending Story 2.12.

## Cross-Story Dependencies

- Story 2.1 (table, `journal.record`, package placement) underlies every other story in this epic.
- Story 2.2's attention policy/narrator are prerequisites for later needs-you classification and for Epic 3's Needs-you items, which open directly from `needs_you`-classified events.
- Stories 2.3–2.4 (Hello compatibility, link authentication) must land before Story 2.5, which pushes events over that same link and deletes `ProgressEventFrame` in the same change.
- Stories 2.6–2.9 each add event types to the 2.1–2.2 registry and are checked by the Story 2.10 coverage/upcaster tripwires.
- Story 2.11's prune job depends on retention holds later epics register (e.g. Epic 3's unresolved Needs-you items); it must never delete anything this epic still needs.
- Story 2.12 depends on Stories 2.1–2.11 being complete and produces the values Story 2.11's provisional settings ultimately adopt via a deferred-work entry.

# Epic 3 Context: Owl's questions are durable and answered once

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the platform one durable home for everything that needs the owner: approvals, clarifying questions, incidents, budget alerts and new-device requests. Today these can be lost on a restart, answered twice on two surfaces, or silently auto-granted on a channel nobody is watching. This epic makes every such item a durable, deduplicated row with one atomic resolver so the first answer always wins; wires consent routing to fail closed on unknown channels instead of falling back to an autonomous grant; carries the reply address across the gateway↔core link so an approval always returns to the exact chat that asked; and delivers every item to Telegram with Owl's deterministic read-back, editable in place when it resolves anywhere. Until Epic 4 lands durable COMMAND tasks, an item's waiter for an in-flight turn lives in core memory, so a core restart resolves it as `expired` rather than leaving it stranded.

## Stories

- Story 3.1: Give-ups become durable Needs-you items
- Story 3.2: One answer wins, on every surface
- Story 3.3: Approvals and questions become Needs-you items
- Story 3.4: Consent never grants a channel nobody can answer
- Story 3.5: The consent address survives the gateway↔core link
- Story 3.6: Answer from Telegram and watch it close

## Requirements & Constraints

- The Needs-you queue holds five item kinds: `approval`, `question`, `incident`, `alert`, `device` — one open item per target, enforced by a dedupe constraint.
- Only explicit give-up/exhaustion events (`heal.exhausted`, `task.dead_lettered`, `job.parked`, repeated Hello mismatch) open `incident` items at `high`; a healed failure never reaches the queue. Budget warnings open `alert` items. Intensity is set only by the attention policy (Epic 2), never by the emitter.
- An item is answered exactly once across every surface (Telegram, strip, voice, notification deep link): the first answer wins; an answer to an already-resolved or expired item returns the winning outcome unchanged; an answer whose version/digest no longer matches the item is refused and the item is re-shown.
- Items survive core restarts. An item bound to an in-memory turn waiter resolves as `expired` at core boot if that turn is gone; a durable waiter (Epic 4 COMMAND tasks) re-materialises instead.
- A consent request on a channel with no live, registered prompter is denied and opens a deduplicated `incident` for that channel — never silently auto-granted. The `RoutingPrompter`→`AutonomousPrompter` fallback is deleted outright.
- Scheduled/autonomous runs carry the explicit principal `autonomous:scheduler`, set by the trigger, never inferred from a channel or payload; that principal keeps its own autonomous grants so unattended jobs still finish.
- `ConsentRequest.channel` comes only from ingress provenance, never from the payload; the provenance auto-grant never applies to `web` or `voice`. Always-ask categories (`prompt_surface`, `destructive`, `lock`, `alarm`, `authority_widening`, `owl_build`) can never be bypassed by "run at once."
- `ConsentRequestFrame` carries `reply_target` across the gateway↔core IPC link (typed, `extra=forbid`, bumps `protocol_version`); a frame arriving without it is refused and logged, never routed to a guessed destination.
- Telegram delivery: `approval` items show the narrator's deterministic `full` read-back on a buttons message (never model-written); each button carries a short opaque token (well under the 64-byte `callback_data` limit) resolved server-side to item id, version and shown-request digest. `question`/`incident`/`alert` items deliver as narrated text. A Telegram answer is accepted only from the owner's allowlisted chat and may resolve any approval, including irreversible/consequential ones, from anywhere — a documented residual risk (a hijacked Telegram account can approve irreversible actions). On resolve (from any surface), the Telegram message is edited to show the outcome and loses its buttons.
- `device` items are declared as a kind in this epic; their Telegram delivery (name + matching code) ships in Epic 5.
- Prove the Telegram approval path end-to-end through `scripts/dev_ingress.py` with only the AI provider mocked, including split-mode group/thread delivery.

## Technical Decisions

- **Needs-you table** (owned by `journal/`, created by idempotent migration): `id`, `kind`, `intensity`, `record_ref`, `dedupe_key`, `waiter_kind`, `waiter_id`, `expires_at`, `version`, `opened_cursor`, `resolved_cursor`, `answer`, `resolved_by`. The `answer` on this row is authoritative — the journal's "derived" rule does not apply to it. A partial unique index on `dedupe_key` over unresolved rows enforces one open item per target.
- **One resolver, `needs_you.resolve`:** a conditional `UPDATE … WHERE id = ? AND resolved_cursor IS NULL` in the same transaction as the `needs_you.resolved` event. Every surface calls only this resolver; an answer is never enqueued as a COMMAND task. A winning resolution is delivered to its waiter by frame. A seeded expiry sweep is the only other writer of `expired` outcomes, run as exactly one idempotently-seeded job.
- **Consent stays a core-side, one-shot decision** (composes with the Epic 4 action-policy gate later): prompters only render an item and hand its answer to the resolver — they never decide. The prompter channel set derives live from `ChannelRegistry`, never a hardcoded list.
- **One narrator in `journal/`** renders every item and read-back deterministically at delivery time — `full` for Telegram, `public` (kind/count only) for push surfaces. No surface stores or writes its own sentence.
- **One gateway notifier** drives Telegram/Web Push dispatch from fan-out of `needs_you.opened`/`needs_you.resolved`; this epic implements the Telegram side, Web Push follows in Epic 11.
- **Package boundaries carry over from Epic 2:** `journal/` owns the table, resolver, attention policy and narrator; it imports nothing from subsystems. `tools/consent` and the channel prompters live outside `journal/` and call only the resolver.
- Retention (Epic 2's prune job) must never delete an event referenced by an unresolved Needs-you item — each open item is registered as a retention hold.

## Cross-Story Dependencies

- Story 3.1 (table, migration, item-opening) underlies every other story in this epic.
- Story 3.2's resolver is required before 3.3 (approvals/questions route through it), 3.4 (denied consent opens incidents through it) and 3.6 (Telegram answers call it).
- Story 3.4's fail-closed routing and 3.5's `reply_target` frame must both land before 3.6 can prove split-mode Telegram delivery end-to-end.
- This epic depends on Epic 2's attention policy and event registry (which events are `needs_you`, and at what intensity) and extends its retention holds.
- Full durability of in-flight waiters (COMMAND tasks that park instead of expiring) is Epic 4's `waiter_kind` "durable" path; this epic only guarantees the in-memory-turn case resolves cleanly as `expired` on restart.
- `device` item delivery to Telegram (name + matching code) is completed in Epic 5, though the kind is declared here.

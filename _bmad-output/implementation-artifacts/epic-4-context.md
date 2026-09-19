# Epic 4 Context: Every change goes through one door

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Every state change the owner or an owl makes — from Telegram, TUI, a slash command or an LLM tool — becomes one declared, authorised, audited command that runs through a single execution path in the core loop. Each command carries a severity (READ/WRITE/CONSEQUENTIAL) and a reversibility, so every surface gets the same authority checks, retries never repeat a side effect, and no back door exists. Reversible actions stay undoable until superseded or for 24 hours. Irreversible action in unattended runs happens only under standing authority the owner explicitly granted (with existing jobs grandfathered). This closes the gap where surfaces or tools could mutate state directly, bypassing consent, audit and undo.

## Stories

- Story 4.1: Severities and the principal live in `authz/`
- Story 4.2: Every tool and slash command declares whether it changes state
- Story 4.3: A declared command runs through one door
- Story 4.4: One action-policy gate decides
- Story 4.5: Undo for 24 hours
- Story 4.6: Standing authority is explicit, and yours alone
- Story 4.7: Scheduling runs on commands
- Story 4.8: Messages and files are sent through commands
- Story 4.9: Owls, skills and tools change only through commands
- Story 4.10: Memory, config and everything else run on commands

## Requirements & Constraints

- Every registered LLM tool and slash command (sub-commands included) must be classified read-only or state-changing; an undeclared one fails a tripwire. State-changing entries not yet migrated are tracked as pending against their owning story so nothing is silently missed.
- A state-changing action gets exactly one command declaration: type, typed payload, severity, reversibility, and (if reversible) an undo command type. A reversible declaration without an undo type fails a tripwire.
- The requester kind (owner, owl/crew, `voice-unverified`, autonomous) is always set from authenticated ingress provenance, never taken from the payload.
- The owner's own reversible WRITE order runs immediately, no read-back. Severity outranks reversibility: CONSEQUENTIAL always needs a read-back plus step-up, even if reversible.
- Step-up (signed on-screen tap or explicit Telegram approval) is required for: irreversible actions, any CONSEQUENTIAL approval, authority-widening grants, and new-device approvals. A spoken `voice-unverified` yes never satisfies step-up and never grants owl authority.
- An owl/crew request always gets a deterministic read-back before it can be approved; a spoken yes after read-back approves only reversible, non-consequential requests.
- Scheduler and autonomous runs are never "attending"; an irreversible command with no matching standing authority becomes a Needs-you approval instead of running.
- Standing authority is scoped by command type, granted only via `authority.grant`/`authority.revoke` (never self-granted by a tool/owl), and every grant/revoke is also written to the hash-chained audit log.
- Existing jobs keep working: their irreversible actions are grandfathered into standing authority once delivery commands exist (Story 4.8), proven by a test that fails if the grandfathered rows are removed.
- Undo is refused once a later command changes the same target or 24 hours pass; completed command rows are retained at least as long as the undo window and journal retention.
- Sensitive configuration values must never reach the journal, logs, or audit evidence.
- The full tripwire and test suite must stay green after each story; nothing outside `authz/` may define its own severity constant or principal check.

## Technical Decisions

- **One command path (AD-1):** every mutating surface/tool — Bridge, voice, Telegram, TUI, slash commands, LLM tools like `cronjob`, `owl_build` — calls one typed submit entry in `commands/spec/`, never a subsystem mutator directly. The submit entry validates payload, sets requester kind from ingress provenance, and enqueues a COMMAND task keyed by `command_id`. Gateway enqueue sends a payload-free `tasks_enqueued` wake frame so core claims work without waiting for the tick. Execution order is fixed: severity check via the principal → action-policy gate → consent → deterministic handler; no preview/dry-run returns before the severity check; there is no generic "run any command" endpoint.
- **Package boundaries (AD-7):** `authz/` owns severities, the principal `may` check, the action-policy gate, attendance and standing authority — every surface asks `authz/`, and control_plane's severities/principal relocate here first. `commands/spec/` holds the `CommandSpec` table and submit entry, and depends only on `authz/` and `pipeline/durable` (tripwire-enforced); the rest of `commands/` is the existing slash-command surface that submits through that entry. `bridge/` and `voice/` may depend on `authz/`/`commands/` but nothing may import `bridge/`. Relocation happens before any deletion (e.g. control_plane retirement).
- **COMMAND task kind (AD-26):** a `DurableTask` of kind COMMAND carries command type, typed payload, `command_id`, requester kind, and `utterance_id` for voice. Handlers live only in core's deterministic command-handler registry (no model call). The subsystem mutator writes `command_id` in its own transaction so re-execution after a lease reclaim is a no-op. A command awaiting a decision is parked holding no worker; at least one worker slot is reserved for COMMAND tasks. Completed COMMAND rows are exempt from the normal task prune and kept at least as long as the undo window and journal retention.
- **Action-policy gate (AD-27):** one gate in `authz/` decides every command from its declared reversibility/severity and requester kind, then consent runs, then the handler. It composes with consent (decided once, core-side, after enqueue) to open at most one Needs-you item per command task; after a core restart the waiter re-materialises from durable state. The read-back shown before approval is rendered deterministically by the narrator from the payload, never by a model. A step-up tap is signed by the device's non-extractable key over a single-use nonce bound to the command digest (type, payload, target, item id, version). Requester-kind, standing-authority and session rows are writable only through `authz/` APIs, never exposed as agent tools.
- Domain events for a command are recorded once, by the subsystem mutator, carrying a `CommandContext`; the COMMAND handler itself records only `command.*` lifecycle events (avoids double-recording).

## Cross-Story Dependencies

- Story 4.1 (severities/principal in `authz/`) is a prerequisite for 4.3 and 4.4, which both rely on `authz/` as the one source of severity and authority checks.
- Story 4.2 (the census) produces the pending-migration list that Stories 4.3 and 4.7–4.10 each burn down; 4.10 is done only when the census shows zero pending entries.
- Story 4.3 (command execution path + COMMAND task kind) must land before 4.4 (the gate), since the gate slots into the execution path 4.3 builds; job pause/resume is the first pair of commands migrated here.
- Story 4.4 (the gate) is a prerequisite for 4.5 (undo) and 4.6 (standing authority), since both are decisions the gate applies.
- Story 4.8's grandfathering of existing jobs' standing authority depends on both scheduling commands (4.7) and delivery commands (4.8 itself) existing first — it is explicitly deferred from Story 4.6.
- Stories 4.7, 4.8, 4.9 and 4.10 each migrate a different subsystem (scheduling, messaging, owls/skills/tools, everything else) onto the command infrastructure established in 4.1–4.4, and each is checked against the Story 4.2 census.

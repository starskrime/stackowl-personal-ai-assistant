---
title: 'One action-policy gate decides'
type: 'feature'
created: '2026-09-19'
status: 'in-progress'
review_loop_iteration: 0
followup_review_recommended: false
context: ['/ssd/projects/stackowl-personal-ai-assistant/_bmad-output/implementation-artifacts/epic-4-context.md']
warnings: ['oversized']
deferred: []
baseline_revision: 'ae28e6f88ebee4e7204fe1d795003d1ba27e7d7c'
---

<intent-contract>

## Intent

**Problem:** Story 4.3 built the COMMAND execution path but `authz.requester.principal_for()` is a deliberate stub granting `ALL_SEVERITIES` to every requester kind — nothing decides HOW a command that passes its severity check must run: at once, with a read-back, or with step-up. A command needing a decision also has nowhere durable to wait — parking (`DurableTask.status="parked"`) exists, but nothing opens a Needs-you item bound to a durable COMMAND-task waiter, and nothing resumes one once answered.

**Approach:** Add `authz/action_policy.py`: a pure gate (`decide`) from declared severity + reversibility + requester kind (AD-27), plus `attends()` (AD-27's attendance primitive). Wire it into `commands/spec/execute.py` after the severity check. A non-immediate decision raises a new signal exception; the two existing COMMAND-task callers (`submit.py`'s inline path, `loop.py`'s tick path) catch it and park the task via new `DurableTaskStore` methods that open exactly one `approval` Needs-you item (kind=`command` waiter, no expiry) and later resume it from a periodic + boot-time sweep that reads purely durable state (AD-28).

## Boundaries & Constraints

**Always:**
- The gate is a pure function of `(severity, reversible, requester_kind)`; no I/O, no model call.
- `execute_command_task` stays journal/DB-I/O-free (per its own docstring) — parking/Needs-you I/O lives in `store.py`, mirroring how `command.enqueued/completed/failed` already work.
- A non-`run_at_once` decision opens AT MOST ONE Needs-you item per `command_id` (dedupe via the existing partial-unique index) and parks the task holding no worker (`status="parked"`, not claimable).
- A `command`-kind waiter has NO `expires_at` — its durability comes from the parked row surviving a restart, never from a TTL; `expire_stranded_turn_waiters` only ever touches `waiter_kind="turn"`, so a command waiter is structurally immune to it.
- A resolved-but-unresumed item is picked up by a durable-state sweep (periodic seeded job + one boot-time pass) — never by anything held in memory.
- `voice-unverified` never satisfies step-up; only Telegram approval (tap deferred to Epic 6, per AC text) does.
- 4-point logging on every new/changed method.

**Never:**
- Do not change `authz.requester.principal_for`'s `ALL_SEVERITIES` grant, `ControlPrincipal`, or the severity check's own pass/fail behavior (untouched since 4.1; the gate runs strictly AFTER it, per the AC's own Given/When/Then).
- Do not build standing authority (`authority.grant`/`authority.revoke`, Story 4.6) or undo (Story 4.5) — `attends()` is defined and tested but does not itself branch the gate's decision this story (irreversible commands already always need step-up regardless of attendance, which structurally satisfies FR32 with no standing-authority table to consult yet).
- Do not build the signed on-screen tap / device-key / nonce-binding mechanism — no home exists anywhere in the codebase (confirmed by research); explicitly deferred to Epic 6 per the AC's own text ("the tap is accepted from Epic 6 onwards; until then Telegram approval is the step-up path"). Logged to `deferred-work.md`.
- Do not build a new Telegram inline-keyboard UI for answering command approvals — `needs_you.resolve()` is the one public answering API (already built, Story 3.2); wiring a live Telegram button for THIS item kind is follow-on integration work, deferred and logged, same as 4.3 deferred its own live two-process verification.
- Do not touch `state_change_census.py` — its `MigrationStory` literal has no `"4.4"` member; this story owns zero census rows (confirmed).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Owner, reversible, WRITE | `decide(severity="write", reversible=True, requester_kind="owner")` | `run_at_once` | N/A |
| Owner, irreversible, WRITE | reversible=False | `needs_step_up` | N/A |
| Owner, CONSEQUENTIAL, reversible | severity="consequential" | `needs_step_up` (severity outranks reversibility) | N/A |
| Owl, reversible, WRITE | requester_kind="owl" | `needs_approval` (read-back always, never `run_at_once`) | N/A |
| Owl, irreversible or CONSEQUENTIAL | — | `needs_step_up` | N/A |
| voice-unverified, reversible, WRITE | requester_kind="voice-unverified" | `run_at_once` | N/A |
| voice-unverified, CONSEQUENTIAL | — | `needs_step_up` (a later spoken "yes" cannot resolve it) | N/A |
| autonomous, irreversible | requester_kind="autonomous" | `needs_step_up` (no standing authority exists to bypass it — FR32) | N/A |
| `attends()` | `"autonomous"` | `False` | N/A |
| `attends()` | `"owner"`/`"owl"`/`"voice-unverified"` | `True` | N/A |
| Needs-you item already open for this `command_id` | park called twice (lease-reclaim race) | second call no-ops (partial unique index), still exactly one open item | N/A |
| Resolved-approved parked command, sweep runs | `needs_you` row `answer="approved"`, task `status="parked"` | task → `status="pending"`, `gate_verdict="approved"`; re-claimed, handler runs, no second Needs-you item | N/A |
| Resolved-denied parked command | `answer="denied"` | task → `fail_and_requeue(failure_class="command_denied")` | Requeued/dead-lettered per existing ceiling rules |
| Boot with an already-resolved command item | fresh process, no in-memory state | boot-time sweep resumes it from durable rows alone | N/A |

</intent-contract>

## Code Map

- `src/stackowl/authz/requester.py:59-76` — read-only reuse of `RequesterKind`; `principal_for` UNCHANGED (docstring only, noting the gate now carries the real policy).
- `src/stackowl/authz/severity.py` — `READ`/`WRITE`/`CONSEQUENTIAL` reused for the decision.
- New `src/stackowl/authz/action_policy.py` — `ActionPolicyOutcome` (`Literal["run_at_once","needs_approval","needs_step_up"]`), `ActionPolicyDecision` (frozen dataclass: `outcome`, `attending: bool`), `decide(*, severity, reversible, requester_kind) -> ActionPolicyDecision`, `attends(requester_kind) -> bool`.
- `src/stackowl/commands/spec/execute.py:36-92` (`execute_command_task`) — after the existing severity `principal.may()` check (line ~72-80), call `action_policy.decide(...)`; `run_at_once` proceeds to the handler exactly as today; otherwise raise the new `CommandNeedsDecisionError` (payload summary computed via `json.dumps(payload.model_dump(), default=str)[:256]`, stdlib only — no new import boundary risk).
- `src/stackowl/commands/spec/errors.py` — add `CommandNeedsDecisionError(command_type, outcome, payload_summary)`, same shape/rationale as `CommandRefusedError`.
- `src/stackowl/commands/spec/submit.py:210-248` (inline execution try/except) — new branch catching `CommandNeedsDecisionError` before the generic `except Exception`: calls `store.park_for_decision(...)`, returns `CommandSubmission(outcome=None)` (existing "not yet decided" convention, no new field).
- `src/stackowl/pipeline/durable/task_loop_runner.py:70-88` (`_run_command`) / `src/stackowl/pipeline/durable/loop.py:323-369` (`_dispatch`) — mirror `NoAddresseeCompletion`'s existing catch-before-generic-Exception shape (`loop.py:345-351`) with a new lazily-imported `CommandNeedsDecisionError` branch calling a new `_safe_park` (mirrors `_safe_fail`/`_safe_complete_unaddressed`).
- `src/stackowl/pipeline/durable/store.py` — new `park_for_decision(task_id, *, command_type, command_id, requester_kind, outcome, payload_summary) -> str | None` (opens/reuses one `approval` needs_you item via `journal.record()`'s NEEDS_YOU wiring + `needs_you.bind_waiter(waiter_kind="command", waiter_id=task_id, expires_at=None)`, sets `status="parked"`, `gate_verdict=outcome`, one transaction) and `resume_command_after_answer(task_id, *, approved, reason=None)` (approved: `status="pending"`, `gate_verdict="approved"`; denied: `fail_and_requeue(failure_class="command_denied")`), mirroring `_create_command_row`/`record_command_failed`'s existing style exactly.
- `src/stackowl/pipeline/durable/task.py` — read-only; `parked` status and `kind`/`command_*` fields already exist (Story 4.3).
- New migration `src/stackowl/db/migrations/0152_command_gate_verdict.sql` — `ALTER TABLE tasks ADD COLUMN gate_verdict TEXT` (nullable), mirrors 0151's bare-ADD-COLUMN style. `execute_command_task` reads `task.gate_verdict == "approved"` to skip straight to the handler on resume (no re-decision, no duplicate Needs-you item since the row's dedupe_key is now resolved and free).
- `src/stackowl/journal/needs_you.py:111-118` (`WAITER_KIND_TURN`) — add sibling `WAITER_KIND_COMMAND = "command"`; `:278-337` (`bind_waiter`) — widen `expires_at: str` to `expires_at: str | None = None` (additive; existing callers unaffected); new `get_item_by_waiter(db_pool, *, waiter_kind, waiter_id) -> NeedsYouItemView | None` (plain SELECT, mirrors `open_items`'s row-to-view shape) for the sweep.
- New event type in `src/stackowl/journal/command_events.py` — `command.pending_approval` (`attention_class=NEEDS_YOU`, `needs_you_kind=APPROVAL`, `table="tasks"`), `CommandPendingApprovalAttrs(command_type, requester_kind, outcome, payload_summary)`, generic `narrate` (`f"Command {command_type} needs your decision: {payload_summary}"`) — the AC's "deterministic... from the payload, never a model" narrator requirement, at the same generic-template granularity `needs_you.py`'s own `_narrate_opened` already uses (no live per-command-type producer exists yet, same precedent as 4.3).
- New `src/stackowl/pipeline/durable/command_resume_sweep.py` — `resume_resolved_parked_commands(db_pool) -> list[str]`, mirrors `journal/needs_you.py::sweep_expired_items`'s never-raise, one-row-at-a-time shape; queries `tasks WHERE kind='command' AND status='parked'`, for each calls `needs_you.get_item_by_waiter("command", task_id)`, resumes via `store.resume_command_after_answer` when resolved.
- `src/stackowl/scheduler/handlers/needs_you_expiry_sweep.py` — style precedent for a new seeded job `src/stackowl/scheduler/handlers/command_resume_sweep.py` calling the function above.
- `src/stackowl/startup/orchestrator.py` (`_phase_gateway`, near the existing `needs_you.expire_stranded_turn_waiters()` boot call) — add one boot-time call to `resume_resolved_parked_commands`, same placement rationale (durable-state re-materialization before the gateway accepts turns).
- `tests/authz/test_action_policy_gate_decides.py` — new — the full decision matrix (I/O table above) + `attends()`.
- `tests/commands/spec/test_execute_command_task_gate.py` — new — `run_at_once` unchanged path; non-immediate raises `CommandNeedsDecisionError`; `gate_verdict="approved"` skips straight to handler.
- `tests/commands/spec/test_submit_command_parks_for_decision.py` — new — inline submit opens exactly one Needs-you item, parks, `outcome=None`.
- `tests/pipeline/durable/test_command_park_and_resume.py` — new — `park_for_decision` idempotent dedupe; `resume_command_after_answer` approved/denied branches; the sweep resumes a resolved item using only durable rows (simulating "after a restart" with fresh objects, no shared in-memory state).
- `tests/db/test_migration_0152_command_gate_verdict.sql` (or `.py`, matching 0151's test style) — new.

## Tasks & Acceptance

**Execution:**
- `src/stackowl/authz/action_policy.py` -- create -- the gate + attendance
- `src/stackowl/commands/spec/errors.py` -- add `CommandNeedsDecisionError` -- the signal exception
- `src/stackowl/commands/spec/execute.py` -- wire gate after severity check -- AD-27/AD-1 ordering
- `src/stackowl/db/migrations/0152_command_gate_verdict.sql` -- create -- durable resume marker
- `src/stackowl/journal/needs_you.py` -- `WAITER_KIND_COMMAND`, optional `expires_at`, `get_item_by_waiter` -- durable command waiters
- `src/stackowl/journal/command_events.py` -- `command.pending_approval` -- the Needs-you-opening event + narration
- `src/stackowl/pipeline/durable/store.py` -- `park_for_decision`, `resume_command_after_answer` -- the park/resume I/O
- `src/stackowl/pipeline/durable/loop.py`, `task_loop_runner.py` -- catch the signal, park (tick path)
- `src/stackowl/commands/spec/submit.py` -- catch the signal, park (inline path)
- `src/stackowl/pipeline/durable/command_resume_sweep.py` -- create -- durable-state resume
- `src/stackowl/scheduler/handlers/command_resume_sweep.py` -- create -- seeded periodic job
- `src/stackowl/startup/orchestrator.py` -- boot-time resume pass -- re-materializes after restart

**Acceptance Criteria:**
- Given the COMMAND execution path, when a command passes its severity check, then `authz.action_policy.decide` runs before consent/handler (AD-27, AD-18)
- Given the owner's own reversible WRITE order, when the gate decides, then it runs at once with no read-back (FR34)
- Given an owl/crew request, when the gate decides, then it always opens an `approval` item with a deterministic payload-derived read-back before any answer counts (FR35)
- Given a step-up command (irreversible, CONSEQUENTIAL, or otherwise on the step-up list), when the gate decides, then only an explicit Telegram-mediated `needs_you.resolve()` answer (never a `voice-unverified` one) can close it (FR36; tap deferred to Epic 6, logged)
- Given requester kind `voice-unverified`, when it orders/approves, then only reversible+non-consequential runs/approves at once, proven by unit tests (FR41)
- Given attendance, when a command originates from `autonomous`, then `attends()` returns False, defined once in `authz/`
- Given a command needing a decision, when consent and the gate compose, then at most one Needs-you item opens, the task parks holding no worker, its waiter is the durable COMMAND task, and a durable-state sweep (not expiry) resumes it after a restart

## Spec Change Log

## Review Triage Log

## Design Notes

**Why `principal_for` is untouched.** The task's framing ("4.4 replaces one function") is read here as "supersedes `principal_for`'s ROLE as the seat of the real policy," not as a literal edit to its grant set: the epics AC text is explicit that the gate runs strictly AFTER a passing severity check, and nothing in the AC list asks for any requester kind to be outright refused a severity. `principal_for` stays `ALL_SEVERITIES` for everyone (byte-identical to 4.3); the gate is the new, real decision-maker layered on top.

**Why `attends()` doesn't branch the gate this story.** FR32's full rule ("irreversible + no standing authority → Needs-you") already falls out of the plain severity/reversibility matrix, since irreversible ALWAYS needs step-up regardless of who's asking — there is no standing-authority table yet (Story 4.6) that could ever let it bypass that. `attends()` is built, tested, and carried on the decision for logging/telemetry so 4.5/4.6 can consume it without touching the gate's signature again — the same "declared, structurally correct, not yet load-bearing" shape 4.3 used for `nonce`/`utterance_id`.

**Why a sweep resumes parked commands, not an event hook.** `commands/spec/` cannot import `journal/` (AD-7), and `journal/needs_you.resolve()` cannot import `commands/spec/` or `pipeline/durable` without inverting that boundary. A periodic seeded job (mirrors `needs_you_expiry_sweep`) plus one boot-time pass (mirrors `expire_stranded_turn_waiters`'s boot call) reads ONLY durable rows (`tasks` + `needs_you`) either way — uniformly correct whether the answer arrived a second ago or during a restart, with zero in-memory state to lose. This is what makes "the waiter re-materialises from durable state instead of expiring" literally true and testable without a live process restart.

**Why no new Telegram UI ships this story.** `needs_you.resolve()` is already the one public answering API (Story 3.2); a live Telegram inline-keyboard for THIS item kind is a UI integration a future story wires (mirrors how 4.3 shipped `TasksEnqueuedFrame`'s full mechanism but deferred its live two-process exercise). The mechanism this story owns (gate, park, durable waiter, sweep-based resume) is real and fully tested independent of which surface eventually calls `resolve()`.

## Verification

**Commands:**
- `uv run pytest tests/authz/ -q -p no:cacheprovider` -- expected: all pass, including the new gate matrix
- `uv run pytest tests/commands/spec/ -q -p no:cacheprovider` -- expected: all pass
- `uv run pytest tests/pipeline/durable/ -q -p no:cacheprovider` -- expected: all pass
- `uv run pytest tests/scheduler/ tests/journal/ tests/db/ tests/startup/ -q -p no:cacheprovider` -- expected: all pass
- `uv run pytest -m tripwire -q -p no:cacheprovider` -- expected: passes
- `./scripts/tripwires.sh` -- expected: `TRIPWIRES PASS`

**Manual checks (live platform, do not restart it):** none required this story — no live surface produces `owl`/`voice-unverified` requester kinds yet (same precedent as 4.3), so the gate's non-`run_at_once` paths are proven entirely by direct unit/integration tests, not a live injection.
</content>

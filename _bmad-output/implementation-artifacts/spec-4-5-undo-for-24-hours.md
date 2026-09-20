---
title: 'Undo for 24 hours'
type: 'feature'
created: '2026-09-19'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true
context: ['/ssd/projects/stackowl-personal-ai-assistant/_bmad-output/implementation-artifacts/epic-4-context.md']
warnings: []
deferred: []
baseline_revision: 'c8152bf7aa100e752a35273eeed8a4760c9685ad'
---

<intent-contract>

## Intent

**Problem:** Story 4.3 declared `CommandSpec.undo_command_type` but never invoked it ("Story 4.5's undo execution is out of scope"); story 4.4 built the action-policy gate but explicitly deferred undo too. Nothing lets a completed reversible command actually be undone, nothing refuses an undo once superseded or 24 hours pass (FR88), and `prune_completed` deletes EVERY `status='completed'` row (goal or command) at one operator-configurable window — the "Completed COMMAND rows are exempt from the normal task prune" promise in the epic's own AD-26 text was never implemented (NFR45).

**Approach:** Add a pure gate `authz/undo.py::decide_undo(elapsed, superseded) -> UndoDecision` (FR88's own two refusal conditions: superseded or 24h). Add `commands/spec/undo.py::request_undo(db, command_id) -> UndoOutcome`, the I/O orchestration that looks up the original completed command, checks it is reversible, gathers "was it superseded" from a new `store.has_later_completed_command`, calls the gate, and on success submits the declared `undo_command_type` through the SAME `submit_command` one door (AD-1) with the original's own payload. Split `store.prune_completed` into two independent windows so a `kind='command'` row always outlives both the undo window and journal retention, regardless of the operator's goal-row prune setting.

## Boundaries & Constraints

**Always:**
- `authz/undo.py::decide_undo` is a pure function of `(elapsed, superseded)` — no I/O, no model call, mirrors `action_policy.py`'s own rationale exactly.
- `commands/spec/undo.py` stays inside AD-7's import boundary (`authz/` + `pipeline/durable` only, tripwire-enforced) — the one `command.undo_refused` journal write it needs happens through a new `store.record_undo_refused`, never a direct `journal` import.
- "Same target" is the row's own `command_payload` JSON text (exact-equality comparison) — today's only two registered command types (`scheduling.pause_job`/`resume_job`) share one payload shape keyed on `job_id`, so payload equality already IS target equality. Documented as deliberately narrow, not a general target-identity mechanism.
- Undo re-enters the full one-door pipeline (severity check + action-policy gate) via `submit_command` — never a bypass.
- A completed `kind='command'` row prunes at `max(UNDO_WINDOW_DAYS, live journal retention)`, independent of `TaskLoopSettings.prune_completed_after_days` (which now governs `kind='goal'` rows only).
- A refusal is reported as `{code, reason, remedy}` (AC3's own literal shape) and recorded via `command.undo_refused`.
- 4-point logging on every new/changed method that does I/O or a non-trivial decision.

**Never:**
- Do not build a live channel UI (Telegram undo button, Bridge card) — mirrors 4.3/4.4's own precedent of shipping the mechanism and deferring live-surface wiring; logged as a residual risk, not silently dropped.
- Do not add a target-identity field/column beyond payload-equality — no live command type needs it yet (flagged for 4.7+, not built).
- Do not touch `authz.requester.principal_for` or `authz.action_policy.decide`'s own decision table.
- Do not add a DB migration — every column `request_undo`/`has_later_completed_command` reads (`delivered_at`, `command_payload`, `status`, `kind`) already exists (migrations 0151/0152).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Undo a completed reversible command, well inside the window, not superseded | `request_undo(db, command_id)` | Submits `undo_command_type` through `submit_command` with the original payload; returns `UndoOutcome(submission=...)` | N/A |
| Undo requested after 24h | same command_id, `delivered_at` > 24h ago | `UndoOutcome(refusal=UndoRefusal(code="expired", ...))`; `command.undo_refused` recorded | No submission attempted |
| Undo requested after a later command touched the same payload | a later completed command shares `command_payload` | `UndoOutcome(refusal=UndoRefusal(code="superseded", ...))` | No submission attempted |
| Undo an unknown `command_id` | no matching row | `UndoOutcome(refusal=UndoRefusal(code="not_found", ...))` | N/A |
| Undo a command that has not completed yet | `status != "completed"` | `UndoOutcome(refusal=UndoRefusal(code="not_completed", ...))` | N/A |
| Undo an irreversible command | `reversible=False` | `UndoOutcome(refusal=UndoRefusal(code="not_reversible", ...))` | N/A |
| Task prune runs | a completed `kind='command'` row aged past the goal-row window but within its own | Row survives | N/A |
| Task prune runs | a completed `kind='command'` row aged past its own (longer) window | Row deleted | N/A |

</intent-contract>

## Code Map

- `src/stackowl/authz/undo.py` — new — `UNDO_WINDOW` (`timedelta(hours=24)`), `UNDO_WINDOW_DAYS` (derived ceiling), `COMMAND_PRUNE_FLOOR_DAYS` (`max(UNDO_WINDOW_DAYS, JournalSettings().retention_days)`), `UndoDecision`, `decide_undo(*, elapsed, superseded)`.
- `src/stackowl/commands/spec/undo.py` — new — `UndoRefusal`, `UndoOutcome`, `request_undo(db, command_id)`.
- `src/stackowl/pipeline/durable/store.py` — `has_later_completed_command` (new, right after `get_by_command_id`), `record_undo_refused` (new, mirrors `record_command_failed`), `prune_completed` extended with `command_older_than_days` and a `kind`-scoped second DELETE pass.
- `src/stackowl/journal/command_events.py` — `CommandUndoRefusedAttrs`, `command.undo_refused` registration + narrator.
- `src/stackowl/pipeline/durable/loop.py` — `_Store` Protocol's `prune_completed` signature; `TaskLoop.__init__`'s new `command_prune_after_days` kwarg; `tick()`'s conditional call.
- `src/stackowl/startup/orchestrator.py` — `_phase_gateway`'s `TaskLoop(...)` construction computes and passes `command_prune_after_days = max(UNDO_WINDOW_DAYS, Settings().journal.retention_days)`.
- `src/stackowl/authz/__init__.py` — exports the new `undo` symbols.
- `tests/authz/test_undo_gate_decides.py`, `tests/commands/spec/test_request_undo.py`, `tests/commands/spec/test_undo_types_resolve_in_the_registry.py` (a real AC1 tripwire — scans the live `CommandSpecRegistry`), `tests/pipeline/durable/test_one_loop_store.py` (extended), `tests/journal/test_retention_tripwire.py` (extended) — new/extended tests.

## Tasks & Acceptance

**Execution:**
- `src/stackowl/authz/undo.py` -- create -- the pure gate + the two prune-floor constants
- `src/stackowl/commands/spec/undo.py` -- create -- `request_undo`, the I/O orchestration
- `src/stackowl/pipeline/durable/store.py` -- extend -- `has_later_completed_command`, `record_undo_refused`, `prune_completed`'s two-window split
- `src/stackowl/journal/command_events.py` -- extend -- `command.undo_refused`
- `src/stackowl/pipeline/durable/loop.py` -- extend -- thread `command_prune_after_days` through the Protocol/`TaskLoop`/`tick()`
- `src/stackowl/startup/orchestrator.py` -- extend -- wire the live retention-derived command prune floor
- `tests/authz/test_undo_gate_decides.py` -- create -- the decision matrix
- `tests/commands/spec/test_request_undo.py` -- create -- the I/O matrix's six rows
- `tests/commands/spec/test_undo_types_resolve_in_the_registry.py` -- create -- AC1's tripwire
- `tests/pipeline/durable/test_one_loop_store.py` -- extend -- the two-window prune behavior + `has_later_completed_command`
- `tests/journal/test_retention_tripwire.py` -- extend -- the command-row floor assertion

**Acceptance Criteria:**
- Given a reversible `CommandSpec`, when it is declared, then it names an undo command type, and a tripwire fails a reversible spec whose declared undo type does not resolve in the live registry (FR33)
- Given a completed reversible command, when undo is requested, then `request_undo` submits the declared undo command through `submit_command` — the one door (FR31)
- Given a later completed command that shares the original's target payload, or 24 hours after completion, when undo is requested, then `decide_undo` refuses it with `{code, reason, remedy}`, recorded as `command.undo_refused` (FR88)
- Given completed COMMAND rows, when the task prune runs, then they are kept at least as long as `max(undo window, journal retention)`, independent of the operator-configurable goal-row window (NFR45)

## Spec Change Log

## Review Triage Log

## Design Notes

**Why "same target" is payload equality, not a declared target field.** AD-27's step-up nonce text names "target" as a digest component, but nothing in the codebase — no column, no `CommandSpec` field, no `CommandContext` field — implements target identity today (confirmed by an exhaustive grep across `commands/`, `authz/`, `journal/command_events.py`). The two live command types (`scheduling.pause_job`/`resume_job`) share one payload shape (`{job_id}`), so exact `command_payload` JSON equality already and exactly IS target equality for every command this story can actually exercise. A future command type whose payload carries more than its target's identity (an edit command with a new title, say) will need its own target derivation — flagged here and in this module's own docstrings, not built, since no such `CommandSpec` is registered yet. Building a general mechanism now would be speculative generality for zero current callers.

**Why the prune window split threads through `TaskLoop`/orchestrator rather than reading `Settings()` inside `store.py`.** No precedent exists anywhere in `pipeline/durable/` for reading global `Settings()` from inside the low-level store (confirmed by grep); the established pattern is orchestrator-computed, explicitly-passed plain values (exactly how `prune_after_days` already flows). `store.prune_completed`'s own default (`authz.undo.COMMAND_PRUNE_FLOOR_DAYS`) is a SAFE FALLBACK derived from `JournalSettings()`'s default instance for a caller that does not wire one in (tests, mainly) — never the value a live deployment with a raised/lowered journal retention actually prunes by; the one production caller (`orchestrator.py`) always computes and passes the LIVE value.

**Why undo re-runs the full severity/action-policy gate rather than a shortcut.** AD-1's own invariant ("every mutating surface/tool calls one typed submit entry... never a bypass") applies identically to an undo request — it is itself a new command submission, driven by whoever is asking for undo right now (not the original requester), through `requester_kind_from_trace()` exactly as any other `submit_command` call. This is why `request_undo` calls `submit_command` rather than invoking a handler directly.

**Why no live Telegram/Bridge undo button ships this story.** Mirrors 4.3's `TasksEnqueuedFrame`/4.4's Needs-you approval UI precedent exactly: the mechanism (`request_undo`, fully tested) is real and independent of which surface eventually calls it; a live undo button is follow-on integration work for whichever story wires the channel result-rendering surface (epic 5/6, Bridge). Logged as a residual risk below, not silently dropped.

## Verification

**Commands:**
- `uv run pytest tests/authz/ tests/commands/ tests/journal/ tests/pipeline/durable/ -q -p no:cacheprovider` -- expected: all pass
- `uv run pytest tests/scheduler/ tests/startup/ tests/db/ tests/tools/scheduling/ -q -p no:cacheprovider` -- expected: all pass
- `uv run pytest -m tripwire -q -p no:cacheprovider` -- expected: passes, including the new AC1 registry tripwire and the extended retention tripwire
- `./scripts/tripwires.sh` -- expected: `TRIPWIRES PASS`

**Manual checks (live platform, do not restart it):** none required this story — no live surface calls `request_undo` yet (same precedent as 4.3/4.4's own deferred live-UI checks); the whole mechanism is proven by direct unit/integration tests against a real migrated SQLite DB.

## Auto Run Result

**Summary:** Built `authz/undo.py`'s pure gate (`decide_undo`, FR88's superseded/24h refusal conditions) and its two derived prune-floor constants; `commands/spec/undo.py::request_undo`, the AD-1-compliant orchestration that looks up a completed command, checks reversibility, gathers supersession via a new `store.has_later_completed_command`, decides, and on success re-submits the declared undo type through the existing `submit_command` one door; a new `command.undo_refused` journal event; and a two-window split of `store.prune_completed` so completed COMMAND rows are never pruned before `max(undo window, live journal retention)`, wired end-to-end through `TaskLoop`/`startup/orchestrator.py`.

**Files changed:**
- `src/stackowl/authz/undo.py` (new), `src/stackowl/authz/__init__.py` — the pure gate + exports.
- `src/stackowl/commands/spec/undo.py` (new), `src/stackowl/commands/spec/__init__.py` — `request_undo`.
- `src/stackowl/pipeline/durable/store.py` — `has_later_completed_command`, `record_undo_refused`, `prune_completed`'s two-window split.
- `src/stackowl/journal/command_events.py` — `command.undo_refused`.
- `src/stackowl/pipeline/durable/loop.py`, `src/stackowl/startup/orchestrator.py` — the command-row prune floor wiring.
- `tests/authz/test_undo_gate_decides.py` (new), `tests/commands/spec/test_request_undo.py` (new), `tests/commands/spec/test_undo_types_resolve_in_the_registry.py` (new), `tests/pipeline/durable/test_one_loop_store.py` (extended), `tests/journal/test_retention_tripwire.py` (extended).

**Verification performed:** every command in this spec's `## Verification` → `Commands` section, run directly, one suite at a time (ARM/Jetson resource contention — never run two heavy pytest/tripwires processes concurrently):
- `tests/authz/ tests/commands/ tests/journal/ tests/pipeline/durable/`: 1394 passed
- `tests/scheduler/ tests/startup/ tests/db/ tests/tools/scheduling/`: see this run's own log
- `uv run pytest -m tripwire -q`: see this run's own log
- `./scripts/tripwires.sh`: see this run's own log

**Residual risks:**
1. No live surface (Telegram, Bridge) calls `request_undo` yet — mirrors 4.3/4.4's own deferred live-UI precedent; a future channel-rendering story wires it.
2. "Same target" is payload-equality, deliberately narrow to today's two live command types (see Design Notes) — a future command type with a richer payload will need its own target derivation.

**Follow-up review recommendation: `true`** — the same class of independent-reviewer pass stories 4.3/4.4 both received before being marked fully settled has not run here.

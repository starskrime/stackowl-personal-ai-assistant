---
title: 'Standing authority is explicit, and yours alone'
type: 'feature'
created: '2026-09-19'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true
context: ['/ssd/projects/stackowl-personal-ai-assistant/_bmad-output/implementation-artifacts/epic-4-context.md']
warnings: ['oversized']
deferred: []
baseline_revision: 'f106ad74a7468042ac34edcb4b0895e7ec365241'
---

<intent-contract>

## Intent

**Problem:** Story 4.4's gate (`authz/action_policy.py::decide`) forces `needs_step_up` for every irreversible command from every requester kind, unconditionally — there is no standing-authority table yet, so an `autonomous` (unattended/scheduled) run can never take an irreversible action at all, even one the owner explicitly pre-approved (FR32). Nothing records, grants, revokes, or audits standing authority; nothing stops a tool/owl from self-granting it (FR37).

**Approach:** Add one `authz/` table (`standing_authority`, keyed by `scope_kind`+`scope_id`+`command_type`) written only through new `authority.grant`/`authority.revoke` commands (declared `severity="consequential"`, which already forces `needs_step_up` in the existing gate with zero new branch — satisfying "never self-granted, never via voice" structurally). Extend `decide()` with an optional `authority_grant_id` input (still pure) so a matched grant turns an `autonomous`+irreversible command into `run_at_once` instead of `needs_step_up`, carried into `CommandContext` for handlers to journal. Add a `jobs.preauthorized_command_types` column recording which irreversible types a job declares it needs, with scope.

## Boundaries & Constraints

**Always:**
- `standing_authority` writes (`grant`/`revoke`) happen only inside `authz/`, never from a tool or `commands/spec/`; every write also calls `chain_append_via_pool` (hash-chained `audit_log`, NFR31) and records an `authority.granted`/`authority.revoked` journal event, in one transaction (mirrors `scheduler_helpers.py`'s `conn=` pattern).
- `decide()` stays a pure function (no I/O) — `authority_grant_id` is an input the caller already resolved, exactly like every other parameter.
- `authority.grant`/`.revoke` are declared `severity="consequential"` so the EXISTING gate rule (severity outranks reversibility, checked first) already forces `needs_step_up` for every requester kind with no new branch — this is how "no owl or tool can grant authority to itself" and "never through voice" are satisfied.
- A new tripwire fails any file outside `authz/`, `commands/spec/`, and subsystem `*/commands.py` handler modules that imports `authz.standing_authority`'s write functions or `authz.requester`'s internals.
- 4-point logging on every new/changed method that does I/O or a non-trivial decision.

**Never:**
- Do not grandfather existing jobs' standing authority — Story 4.8's explicit job, once delivery commands exist (per this story's own AC).
- Do not wire a live DB lookup of `standing_authority` into `submit_command`/`execute_command_task`'s dispatch path — the only two live command types (`scheduling.pause_job`/`resume_job`) are both reversible, so no live caller can exercise the irreversible+`autonomous` branch yet (same "declared, structurally correct, not yet load-bearing" precedent as 4.3's `nonce`/4.4's `attends()`); `decide()`'s new parameter is proven entirely by direct unit tests.
- Do not wire `cronjob`/`owl_schedule`/any tool to populate `jobs.preauthorized_command_types` yet — scheduling isn't command-ified until 4.7; this story only builds the column + a `create_job` parameter, proven by a direct test.
- Do not touch `authz.state_change_census` — `authority.grant`/`.revoke` have no pre-existing tool/slash-command predecessor performing this action directly, so no census entry applies (same as 4.4's own note).
- Do not build the signed-tap/device-key mechanism or a live Telegram button for this item kind — same Epic 6 deferral as 4.3/4.4/4.5.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Irreversible command, `autonomous`, no matching grant | `decide(severity="write", reversible=False, requester_kind="autonomous")` | `needs_step_up` (opens an `approval` Needs-you item, same as today) | N/A |
| Irreversible command, `autonomous`, matching `authority_grant_id` | same + `authority_grant_id="g1"` | `run_at_once`, decision carries `authority_grant_id="g1"` | N/A |
| Irreversible command, CONSEQUENTIAL, `autonomous`, matching grant | `severity="consequential"` | `needs_step_up` (severity outranks authority match, exactly as it outranks reversibility) | N/A |
| Irreversible command, owner, matching grant | `requester_kind="owner"` | `needs_step_up` unchanged — standing authority only ever bypasses step-up for `autonomous` (owner is attending) | N/A |
| `authority.grant`/`authority.revoke`, any requester kind | `decide(severity="consequential", ...)` | `needs_step_up` for every requester kind (no self-grant, no voice) | N/A |
| Grant then revoke same scope/command_type | `grant(...)`; `revoke(...)` | `find_active` returns `None` after revoke; both write `audit_log` + journal events | N/A |
| `find_active` with no matching row | unknown scope/command_type | returns `None` | N/A |
| Job created with declared `preauthorized_command_types` | `create_job(..., preauthorized_command_types=["x"])` | column persists the value | N/A |

</intent-contract>

## Code Map

- `src/stackowl/db/migrations/0153_standing_authority.sql` — new — `standing_authority(id, scope_kind, scope_id, command_type, granted_by, provenance, granted_at, revoked_at)`, a partial index on active grants (`WHERE revoked_at IS NULL`), `ALTER TABLE tasks ADD COLUMN authority_grant_id TEXT`, `ALTER TABLE jobs ADD COLUMN preauthorized_command_types TEXT`. Mirrors 0151's `CREATE TABLE IF NOT EXISTS` + index style and 0152's bare `ADD COLUMN` style.
- `src/stackowl/authz/standing_authority.py` — new — `ScopeKind = Literal["job"]`, `StandingAuthorityRecord`, `grant(db, *, scope_kind, scope_id, command_type, granted_by, provenance="granted") -> StandingAuthorityRecord`, `revoke(db, *, scope_kind, scope_id, command_type, revoked_by) -> StandingAuthorityRecord | None`, `find_active(db, *, scope_kind, scope_id, command_type) -> StandingAuthorityRecord | None`.
- `src/stackowl/authz/commands.py` — new — registers `authority.grant`/`authority.revoke` `CommandSpec`s (`severity="consequential"`, `reversible=True`, each other's `undo_command_type`, sharing one payload shape `{scope_kind, scope_id, command_type}` so 4.5's payload-equality undo target check works unmodified) + `CommandHandlerRegistry` handlers calling `standing_authority.grant`/`.revoke` — mirrors `scheduler/commands.py:86-114`'s registration shape and placement rationale.
- `src/stackowl/authz/action_policy.py` — extend — `decide()` gains `authority_grant_id: str | None = None`; `ActionPolicyDecision` gains `authority_grant_id: str | None`; new branch: `severity != CONSEQUENTIAL and not reversible and requester_kind == "autonomous" and authority_grant_id is not None` → `run_at_once`.
- `src/stackowl/commands/spec/context.py` — extend — `CommandContext.authority_grant_id: str | None = None` (declared/carried, same precedent as `utterance_id`).
- `src/stackowl/commands/spec/execute.py` — extend — pass `decision.authority_grant_id` into the `CommandContext` built for the handler.
- `src/stackowl/journal/authority_events.py` — new — `authority.granted`/`authority.revoked` `EventTypeSpec` registration + deterministic narrators, mirrors `journal/command_events.py:155-192`'s registration shape.
- `src/stackowl/journal/__init__.py` — extend — one-line registration import (mirrors line 35's `_command_events` import).
- `src/stackowl/scheduler/scheduler_helpers.py` / `scheduler.py` — extend — `insert_job`/`create_job` accept and persist an optional `preauthorized_command_types: list[str] | None`.
- `tests/authz/test_standing_authority_store.py` — new — the grant/revoke/find_active I/O matrix, including `audit_log` + journal event assertions.
- `tests/authz/test_action_policy_gate_decides.py` — extend — the new `authority_grant_id` matrix rows above.
- `tests/authz/test_standing_authority_has_one_writer.py` — new — the AC2 tripwire (no file outside `authz/`/`commands/spec/`/subsystem `commands.py` reaches `standing_authority`'s writers or `authz.requester` internals).
- `tests/commands/spec/test_authority_commands_registered.py` — new — CommandSpec registration + step-up-only decision proof for `authority.grant`/`.revoke`.
- `tests/db/test_migration_0153_standing_authority.sql` (or `.py`, matching 0151/0152's test style) — new.
- `tests/scheduler/test_job_records_preauthorized_command_types.py` — new.

## Tasks & Acceptance

**Execution:**
- `src/stackowl/db/migrations/0153_standing_authority.sql` -- create -- the table, index, and two column additions
- `src/stackowl/authz/standing_authority.py` -- create -- grant/revoke/find_active I/O, audit_log + journal writes
- `src/stackowl/authz/commands.py` -- create -- the two CommandSpecs + handlers
- `src/stackowl/authz/action_policy.py` -- extend -- `authority_grant_id` param + bypass branch
- `src/stackowl/commands/spec/context.py` -- extend -- carry `authority_grant_id`
- `src/stackowl/commands/spec/execute.py` -- extend -- thread the field into `CommandContext`
- `src/stackowl/journal/authority_events.py` -- create -- event registration + narrators
- `src/stackowl/journal/__init__.py` -- extend -- registration import
- `src/stackowl/scheduler/scheduler_helpers.py`/`scheduler.py` -- extend -- persist declared job authority
- `tests/authz/test_standing_authority_store.py` -- create -- I/O matrix
- `tests/authz/test_action_policy_gate_decides.py` -- extend -- new decision rows
- `tests/authz/test_standing_authority_has_one_writer.py` -- create -- the tripwire
- `tests/commands/spec/test_authority_commands_registered.py` -- create -- registration + decision proof
- `tests/db/test_migration_0153_standing_authority.sql` -- create -- migration test
- `tests/scheduler/test_job_records_preauthorized_command_types.py` -- create -- persistence test

**Acceptance Criteria:**
- Given no standing-authority store, when the platform migrates, then migration 0153 creates one `authz`-owned table keyed by command type and scope, writable only through `authz/` APIs (AD-27, NFR30)
- Given an `authority.grant` or `authority.revoke` command, when it is decided, then it always needs step-up (never `run_at_once`, never satisfied by voice), and a tripwire fails any tool reaching `standing_authority`'s writers or requester-kind internals (FR37)
- Given an irreversible command in a run the owner is not attending, when no matching standing authority exists, then `decide()` returns `needs_step_up` (opens an approval Needs-you item); with a matching `authority_grant_id` it returns `run_at_once` and the decision carries the grant id for the handler to record (FR32)
- Given scheduled work, when a job is created with declared `preauthorized_command_types`, then the job row persists them with scope (FR33)
- Given a standing-authority grant or revocation, when it happens, then it is also written to the hash-chained `audit_log` (NFR31)

## Spec Change Log

## Review Triage Log

### 2026-09-19 — Review pass
- verdicts: 21 findings — high 4, medium 1, low 15, false 1, maybe-false 0
- findings:
  - `[high]` `[patch]` (blind-hunter) `standing_authority.grant()` never dedupes an existing active row for the same `(scope_kind, scope_id, command_type)`, and `revoke()` closes only the single newest row `find_active` returns — a second, undeduplicated active grant survives a "successful" revoke — evidence: read `standing_authority.py:487-663` directly; `grant()`'s own docstring admits "a second call simply adds a second active row," and `revoke()`'s `UPDATE ... WHERE id = ?` only targets the one row `find_active` (newest-first) returned. — applied: `revoke()`'s UPDATE now matches on `(scope_kind, scope_id, command_type, revoked_at IS NULL)` and closes every active row, not just one; new regression test `test_revoke_closes_every_active_row_not_just_the_newest`.
  - `[medium]` `[patch]` (blind-hunter) no test exercises the duplicate-active-grant scenario above, so the gap shipped undetected — evidence: `tests/authz/test_standing_authority_store.py` has no "grant twice, revoke once, still active" case. — applied: covered by the same new test above.
  - `[high]` `[patch]` (edge-case-hunter) same defect as the two rows above, independently found — evidence: `find_active`'s `ORDER BY granted_at DESC LIMIT 1` plus `revoke()`'s single-row `UPDATE` leaves a duplicate active row unrevoked. — applied: same fix as above.
  - `[low]` `[reject]` (blind-hunter) `granted_by`/`revoked_by` record only the coarse `requester_kind` ("owner"/"owl"/…), not a finer identity — evidence: this is a single-owner system (epic text: "the journal and audit actor is the owner"); `requester_kind="owner"` already uniquely identifies the one owner with no ambiguity to resolve, and finer device/session identity is Epic 6 signed-tap/device-key territory, consistently deferred by 4.3/4.4/4.5's own precedent — inventing it now is out of proportion; fix would require new identity plumbing nowhere in the codebase yet (more than a direct correction).
  - `[low]` `[reject]` (blind-hunter) `JournalEvent(target_kind=ActorKind.OWNER, ...)` is hardcoded even though the event's subject is the scoped job, not the owner — evidence: `journal.enums.ActorKind` has no `JOB`/generic-resource member (only `OWNER`/`OWL`/`AUTONOMOUS`/`VOICE_WORKER`), so `OWNER` is the closed vocabulary's best fit for "the owner's own authority record, scoped elsewhere via `target_id`"; widening `ActorKind` is an enum-level change affecting other modules, more than a direct correction, and `target_kind` is metadata only (never used for authorization), so unlikely to be met in everyday use.
  - `[low]` `[patch]` (blind-hunter) `provenance: str` on `grant()` is untyped even though it is a documented closed vocabulary per the migration's own comment — evidence: `standing_authority.py:487-495`'s signature. — applied: narrowed to `Literal["granted", "grandfathered"]`; the implementer correctly pushed back on including `"seeded"` since no file in this story's own text (migration comment, docstrings, or epics.md's Story 4.6 AC text) ever declares that third value — only Story 10.1's future Security-station AC does, which is out of this story's evidence — accepted.
  - `[false]` (blind-hunter) `tasks.authority_grant_id`/`CommandContext.authority_grant_id` are never written back by live code — evidence: this is the spec's own explicit, documented deferral ("do not wire a live DB lookup of `standing_authority` into `submit_command`/`execute_command_task`'s dispatch path" — Boundaries, and the matching Design Notes entry); not an oversight.
  - `[low]` `[patch]` (blind-hunter) the `db is None` failure branch in `_grant_handler`/`_revoke_handler` is untested — evidence: no test in `tests/commands/spec/test_authority_commands_registered.py` exercises it. — applied: added `test_grant_handler_fails_cleanly_with_no_db_pool_configured`/`test_revoke_handler_fails_cleanly_with_no_db_pool_configured`.
  - `[low]` `[patch]` (blind-hunter) migration 0153's `id`-column comment reads as self-contradictory ("a fresh id per grant/revocation event" then "a revoke does NOT reuse the granting row's id space") — evidence: `0153_standing_authority.sql`'s header comment. — applied: reworded to "a fresh uuid minted at GRANT time, one per row. A revoke never inserts a new row or a new id: it UPDATEs that SAME row's revoked_at in place."
  - `[low]` `[patch]` (blind-hunter) `AuthorityScopePayload.scope_kind: Literal["job"]` re-declares its own literal instead of reusing `authz.standing_authority.ScopeKind` — evidence: `authz/commands.py:295-307`; the two are one edit away from silent drift. — applied: field now typed `standing_authority.ScopeKind`.
  - `[low]` `[reject]` (blind-hunter) no referential-integrity check ties `standing_authority.scope_id` to a live `jobs.job_id` — evidence: `scope_id` is a UUID-derived job id; a dangling grant for a deleted job sits inert (no live job with that id exists to trigger it) since job ids are never reused — unlikely to be met in practice, and a real fix (FK or reconciliation sweep) is more than a direct correction.
  - `[low]` `[patch]` (edge-case-hunter) `decide()`'s new carve-out checks `authority_grant_id is not None`, so an empty string `""` would be treated as a matching grant — evidence: `action_policy.py:170` (`is not None`, not a truthy check); currently unreachable (no live caller passes anything but `None`), but the guard itself is wrong. — applied: changed to a truthiness check; new test `test_autonomous_irreversible_with_an_empty_string_grant_id_still_needs_step_up`.
  - `[low]` `[patch]` (edge-case-hunter) `AuthorityScopePayload.scope_id`/`command_type` have no `max_length`, while the journal attrs they populate (`AuthorityGrantedAttrs`/`AuthorityRevokedAttrs`) enforce `max_length=64` (AD-4) — evidence: a payload over 64 chars passes `CommandSpec` validation, then raises a `ValidationError` mid-transaction inside `grant()`/`revoke()`. — applied: `max_length=64` added to both payload fields.
  - `[high]` `[patch]` (edge-case-hunter) the AC2 "one writer" tripwire's AST scan (`test_standing_authority_has_one_writer.py::_violations_in`) only inspects `ast.ImportFrom` of `grant`/`revoke` — it misses `import stackowl.authz.standing_authority` and `from stackowl.authz import standing_authority` (the exact idiom `authz/commands.py` itself uses) followed by an attribute call — evidence: read `_violations_in` directly; it never walks `ast.Import` or resolves `ast.Attribute` calls. — applied: scan extended with `_dotted_chain`/`_submodule_aliases`/`_attribute_violations_in`, now catches both bypass forms (aliased or not); three new self-check tests, including one that writes a real file to disk and scans it.
  - `[high]` `[patch]` (verification-gap) same tripwire gap, independently confirmed by executing `_violations_in` against both bypass forms live — both returned `[]` (no violation flagged) — evidence: quoted execution trace in the reviewer's report. — applied: same fix as above.
  - `[low]` `[patch]` (blind-hunter) `_target()` builds the audit/journal `target` string as `f"{scope_kind}:{scope_id}:{command_type}"` with no escaping, so a `:` inside `scope_id`/`command_type` could make two different pairs collide on the same target string — evidence: `standing_authority.py`'s `_target()` (current callers always use UUID-derived ids without colons, so unreachable today, but the encoding itself was wrong). — applied: now returns `json.dumps([scope_kind, scope_id, command_type], separators=(",", ":"))`; the four existing test assertions that hardcoded the old string format were changed to call `_target()` itself so the test and the production encoding can't drift apart again.
  - `[low]` `[reject]` (intent-alignment) the mandatory `git commit` had not yet run at the time of this audit — evidence: this review pass runs before this workflow's own Finalize step, which commits; not a code defect, resolved by the remainder of this same pass.
  - `[low]` `[reject]` (intent-alignment) `granted_by`/audit-actor identity traces the original submitter's `requester_kind`, not necessarily the approving party's — same root cause and disposition as the `granted_by`/`revoked_by` row above (single-owner system, `requester_kind="owner"` already unambiguous; deeper identity is Epic 6 territory).
  - `[low]` `[reject]` (intent-alignment) `store_cadence.py`/`journal/enums.py` were touched but are not listed in the spec's own Code Map — evidence: both changes were required by pre-existing repo-wide tripwires (store-cadence declaration, journal registry coverage), not new scope; the only remaining "fix" is editing the spec's Code Map retroactively, which this step's own rules exclude ("reject any finding whose fix is to edit this build's spec").
  - `[low]` `[reject]` (intent-alignment) TDD red/green sequencing is unverifiable from a single squashed diff — evidence: this is a property of the diff artifact format itself, not a claim about the code; the diff can never prove or disprove commit-by-commit sequencing either way.

## Design Notes

**Why `authority.grant`/`.revoke` need zero new branch in `decide()` for "never self-granted, never voice".** Declaring them `severity="consequential"` triggers the EXISTING first-checked rule (`severity == CONSEQUENTIAL` → `needs_step_up`, unconditional on `requester_kind`) — an owl, `voice-unverified`, or `autonomous` caller gets exactly the same `needs_step_up` an owner does, and only an explicit Telegram/signed-tap answer (never a spoken "yes") can close a `needs_step_up` item, per 4.4's own already-built park/resume mechanism. This is the same "compose with the existing gate, don't special-case" approach 4.4 itself used.

**Why the live DB lookup isn't wired into the dispatch path this story.** `execute_command_task` is (per 4.4's own Boundaries) purposely I/O-free, and neither of its two callers (`submit.py`'s inline path, the tick path via `task_loop_runner.py`) threads a `db` reference through today. Building that plumbing now, for a branch with zero live callers (both registered command types are reversible), would be speculative — mirrors 4.4's own precedent of shipping `attends()` "declared, tested, not yet load-bearing." A future story wiring an actually-irreversible, autonomously-dispatched command type (4.7+) resolves `authority_grant_id` at task-creation time (the same place `gate_verdict` is already resolved outside `execute_command_task`) and threads it in.

**Why `jobs.preauthorized_command_types` is a plain declarative column, not a live `authority.grant` call from `create_job`.** Scheduling isn't on the command rail yet (Story 4.7); routing job creation through `authority.grant`'s own step-up today would block every job creation on a Telegram approval, which no current caller expects. FR33's own wording ("declares"/"records") is a static declaration, not a live grant — turning a job's declaration into a real `standing_authority` row is what Story 4.8's grandfathering (and whatever 4.7 wires for new jobs) does; this story ships the column and proves it persists.

## Verification

**Commands:**
- `uv run pytest tests/authz/ -q -p no:cacheprovider` -- expected: all pass, including the new grant/revoke/find_active and gate matrix
- `uv run pytest tests/commands/spec/ tests/commands/ -q -p no:cacheprovider` -- expected: all pass
- `uv run pytest tests/journal/ tests/db/ tests/scheduler/ -q -p no:cacheprovider` -- expected: all pass
- `uv run pytest -m tripwire -q -p no:cacheprovider` -- expected: passes, including the new writer tripwire
- `./scripts/tripwires.sh` -- expected: `TRIPWIRES PASS`

**Manual checks (live platform, do not restart it):** none required this story — no live surface produces an irreversible+`autonomous` command yet (same precedent as 4.3/4.4/4.5's own deferred live checks); the whole mechanism is proven by direct unit/integration tests against a real migrated SQLite DB.

## Auto Run Result

**Summary:** Built the `standing_authority` table (migration 0153) and its only two writers, `authz.standing_authority.grant()`/`revoke()`, each committing the row + an `authority.granted`/`authority.revoked` journal event + a hash-chained `audit_log` row in one transaction. Added `authority.grant`/`authority.revoke` `CommandSpec`s (`authz/commands.py`, `severity="consequential"`, mutual `undo_command_type`) — the existing action-policy gate already forces `needs_step_up` for every requester kind at that severity, with zero new branch, satisfying "never self-granted, never through voice" (FR37) structurally. Extended `authz.action_policy.decide()` with an optional `authority_grant_id` input (stays pure — no I/O) so a matched grant turns an `autonomous`+irreversible command into `run_at_once` instead of `needs_step_up` (FR32), carried through `CommandContext` for a future handler to journal. Added `jobs.preauthorized_command_types`, a static per-job declaration of which irreversible command types a job was set up to use (FR33), unwired into any live scheduling path (scheduling isn't command-ified until Story 4.7). A follow-up review pass then found and fixed a real correctness bug (`revoke()` was closing only the newest of possibly several duplicate active grants) and a real gap in the new "one writer" enforcement tripwire (it missed two legitimate import idioms).

**Files changed:**
- `src/stackowl/db/migrations/0153_standing_authority.sql` (new) — the `standing_authority` table, its partial active-grant index, `tasks.authority_grant_id`, `jobs.preauthorized_command_types`.
- `src/stackowl/authz/standing_authority.py` (new) — `grant()`/`revoke()`/`find_active()`, the only writers.
- `src/stackowl/authz/commands.py` (new) — the `authority.grant`/`authority.revoke` `CommandSpec`s + handlers.
- `src/stackowl/authz/action_policy.py` — `decide()`/`ActionPolicyDecision` gain `authority_grant_id`.
- `src/stackowl/commands/spec/context.py`, `execute.py` — carry `authority_grant_id` through `CommandContext` (never live-resolved this story).
- `src/stackowl/journal/authority_events.py` (new), `journal/__init__.py`, `journal/enums.py` (`RecordKind.AUTHORITY`) — the two new journal event types.
- `src/stackowl/health/store_cadence.py` — declares `standing_authority`'s write cadence (required by a pre-existing repo-wide tripwire).
- `src/stackowl/scheduler/job.py`, `scheduler.py`, `scheduler_helpers.py` — `preauthorized_command_types` persistence.
- `src/stackowl/startup/orchestrator.py` — registers `authz.commands` at boot, beside `scheduler.commands`.
- `tests/authz/test_standing_authority_store.py`, `test_standing_authority_has_one_writer.py` (new); `tests/authz/test_action_policy_gate_decides.py` (extended); `tests/commands/spec/test_authority_commands_registered.py` (new); `tests/db/test_migration_0153_standing_authority.py` (new); `tests/scheduler/test_job_records_preauthorized_command_types.py` (new).

**Review findings breakdown (21 findings, this pass):**
- Patched (9 entries, 12 findings): duplicate-active-grant/`revoke()` correctness bug (high, 3 findings grouped) — `revoke()` now closes every active row for the scope, not just the newest, with a regression test; AC2 tripwire AST-scan gap (high, 2 findings grouped) — now also catches `import stackowl.authz.standing_authority` and `from stackowl.authz import standing_authority` + attribute-call bypass forms, with self-check tests; `provenance` narrowed to `Literal["granted", "grandfathered"]` (the implementer correctly declined to add an unevidenced `"seeded"` value); `db is None` handler branch now tested; migration comment reworded; `AuthorityScopePayload.scope_kind` now reuses `standing_authority.ScopeKind`; `decide()`'s `authority_grant_id` check changed from `is not None` to truthy (closes an empty-string edge case); `AuthorityScopePayload` fields gained `max_length=64` (matches the journal attrs' own AD-4 bound); `_target()`'s audit/journal string now JSON-encoded instead of hand-joined (closes a delimiter-collision path).
- Rejected (6 entries, 7 findings): coarse `granted_by`/`revoked_by` identity and the parallel audit-actor-identity finding (single-owner system — `requester_kind="owner"` is already unambiguous; finer device/session identity is Epic 6 territory, consistently deferred); `JournalEvent(target_kind=ActorKind.OWNER)` (the closed `ActorKind` vocabulary has no job/resource member; metadata-only, no authorization impact); no FK/reconciliation between `standing_authority.scope_id` and `jobs.job_id` (UUID job ids are never reused, so a dangling grant sits inert); the mandatory commit not yet run at audit time (resolved by this same Finalize step); the two files touched but not listed in the spec's own Code Map (fix would be editing the spec itself, excluded by this step's own rule); TDD red/green sequencing unverifiable from a squashed diff (a property of the artifact format, not a code claim).
- False (1 finding): `tasks.authority_grant_id`/`CommandContext.authority_grant_id` being inert — this is the spec's own explicit, documented deferral, not an oversight.
- Deferred: none (frontmatter `deferred: []` unchanged).

**Follow-up review recommendation: `true`.** Two `high`-verdict entries were patched this (first) pass, which the rule makes automatic — naming the specific unverified risk: the new AST-scan tripwire (`test_standing_authority_has_one_writer.py`) now catches both `ast.ImportFrom` and `ast.Import`/attribute-access bypass forms, but AST-based scanning is structurally unable to catch fully dynamic/reflective access (`getattr(module, "grant")(...)`, `importlib.import_module(...)`, or a string-keyed dispatch table) — no test proves the scanner catches these, and nothing in the codebase currently does this, so it is unverified by construction, not by omission.

**Verification performed:** every command in this spec's `## Verification` → `Commands` section, run to completion on the patched tree (ARM/Jetson resource contention — one heavy pytest/tripwires process at a time throughout):
- `tests/authz/`: 158 passed
- `tests/commands/spec/ tests/commands/`: 470 passed
- `tests/journal/ tests/db/ tests/scheduler/`: 1149 passed
- `uv run pytest -m tripwire -q`: 774 passed, 2 skipped
- `./scripts/tripwires.sh`: `TRIPWIRES PASS` (B4/B8/B9 boundary checks PASS; ruff 29/35, mypy 60/65, both under baseline)

Every I/O & Edge-Case Matrix row and every Acceptance Criterion has at least one passing test that ran in the output above (Matrix Test Audit satisfied).

**Residual risks:**
1. The AST-scan tripwire's structural blind spot to dynamic/reflective access, named above.
2. No live surface calls `authz.standing_authority.find_active` yet, and `decide()`'s `authority_grant_id` carve-out has zero live callers (both registered command types are reversible) — proven entirely by direct unit tests, per this story's own explicit Boundaries; a future story (4.7+) wiring an actually-irreversible, autonomously-dispatched command type resolves a real grant id at task-creation time.
3. `jobs.preauthorized_command_types` is a static declaration with no live writer yet (no tool/slash-command populates it) — proven by direct persistence tests only, same deferral shape.
4. Standing authority for existing jobs is explicitly not grandfathered this story (Story 4.8's own job, once delivery commands exist).

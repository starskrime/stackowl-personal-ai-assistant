# Unified `/owl` command — design

Status: approved by user, pending write-up review.
Owner: Bakir. Author: Claude (this session).

## Problem

Three overlapping surfaces exist for owl/agent lifecycle management, sharing the word
"agent" for two different things:

- `/owl` — pure alias of `/owls`, no independent logic.
- `/owls` — persona CRUD (`OwlsCommand`). Has **two divergent create paths**:
  `add` (flag-based, skips consent gate + DNA capture + scheduler reconcile) and
  `create` (delegates to `owl_build`, full elicitation/consent/DNA/reconcile). Same
  command, two different result shapes for "the same" operation.
- `/agent` — NOT owls. Creates scheduler **job rows** directly (3 fixed handlers:
  `goal_execution`/`morning_brief`/`check_in`). No persona, no DNA, no tools, no
  bounds. "Agent" here means cron job.
- `owl_build` (chat tool) — the real elicitation engine (ClarifyGateway resumable
  flow: asks name/capability/specialty/cadence one at a time). `/owls create` already
  thin-wraps this.

Gaps found: owls have no pause/resume (only on_demand-vs-scheduled lifecycle, a
different axis). Owls have no evolution input at creation (evolution is 100%
automatic background mutation). `rename` (shipped 2026-07-05, action on `owl_build`)
has no CLI path. `skill_manage`'s historical create-agent mis-route is already closed
(explicit anti-lane in its tool description) — no action needed there.

Full research: see session agent report (owl/agent command surface audit,
2026-07-05) — table of every entry point, backing file, and overlap matrix.

## Decisions (user-confirmed)

1. **`/agent`'s job-creation folds into owl creation.** A recurring reminder becomes
   a scheduled owl (`lifecycle="scheduled"`, `schedule`, `goal`) — no separate
   3-handler allowlist. The scheduled-owl provisioning path (UniOwl reconcile loop)
   is already more general than `/agent`'s fixed handlers, so nothing is lost.
2. **Pause suspends cadence only.** Persona/DNA/history untouched; resume continues
   from wherever the schedule naturally lands. Implemented by reusing
   `JobScheduler.pause`/`resume` (already exists, used by today's `/agent pause`) —
   **no new `paused` field on the manifest.**
3. **Evolution input at creation = preset strategy**, not freeform text and not
   skipped. A fixed choice (`conservative` / `adaptive` / `experimental`) mapping to
   different mutation-rate constants in the existing `EvolutionCoordinator`.
4. **Boundaries is a new free-text field**, distinct from tool grants — a behavioral
   guardrail folded into the system prompt (e.g. "has web_fetch but never share raw
   URLs with the user"), not merely "whatever tools weren't picked."
5. **Legacy names (`/owls`, `/agent`) are removed, not aliased** — but only after
   `/owl` is verified to cover every live capability with no duplicate/divergent
   logic, confirmed by tests, not by inspection alone.
6. **Approach: extend `owl_build`, not rewrite.** `owl_build` remains the single
   implementation for all mutation logic (create/edit/rename/retire/pause/resume),
   used by both the chat tool and the `/owl` slash command. No second persistence
   path, no second consent gate, no second DNA-capture path.

## Architecture

```
                    ┌──────────────┐
   chat (NL) ──────▶│              │
                    │  owl_build   │──▶ OwlRegistry (in-memory)
   /owl (slash) ───▶│  (single     │──▶ stackowl.yaml (durable, via OwlsCommand
                    │  impl)       │       ._upsert_to_yaml/_remove_from_yaml/
                    └──────────────┘       _add_retired_builtin)
                           │
                           └──▶ JobScheduler (scheduled-owl job row;
                                 pause/resume reuse existing primitives)
```

`/owl` is a thin dispatcher: every subcommand becomes one `OwlBuildSpec` +
`owl_build.execute()` call, no matter whether the caller used flags or free text.
This is the one architectural rule that kills the `add`-vs-`create` divergence bug:
**there is only ever one code path from "user intent" to "persisted owl", regardless
of entry surface.**

## Schema changes

`OwlBuildSpec` (`src/stackowl/tools/meta/owl_build_spec.py`):
- `action: Literal["create","edit","retire","rename","pause","resume"]` (adds
  `pause`/`resume` to the set already extended today with `rename`).
- `boundaries: str | None = None` — free-text behavioral constraint.
- `evolution_strategy: Literal["conservative","adaptive","experimental"] | None = None`.

Both new fields optional, default-safe — no existing caller (including any
programmatic one) breaks by omitting them.

`OwlAgentManifest` (`src/stackowl/owls/manifest.py`):
- `boundaries: str = ""` — folded into the rendered system prompt alongside
  `system_prompt`/`specialty`.
- `evolution_strategy: Literal["conservative","adaptive","experimental"] = "adaptive"`
  — read by `EvolutionCoordinator` (`src/stackowl/owls/evolution.py`) to scale its
  existing mutation-rate constants (exact constant mapping is an implementation
  detail for the plan phase, not this spec).
- **No new `paused` field** — pause/resume state lives entirely in the scheduler's
  `jobs` table (existing `status`/`enabled` columns), keyed to the owl's owned job
  row the same way `_reconcile_schedules()` already looks it up.

## New actions on `owl_build.py`

- `_pause(spec, t0)` — resolve the owl's owned scheduled job (same lookup
  `_reconcile_schedules()` uses); if none exists, refuse: `"'{name}' has no
  schedule to pause."`; else call `scheduler.pause(job_id)`.
- `_resume(spec, t0)` — same lookup, call `scheduler.resume(job_id)`.
- No new `can_pause`/`can_resume` authorization gate beyond "does this owl have an
  owned job" — pausing cadence doesn't touch tools/authority/persona, same class of
  operation as `rename`, so it is not gated by `can_modify`/`can_retire`.

## `/owl` command surface (final)

```
/owl create [--name --preset|--explicit_tools --specialty --schedule --goal
             --lifecycle --boundaries --evolution_strategy]   (flags optional;
                                                                 missing → asked)
/owl edit <name> [same flags as create, partial]
/owl rename <name> <display_name>
/owl pause <name>
/owl resume <name>
/owl retire <name>
/owl list
```

Free-text `/owl create <sentence>` and no-arg `/owl create` both enter the same
ClarifyGateway elicitation `owl_build` already runs for chat. This replaces `/owls
add` (flag grammar, kept as `/owl create`'s flag form) and `/owls create` (free-text,
kept as-is) — merged into one entry, one validator, one persistence call.

## Migration / removal order

1. Ship schema + action additions (pause/resume/boundaries/evolution_strategy) to
   `owl_build.py` — additive, `/owls`/`/agent` untouched and still working.
2. Register `/owl` in `assembly.py` as the full dispatcher (create/edit/rename/
   pause/resume/retire/list) — `/owls`/`/owl`(old alias)/`/agent` still registered,
   running in parallel.
3. Retarget `/owls`' and `/agent`'s existing test suites at `/owl`'s equivalent
   subcommands. Green required before step 4.
4. Remove `/owls`, old `/owl` alias registration, and `/agent` from
   `assembly.py`'s dispatcher. Delete `agent_create_command.py`/
   `agent_create_helpers.py`/`agents_helpers.py` and the now-unused parts of
   `owls_command.py`/`owls_helpers.py` (keep whatever `/owl list`/DNA-inspection
   logic is reused under the new command).
5. Full test run + manual smoke: create/edit/rename/pause/resume/retire/list via
   `/owl`, and via chat free text, on a throwaway owl.

## Error handling

- Pause/resume on an unscheduled (`lifecycle=on_demand`) owl → clear refusal, no
  crash, no silent no-op.
- `boundaries`/`evolution_strategy` omitted at create → defaults apply, never blocks.
- Existing yaml snapshot/rollback (`_yaml_snapshot`/`_yaml_restore`) already covers
  the new writes (same file) — reused unchanged, no new rollback path needed.
- Step 4 (deletion) is gated on green tests proving zero capability loss, not on
  inspection alone — this is the one place in this feature with real blast radius
  (removing public command surfaces).

## Testing

- Unit: pause/resume happy path + "no schedule" refusal; `boundaries`/
  `evolution_strategy` default + explicit-value round-trip through
  `manifest_to_yaml_entry`/YAML persistence.
- Regression: the 3 old `/agent` use cases (goal_execution/morning_brief/check_in
  cadence) re-expressed as scheduled-owl creates via `/owl create`, confirmed to
  produce the same end-user-visible behavior (message arrives on schedule).
- Pre-deletion gate: `/owls`' and `/agent`'s current test files retargeted at `/owl`,
  full green, before their source files are deleted.

## Out of scope (explicitly deferred, not silently dropped)

- Evolution-strategy mutation-rate constant tuning (what "conservative" vs
  "experimental" numerically means) — a plan-phase implementation detail, not a
  design-level decision.
- Any UI/dashboard surface beyond the slash command + chat tool — this repo has
  neither today (confirmed in prior session's research) and none is being added.
- Migrating already-created owls' historical data — this only affects the creation/
  management surface, not existing persisted owls, which keep working unchanged.

---
baseline_commit: e42b78ed
---

# Story 2.7: Gate observability and manual dry-run

Status: done

## Story

As the operator,
I want to see when the shadow-validation gate rejects a batch, and be able to trigger it on demand,
so that the gate's behavior is observable without waiting on the once-a-day nightly cycle.

## Acceptance Criteria

1. **Given** the gate rejects a batch (fails to reach N consecutive non-regressions)
   **When** this happens
   **Then** it is logged at a visible level (not buried at WARNING) with the specific non-regression that failed, and the rejection is queryable/countable — not just a single log line

2. **Given** the operator wants to exercise the gate without waiting for the nightly cron
   **When** a manual dry-run command or tool is invoked
   **Then** it runs Story 2.5's replay harness against the current live DNA and reports pass/fail, without mutating anything

3. **Given** Story 2.6's shared gate config (N-threshold, held-out sample size)
   **When** the manual dry-run runs
   **Then** it uses the exact same config as the real promotion path — not a looser one (closes the AD-3 letter-vs-intent gap)

## Design decision — no new table, reuse the existing structured-log infrastructure (AC #1)

"Queryable/countable, not just a single log line" does NOT require a new DB table. This repo's structured JSONL logging (`~/.stackowl/logs/stackowl.jsonl`, one JSON object per line, per CLAUDE.md's Observability section) is ALREADY queryable and countable via `jq` (`cat stackowl.jsonl | jq 'select(.msg == "...")' | wc -l`, or grouped by owl/date) — this is the existing infrastructure this repo's own conventions say to reuse (`feedback_use_existing_infrastructure`). Building a new `gate_rejections` table would duplicate what structured logging already gives for free, for a single-operator platform with no dashboard requirement. This story's job is: (a) elevate the rejection log to ERROR (visible, per CLAUDE.md's own `jq 'select(.level == "ERROR")'` convention for "surface problems"), and (b) make sure every rejection log line carries enough structured `_fields` (owl, checkpoint_id, n_replayed, consecutive_non_regressions, specific failure reasons) to be genuinely countable/groupable by `jq`, not just a free-text message. If a future story needs a real-time dashboard or alert, that's new scope — not this one.

## Tasks / Subtasks

- [x] Task 1: Elevate + enrich the rejection log (AC #1)
  - [x] `owls/evolution.py`'s `_checkpoint_validate_and_promote` (Story 2.6): change the rejection log call from `log.engine.warning(...)` to `log.owls.error(...)` (ERROR, not WARNING — "visible level"). Keep the message text stable/greppable (e.g. `"[dna] coordinator.promote: shadow gate REJECTED"` — do not vary the wording between calls, so `jq 'select(.msg == "...")'` reliably matches every rejection)
  - [x] Enrich the `_fields` payload to include, per rejection: `owl`, `checkpoint_id`, `n_replayed`, `consecutive_non_regressions`, `n_consecutive_required` (from `ShadowValidator`'s config, so a log reader can see how close it came), and `failures` — Story 2.5's `ShadowValidationResult.failures` tuple already carries per-replay failure detail (`{"input_text": ..., "reason": ...}`); include it in `_fields` (truncate `input_text` per this repo's sensitive-data convention if it's long — reuse whatever truncation pattern nearby logging already uses, e.g. `[:200]`) so "the specific non-regression that failed" (AC #1's literal wording) is actually IN the log record, not just a count
- [x] Task 2: Manual dry-run (AC #2, #3)
  - [x] New subcommand `dna-dry-run` on `OwlsCommand` (mirrors the `_dna_restore`/`reset-dna` double-registration pattern from Story 2.2 — add to BOTH `_OWLS_META`/`OwlsCommand.handle()` AND `_OWL_META`/`OwlCommand.handle()`), NOT consent-gated (read-only, no mutation — matches `dna`'s existing `SubCommand` severity, not `reset-dna`/`dna-restore`'s YES-confirm pattern, since this command can NEVER change anything)
  - [x] `async def _dna_dry_run(self, rest: str) -> str`: parse `<name>` (one token), `self._registry.get(name)` (raises `OwlNotFoundError`, let it propagate per this file's established convention), `self._db is None` / `self._provider_registry is None` guard (`"DNA store unavailable."` / an equivalent message — check whether `OwlsCommand` already has a `provider_registry` constructor param; if not, this story adds one, since the dry-run needs a real `ProviderRegistry` to construct `ShadowValidator` — thread it through `OwlsCommand.__init__` and its `create_and_register` factory classmethod, mirroring how `db`/`event_bus`/`tool_registry` are already threaded)
  - [x] Construct `ShadowValidator(self._db, self._provider_registry)` — module-level defaults ONLY, no custom `n_consecutive_required`/`sample_size` (AC #3 — the exact same config Story 2.6's real promotion path uses)
  - [x] `result = await validator.validate(manifest.name, manifest, manifest.dna)` — validates the owl's CURRENT live DNA against itself (per the epics AC's literal wording: "runs the replay harness against the CURRENT live DNA," not a hypothetical proposed delta — this is a self-consistency smoke test: "is this owl still passing its own bar on its own recent real interactions," useful for catching drift even between evolution cycles)
  - [x] Format a human-readable report: pass/fail, `n_replayed`/`consecutive_non_regressions`/`n_consecutive_required`, and (on failure) the `failures` detail — reuse whatever formatting convention `format_dna_display`/similar helpers in `owls_helpers.py` already establish for multi-line command output, don't invent a new style
  - [x] Add the `SubCommand` metadata entries (both `_OWLS_META`/`_OWL_META`) with a clear summary/description and an example invocation
- [x] Task 3: Tests (AC #1, #2, #3)
  - [x] Log enrichment: assert the ERROR-level log fires with the expected `_fields` keys on a rejection (reuse Story 2.6's `AlwaysFailShadowValidator` stub from `tests/_story_2_6_helpers.py`)
  - [x] Dry-run happy path: stub `ShadowValidator` (or its provider) to pass, assert the command reports pass, assert `owl_dna`/registry DNA is COMPLETELY UNCHANGED after the command runs (this is a read-only command — the regression test that proves it)
  - [x] Dry-run failure path: stub to fail, assert the report includes failure detail, DNA still unchanged
  - [x] Config parity (AC #3): assert the `ShadowValidator` constructed by `_dna_dry_run` uses the exact same `n_consecutive_required`/`sample_size` as `EvolutionCoordinator`'s (both should be the module-level defaults from `shadow_validator.py` — assert neither caller passes an override)
  - [x] Wired-in-both-commands regression: same double-registration test pattern Story 2.2 used for `dna-restore`
- [x] Task 4: QA + dev review, tests/ruff/mypy green — **do NOT commit**, leave status=review; the orchestrating session runs independent review and commits (same process note as Story 2.6)

## Dev Notes

### This closes out Epic 2

After this story, Epic 2 (Safe Self-Improvement Foundation) is complete — the full Propose→Clamp→Validate→Commit→Observe pipeline exists, is wired into the real nightly batch, is restorable, and is observable/testable on demand. Epic 3 (next) builds `evolve_now` on top of this foundation.

### Architecture Compliance

- AD-3 (the exact gap this story closes per its own wording): the dry-run MUST use the identical shared config `ShadowValidator`'s defaults provide — Task 3's "config parity" test is the concrete proof this AD-3 amendment (originally flagged during elicitation as a literal-compliance-but-intent-violation risk) doesn't regress.
- NFR-3: 4-point logging on `_dna_dry_run`, `log.gateway` namespace (matches every other method in `owls_command.py`).

### Testing Standards

- `pytest` + `pytest-asyncio`, real `tmp_db`, no mocking of pure logic — stub only the `ShadowValidator`/provider boundary (Story 2.6's existing stub helpers).
- Run: `tests/commands/test_owls_reset_dna.py` (or wherever this story's new command tests land — likely the same file, following Story 2.2's precedent) + `tests/commands/test_owls_meta.py` (subcommand registration) + `tests/owls/test_shadow_validator.py` (regression). Do NOT run the full suite (hangs on this box).
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- Modified: `src/stackowl/commands/owls_command.py` (new method + metadata/routing in both places + possible new `provider_registry` constructor param), `src/stackowl/owls/evolution.py` (log-level/fields enrichment only). No new files, no migration.

### Process note (same as Story 2.6)

Implement + test + verify gates green, set status=review, and STOP. Do NOT `git commit`. The orchestrating session runs an independent review and handles the commit.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 2.7] (lines 274-293)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-3 amendment note, "Gate rejections get real visibility" / "manual dry-run" elicitation hardening notes under Epic 2's description)
- [Source: _bmad-output/implementation-artifacts/2-5-shadow-validation-gate-replay-harness.md], [Source: _bmad-output/implementation-artifacts/2-6-wire-gate-into-promotion-auto-restore.md] (prior stories — `ShadowValidator`/`ShadowValidationResult` shape, the rejection log call site being enriched)
- [Source: _bmad-output/implementation-artifacts/2-2-dna-restore-command.md] (Story 2.2 — the double-registration pattern to mirror for `dna-dry-run`)
- [Source: src/stackowl/commands/owls_command.py] (direct read — current `_reset_dna`/`_dna_restore`/`_OWLS_META`/`_OWL_META`/both `handle()` methods)
- [Source: CLAUDE.md#Observability & Debugging] (structured JSONL logging convention this story reuses instead of a new table)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-dev-story, Amelia persona)

### Debug Log References

- `uv run pytest tests/commands/test_owls_reset_dna.py tests/commands/test_owls_meta.py tests/owls/test_shadow_validator.py tests/owls/test_evolution_feedback.py -q` → 25 passed
- `uv run pytest tests/commands/test_owls_objectives.py tests/commands/test_owls_builder.py tests/commands/test_owl_dispatcher.py tests/commands/test_owl_surface_complete.py tests/test_owls_command_registration.py tests/test_story_4_4.py -q` → 54 passed, 1 pre-existing failure (`test_owls_command_registered_list_and_health` — "secretary" missing from `/owls list` roster; reproduced identically on baseline `e42b78ed` via `git stash`, unrelated to this story's files)
- `uv run pytest tests/owls/ -q -k "evolution or shadow"` → 18 passed
- `uv run pytest tests/test_story_4_3.py tests/owls/test_evolution_strategy_scaling.py -q` → 29 passed, 1 pre-existing failure (`test_execute_with_mock_llm_applies_mutations` — mutation-math assertion off by a fixed amount; reproduced identically on baseline via `git stash`, unrelated to gate observability/dry-run)
- `uv run ruff check` (touched files) → clean
- `uv run mypy` (touched files, and full `src/`) → clean on all 5 touched files; the 79 full-`src/` errors are all in files this story never touches (`plugins/context.py`, `mcp/server.py`, `channels/telegram/notifications.py`, `scheduler/assembly.py`, `startup/orchestrator.py`, `cli/app.py`) — pre-existing baseline noise, not introduced here

### Completion Notes List

- Task 1 (AC #1): `_checkpoint_validate_and_promote`'s rejection log moved from `log.engine.warning` to `log.owls.error`, message text held stable (`"[dna] coordinator.promote: shadow gate REJECTED"`, no longer varying with a `— restoring checkpoint` suffix), `_fields` enriched with `n_consecutive_required` and a `failures` list (each entry's `input_text` truncated to `[:200]`, matching this repo's existing truncation convention). Added a public `ShadowValidator.n_consecutive_required` / `.sample_size` property pair (falls back to the module defaults via `getattr` for test doubles like `AlwaysFailShadowValidator` that skip `__init__`) so the log call and the dry-run command can both read the gate's config without reaching into a private attribute.
- Updated the one pre-existing test that directly asserted on the old log call site (`tests/owls/test_evolution_feedback.py::test_gate_rejects_mutation_restores_checkpoint_and_logs_warning`) to monkeypatch `log.owls.error` instead of `log.engine.warning`, and added assertions on the enriched `_fields` payload.
- Task 2 (AC #2, #3): added `OwlsCommand._dna_dry_run`, wired into both `OwlsCommand.handle()`/`_OWLS_META` and `OwlCommand.handle()`/`_OWL_META` (mirrors Story 2.2's `dna-restore` double registration). Not consent-gated — read-only, never mutates. Threaded a new `provider_registry: ProviderRegistry | None` constructor param through `OwlsCommand.__init__` and `create_and_register`, and wired it at the real `/owl` registration site in `commands/assembly.py` (`cast("ProviderRegistry | None", deps.provider_registry)`, same pattern as `preference_store`/`morning_brief_handler`). `_dna_dry_run` constructs `ShadowValidator(self._db, self._provider_registry)` with zero keyword overrides — the same call shape `EvolutionCoordinator.__init__` already uses for its production default — so AC #3's config parity is structural, not just tested.
- Added `format_dry_run_report` to `owls_helpers.py`, mirroring `format_dna_display`'s header/separator/indented-line convention rather than inventing a new report style.
- Task 3: reused Story 2.6's `AlwaysFailShadowValidator` for the log-enrichment assertions; added 5 new tests to `tests/commands/test_owls_reset_dna.py` covering the DNA-store-unavailable guard, pass path (report + zero mutation, including an empty `owl_dna` row-count check), fail path (failure detail surfaced in the report text), config-parity (spies on the constructed validator's `n_consecutive_required`/`sample_size` and compares to `shadow_validator._DEFAULT_N_CONSECUTIVE`/`_DEFAULT_SAMPLE_SIZE`), and a double-registration regression exercising both `OwlsCommand` and `OwlCommand` end to end. Added `"dna-dry-run"` to `test_owls_meta.py`'s declared-subcommands set.
- No new DB table — per the story's own design-decision section, structured JSONL logging is the "queryable/countable" mechanism for AC #1; this story only elevates the log level and enriches its fields.
- Two pre-existing, unrelated test failures were found during regression runs and verified (via `git stash` against baseline `e42b78ed`) to already fail before this story's changes — see Debug Log References. Left untouched per this story's scope (neither file is in the Project Structure Notes' Modified list, and both concern subsystems this story doesn't touch — builtin persona registration and evolution mutation-math scaling). Flagging for the orchestrating session / a follow-up story rather than silently ignoring them.

### File List

- `src/stackowl/owls/evolution.py` — modified (rejection log: WARNING→ERROR, `log.engine`→`log.owls`, enriched `_fields`)
- `src/stackowl/owls/shadow_validator.py` — modified (added `n_consecutive_required`/`sample_size` public properties)
- `src/stackowl/commands/owls_command.py` — modified (new `_dna_dry_run` method; `dna-dry-run` routing in both `handle()`s; `dna-dry-run` `SubCommand` metadata in both `_OWLS_META`/`_OWL_META`; new `provider_registry` constructor param + `create_and_register` param; `format_dry_run_report` import)
- `src/stackowl/commands/owls_helpers.py` — modified (new `format_dry_run_report` function + `ShadowValidationResult` import)
- `src/stackowl/commands/assembly.py` — modified (wired `provider_registry` into the `/owl` `OwlCommand(...)` construction; `ProviderRegistry` added to the `TYPE_CHECKING` import block)
- `tests/owls/test_evolution_feedback.py` — modified (rejection test updated for the new logger/level + enriched-fields assertions)
- `tests/commands/test_owls_reset_dna.py` — modified (5 new `dna-dry-run` tests + `_cmd_with_providers` helper + imports)
- `tests/commands/test_owls_meta.py` — modified (`"dna-dry-run"` added to `_EXPECTED`)
- `_bmad-output/implementation-artifacts/sprint-status-owl-dna.yaml` — modified (`2-7-gate-observability-and-manual-dry-run`: `backlog`→`ready-for-dev`→`review`; `last_updated` note)

## Change Log

- 2026-07-15: Story 2.7 implemented — gate rejections now log at ERROR with enriched structured fields (`n_consecutive_required`, `failures`) instead of a bare WARNING (AC #1); new read-only `/owls dna-dry-run <name>` / `/owl dna-dry-run <name>` command runs `ShadowValidator` against an owl's current live DNA using the exact same module-level config the real promotion path uses, with zero mutation (AC #2, #3). This closes out Epic 2. Status → review; not committed per this story's explicit process note (independent review pass runs first). Two pre-existing, unrelated test failures were found and verified against baseline — see Dev Agent Record.

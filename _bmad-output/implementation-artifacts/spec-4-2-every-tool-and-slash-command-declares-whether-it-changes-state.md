---
title: 'Every tool and slash command declares whether it changes state'
type: 'feature'
created: '2026-09-19'
status: 'in-progress'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: ['oversized']
deferred: []
baseline_revision: '22193f09a870d8537ae39772682637b65eb0c67a'
---

<intent-contract>

## Intent

**Problem:** Epic 4's future "one door" gate (stories 4.3+) needs to know, for every one of the platform's 77 LLM tools and 34 slash commands (125 dot-paths counting the 91 real subcommand nodes across the 14 commands that have them), whether it changes state and — if so — the command type(s) it will eventually submit. Tools already carry `ToolManifest.action_severity`, but 2 of 77 never set it explicitly (silently inherit the permissive "read" field default), no tool names a future command type, and slash commands/subcommands have no severity concept at all.

**Approach:** Make tool severity structurally mandatory (remove `ToolManifest.action_severity`'s field default so an undeclared tool fails construction, not just a lint pass), add a `command_types: tuple[str, ...]` field validated non-empty whenever severity isn't `"read"`, and fix the 2 tools relying on the implicit default. For slash commands (no existing per-file severity mechanism), add one central, reviewable census module — `commands/state_census.py` — mirroring `commands/manifest.py`'s `SHIPPED_COMMANDS` precedent, covering all 34 commands + 91 subcommand paths. Track "not yet migrated" state-changing command types in one shared `authz/state_change_census.py` ledger (mirrors `journal/coverage.py`'s `UNJOURNALED_TABLES` dict-with-reason pattern), keyed by command type across BOTH tools and commands (one shared namespace — the same real action gets the same command-type name regardless of surface), each entry naming its owning migration story (4.3/4.7/4.8/4.9/4.10) and a `status` (`pending`/`migrated`) so a future story flips status rather than deleting the entry (avoids the tripwire regressing when 4.3+ actually migrates something). Three new tripwire tests enforce closure (nothing undeclared) and no stale ledger entries.

## Boundaries & Constraints

**Always:**
- Every live tool (`ToolRegistry.with_defaults().all()`, 77 today) has an explicit, structurally-enforced `action_severity`; every live command+subcommand (`register_all_commands(CommandDeps())` → `CommandRegistry.instance().list()`, real DI-constructed instances — never bare `cls()`) is a key in the new census with a declared severity.
- Every `write`/`consequential` entry (tool or command/subcommand) names ≥1 non-empty `command_types` string, dot-namespaced (`"<subsystem>.<verb>"`), and every such string has a matching entry in `authz/state_change_census.py`'s ledger naming one of exactly `{"4.3","4.7","4.8","4.9","4.10"}` as owning story.
- Command-type names are ONE shared namespace: a tool and a command/subcommand that will end up submitting the same real-world action share the identical string (e.g. `/skill rm` and `skill_manage(action="delete")` both name `"skill.delete"`).
- 4-point logging only where a touched function already had it — this story adds declarative metadata and tripwires, not new runtime code paths, so no new logging is invented.

**Never:**
- Do not build `commands/spec/`, `CommandSpec`, the action-policy gate, or any real command dispatch/execution path — purely declarative census work, per epic-4-context's story boundary (4.3+ own execution).
- Do not change any existing tool's or command's actual runtime behavior, consent gating, or currently-declared `action_severity`/`consent_category` value — this story only adds declarations, never re-classifies existing tool severities (commands have no prior severity to preserve).
- Do not classify individual `LearnedShellTool` instances (dynamic, per-install, model-authored; `LearnedToolSpec.action_severity` already required with a safe `"consequential"` default — confirmed at `tools/meta/tool_spec.py:89` — and already excluded from static discovery via `EXCLUDED_FROM_DISCOVERY`). Note this exemption in the tripwire, don't silently ignore it.
- Do not remove/rename any `PENDING_MIGRATION`-equivalent entry once a future story migrates it — flip `status` to `"migrated"` instead (owned by that future story, not this one).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| A tool constructs `ToolManifest(...)` with no `action_severity` | e.g. a future new tool forgets it | `pydantic.ValidationError` at manifest-access time (registry boot) | Fails loudly, names the missing field |
| A `write`/`consequential` `ToolManifest` has `command_types=()` | e.g. someone sets severity but forgets command types | `ValidationError` from a model-validator | Fails loudly, names the tool |
| A live tool/command declares a `command_types` entry absent from `authz/state_change_census.py` | new state-changing tool ships without a ledger entry | Tripwire fails, names the tool/command and the missing command type | Assertion message lists exact offenders |
| A `state_change_census` ledger entry names a command type no live tool/command declares anymore | stale entry (e.g. renamed) | Stale-entry tripwire fails, names the orphaned key | Assertion message lists exact offenders |
| A live command/subcommand path is missing from `commands/state_census.py` | e.g. a new subcommand ships undeclared | Closure tripwire fails (exact-set-equality diff, mirrors `test_reachability_guard.py`) | Reports extra/missing sets |

</intent-contract>

## Code Map

- `src/stackowl/tools/base.py:205-291` (`ToolManifest`) -- remove the `= "read"` default from `action_severity` (make required); add `command_types: tuple[str, ...] = ()`; add a `@model_validator(mode="after")` requiring non-empty `command_types` when `action_severity != "read"`. `Tool.manifest`'s own base-default property (`base.py:352-362`) is left untouched (still omits `action_severity`) — this is deliberate: any future tool relying purely on it now fails loudly at construction, which is the enforcement mechanism.
- `src/stackowl/tools/meta/note_applied_lesson.py`, `src/stackowl/tools/meta/tool_search.py` -- the 2 tools with zero `action_severity` mentions (confirmed via `grep -rln action_severity src/stackowl/tools/`, 64/66 files hit). Add an explicit `manifest` property override passing `action_severity="read"` (matches the many other read tools' existing defensive-explicit style).
- 28 tool files with an existing non-default `action_severity=` (confirmed live via `ToolRegistry.with_defaults().all()`, 37 tool instances total, 13 `consequential` + 24 `write`; 28 files not 37 because `browser/tools.py` holds 10 of the browser tool classes in one file) -- add `command_types=(...)` to each tool's manifest construction. Exact file list and assigned command types/owning stories (all Design Notes below apply verbatim, one tuple-literal edit per file):
  `tools/io/apply_patch.py`, `tools/interaction/batch_approve.py`, `tools/browser/browse.py`, `tools/browser/dialog.py`, `tools/browser/tools.py` (10 classes: Click/Type/Scroll/EvalJs/Upload/Download/CookiesGet/CookiesSet/CookiesClear/TabClose/Close — only the non-read ones need edits, see Design Notes), `tools/system/claude_code.py`, `tools/scheduling/cronjob.py`, `tools/agents/delegate_task.py`, `tools/io/edit.py`, `tools/code/execute_code.py`, `tools/system/git_tool.py`, `tools/knowledge/memory.py`, `tools/scheduling/objective_tool.py`, `tools/meta/owl_build.py`, `tools/scheduling/owl_schedule.py`, `tools/process/process_tool.py`, `tools/system/run_tests.py`, `tools/scheduling/send_file.py`, `tools/scheduling/send_message.py`, `tools/agents/sessions_send.py`, `tools/agents/sessions_spawn.py`, `tools/knowledge/output_preference.py`, `tools/system/shell.py`, `tools/knowledge/skill_manage.py` (action enum confirmed at `skill_manage.py:69-71`: `create/edit/patch/delete/enable/disable`), `tools/knowledge/synthesize_skills.py`, `tools/meta/tool_build.py`, `tools/io/undo_store.py`, `tools/io/write_file.py`.
- New `src/stackowl/authz/state_change_census.py` -- the shared ledger. `CommandTypeMigration(story: Literal["4.3","4.7","4.8","4.9","4.10"], status: Literal["pending","migrated"] = "pending", reason: str = "")` frozen dataclass; `COMMAND_TYPE_MIGRATIONS: dict[str, CommandTypeMigration]` keyed by every command-type string named anywhere (tools + commands), one entry per string (cronjob's 6 command types split 2×4.3 + 4×4.7, as separate keys). Docstring modeled on `journal/coverage.py`'s "why a dict, not a derived set" rationale.
- `src/stackowl/authz/__init__.py` -- export `CommandTypeMigration`, `COMMAND_TYPE_MIGRATIONS`, `MIGRATION_STORIES` alongside existing exports (same additive pattern story 4.1 used).
- New `src/stackowl/commands/state_census.py` -- `CommandDeclaration(action_severity: Literal["read","write","consequential"], command_types: tuple[str, ...] = ())`; `COMMAND_CENSUS: dict[str, CommandDeclaration]` (34 keys, one per `manifest.py::SHIPPED_COMMANDS` entry); `SUBCOMMAND_CENSUS: dict[str, CommandDeclaration]` (91 keys, dotted paths e.g. `"config.set"`, `"owl.pause"`, `"browser.profile.delete"` for nested children). Full classification table in Design Notes below.
- `src/stackowl/commands/manifest.py` -- reference only, no edit; `SHIPPED_COMMANDS`/`EXEMPT_COMMANDS` is the precedent this mirrors and the closure check's ground truth.
- `src/stackowl/commands/registry.py:98` (`CommandRegistry.list`), `src/stackowl/commands/assembly.py` (`register_all_commands`, `CommandDeps`) -- read-only reuse: the new command tripwire drives the SAME real-registry construction `tests/journeys/commands/test_reachability_guard.py` uses (never bare `cls()` — that silently skips DI commands with required constructor args).
- `src/stackowl/commands/metadata.py` (`CommandMeta.subcommands`, `SubCommand.children`) -- read-only; the new tripwire recursively flattens `.children` into dotted paths, same tree the existing `test_every_command_meta_is_guarded.py` walks (one level) but recursive here since "including sub-commands" must cover nested `browser.profile.*`/`browser.watch.*`.
- New `tests/tools/test_every_tool_declares_state_change.py` -- tripwire: floor check (≥77 live tools, ≥37 non-read), every non-read tool's `command_types` non-empty (belt-and-suspenders over the Pydantic validator, with a named-offender message), every declared command type has a `COMMAND_TYPE_MIGRATIONS` entry with `story` in `MIGRATION_STORIES`.
- New `tests/commands/test_every_command_declares_state_change.py` -- tripwire: `{c.command for c in CommandRegistry.instance().list()} == set(COMMAND_CENSUS)` after `register_all_commands(CommandDeps())` (mirrors `test_reachability_guard.py`); flattened live subcommand-path set `== set(SUBCOMMAND_CENSUS)`; every non-read census entry's `command_types` non-empty and each has a `COMMAND_TYPE_MIGRATIONS` entry.
- New `tests/authz/test_state_change_census_has_no_stale_entries.py` -- tripwire: `COMMAND_TYPE_MIGRATIONS.keys()` is a subset of (tool command types ∪ `COMMAND_CENSUS`/`SUBCOMMAND_CENSUS` command types) — the reverse-direction check neither package-local test can make alone.
- `tests/tools/test_tool_severities.py` -- existing narrow pin test; unaffected (still passes — its 3 assertions read `.manifest.action_severity`, unchanged values).

## Tasks & Acceptance

**Execution:**
- `src/stackowl/tools/base.py` -- edit `ToolManifest` (drop `action_severity` default, add `command_types` field + validator) -- structural enforcement, closes the "declared, not defaulted" AC
- `src/stackowl/tools/meta/note_applied_lesson.py` -- add explicit `manifest` override, `action_severity="read"` -- closes the 2nd of 2 confirmed gaps
- `src/stackowl/tools/meta/tool_search.py` -- add explicit `manifest` override, `action_severity="read"` -- closes the 1st of 2 confirmed gaps
- 28 tool files listed in Code Map -- add `command_types=(...)` per Design Notes table -- names each state-changer's future command type(s)
- `src/stackowl/authz/state_change_census.py` -- create -- shared pending-migration ledger, one entry per command-type string
- `src/stackowl/authz/__init__.py` -- add 3 exports -- canonical import surface (AD-7 pattern)
- `src/stackowl/commands/state_census.py` -- create -- `COMMAND_CENSUS` (34) + `SUBCOMMAND_CENSUS` (91) per Design Notes table
- `tests/tools/test_every_tool_declares_state_change.py` -- create -- tool-side closure + ledger cross-check tripwire
- `tests/commands/test_every_command_declares_state_change.py` -- create -- command-side closure + ledger cross-check tripwire
- `tests/authz/test_state_change_census_has_no_stale_entries.py` -- create -- reverse stale-entry tripwire

**Acceptance Criteria:**
- Given every registered LLM tool and every slash command including sub-commands, when the census is built, then each one is declared read-only or state-changing, and each state-changing entry names its future command type(s) (FR76, FR79) -- proven by `test_every_tool_declares_state_change.py` and `test_every_command_declares_state_change.py`'s closure assertions
- Given a tool or slash command with no declaration, when the tripwires run, then the census tripwire fails and names it -- proven by a red-then-green check: temporarily add an undeclared tool/command in a throwaway test, confirm the closure assertion names it, then confirm the real (clean) tree passes
- Given a state-changing entry not yet migrated to commands, when the census reports, then it is listed pending with its owning story (4.3 for the `cronjob` pause/resume pilot only; 4.7/4.8/4.9/4.10 for everything else) -- proven by `COMMAND_TYPE_MIGRATIONS` containing every declared command type with `status="pending"` today (nothing is migrated yet; 4.3+ flips `status` as it burns this list down)
- Given the full tripwire suite (`./scripts/tripwires.sh`), when it runs after this story, then it passes, including all pre-existing tool/command tests unchanged

## Spec Change Log

## Review Triage Log

## Design Notes

**Command-type / story assignment — tools (37 non-read of 77 live, confirmed via `ToolRegistry.with_defaults().all()`):**

| tool | severity | command_types | story |
|---|---|---|---|
| apply_patch | write | files.apply_patch | 4.10 |
| batch_approve | write | consent.batch_approve | 4.10 |
| browser_browse | consequential | browser.browse | 4.10 |
| browser_click | write | browser.click | 4.10 |
| browser_close | write | browser.close_session | 4.10 |
| browser_cookies_clear | write | browser.clear_cookies | 4.10 |
| browser_cookies_set | write | browser.set_cookie | 4.10 |
| browser_dialog | consequential | browser.handle_dialog | 4.10 |
| browser_download | consequential | browser.download_file | 4.10 |
| browser_eval_js | consequential | browser.eval_js | 4.10 |
| browser_scroll | write | browser.scroll | 4.10 |
| browser_tab_close | write | browser.close_tab | 4.10 |
| browser_type | write | browser.type_text | 4.10 |
| browser_upload | consequential | browser.upload_file | 4.10 |
| claude_code | consequential | dev.claude_code_run | 4.10 |
| cronjob | write | scheduling.pause_job(4.3), scheduling.resume_job(4.3), scheduling.create_job(4.7), scheduling.edit_job(4.7), scheduling.delete_job(4.7), scheduling.run_now_job(4.7) | 4.3+4.7 |
| delegate_task | write | agents.delegate_task | 4.10 |
| edit | write | files.edit | 4.10 |
| execute_code | consequential | code.execute | 4.10 |
| git | write | dev.git_command | 4.10 |
| memory | write | memory.record_reflection | 4.10 |
| objective | write | scheduling.set_objective | 4.7 |
| owl_build | consequential | owls.build | 4.9 |
| owl_schedule | write | scheduling.set_owl_schedule | 4.7 |
| process | write | process.manage | 4.10 |
| run_tests | write | dev.run_tests | 4.10 |
| send_file | consequential | messaging.send_file | 4.8 |
| send_message | consequential | messaging.send_message | 4.8 |
| sessions_send | write | agents.session_send | 4.10 |
| sessions_spawn | write | agents.session_spawn | 4.10 |
| set_output_preference | write | preferences.set_output_format | 4.10 |
| shell | write | system.shell_exec | 4.10 |
| skill_manage | consequential | skill.author_create, skill.author_edit, skill.author_patch, skill.delete, skill.set_enabled (action enum: `skill_manage.py:69-71`) | 4.9 |
| synthesize_skills | consequential | skill.synthesize | 4.9 |
| tool_build | consequential | owls.build_tool | 4.9 |
| undo_write | write | files.undo_write | 4.10 |
| write_file | write | files.write | 4.10 |

**Commands + subcommands (34 top-level + 91 subcommand paths).** `r` = read (no `command_types`/ledger entry). Compressed; top-level bare command listed once, subcommands indented as `parent.child`:

```
help r | find r | tools r | explain r | whoami r | why r | permissions r | style r | learn r | new w session.start_new 4.10

config r
  config.list r | config.get r | config.export r
  config.set w config.set_value 4.10 | config.reset w config.reset_value 4.10 | config.detect-timezone w config.set_value 4.10

cost r
  cost.turns r
  cost.privacy c cost.wipe_history 4.10

provider r
  provider.list r | provider.status r | provider.models r
  provider.add w provider.add 4.10 | provider.remove c provider.remove 4.10 (real delete, no YES gate today — see deferred-work)
  provider.set-tier w provider.set_tier 4.10 | provider.edit w provider.edit_field 4.10
  provider.enable w provider.set_enabled 4.10 | provider.disable w provider.set_enabled 4.10
  provider.set-token w provider.set_token 4.10 | provider.rename w provider.rename 4.10
  provider.model-add w provider.add_model 4.10 | provider.remove-model w provider.remove_model 4.10
  provider.set-model-tokens w provider.set_model_field 4.10 | provider.set-model-context w provider.set_model_field 4.10

tier w tier.set_preference 4.10 (bare+name only; bare-empty is read)
  tier.list r | tier.menu r
  tier.add w tier.add_provider 4.10 | tier.remove w tier.remove_provider 4.10

browser r
  browser.help r | browser.settings r | browser.sessions r
  browser.close w browser.close_session 4.10 | browser.fetch-binary w browser.fetch_binary 4.10
  browser.profile r
    browser.profile.list r | browser.profile.delete w browser.delete_profile 4.10
  browser.watch r
    browser.watch.list r

skill r
  skill.use r | skill.list r | skill.show r | skill.edit r | skill.diff r | skill.menu r
  skill.add w skill.install 4.9 | skill.rm c skill.delete 4.9
  skill.enable w skill.set_enabled 4.9 | skill.disable w skill.set_enabled 4.9
  skill.reload w skill.reload_index 4.9 | skill.pin w skill.set_pinned 4.9 | skill.unpin w skill.set_pinned 4.9
  skill.dedupe c skill.dedupe 4.9 | skill.migrate w skill.migrate_standard 4.9 | skill.restore w skill.restore_version 4.9

memory r
  memory.stats r | memory.search r | memory.budget r | memory.export r
  memory.remember w memory.add_entry 4.10 | memory.forget w memory.remove_entry 4.10

owl r
  owl.list r | owl.dna r | owl.dna-dry-run r | owl.health r | owl.objectives r | owl.objective r
  owl.create c owl.create 4.9 | owl.edit w owl.edit 4.9 | owl.rename w owl.rename 4.9 | owl.retire c owl.retire 4.9
  owl.pause w scheduling.pause_owl_job 4.7 | owl.resume w scheduling.resume_owl_job 4.7
  owl.reset-dna c owl.reset_dna 4.9 | owl.dna-restore c owl.dna_restore 4.9
  owl.objective-cancel c owl.cancel_objective 4.9 | owl.objective-merge c owl.merge_objective 4.9

focus w focus.set_mode 4.10 (bare-no-arg is read)

preferences r
  preferences.list r
  preferences.remove w preferences.remove_note 4.10

urgent c notifications.broadcast_urgent 4.8 (same delivery seam as send_message tool)
quiet w notifications.set_quiet_hours 4.10

notifications r
  notifications.missed r

bye c system.shutdown 4.10 (sets shutdown_event, stops whole server — confirmed `bye_command.py:45`)
reset c session.clear_history 4.10 (own docstring: "the DESTRUCTIVE one")

audit r
  audit.export w audit.export_log 4.10

brief c notifications.deliver_brief 4.8 (epics.md Story 4.8 names "brief" explicitly)

parliament w parliament.start_debate 4.10
  parliament.log r
  parliament.push w parliament.push_interjection 4.10 | parliament.expand w parliament.expand_claim 4.10
  parliament.unsuppress w parliament.unsuppress 4.10

webhook r
  webhook.list r
  webhook.register c webhook.register_source 4.10 | webhook.enable w webhook.set_enabled 4.10 | webhook.disable w webhook.set_enabled 4.10

connect c integrations.link_account 4.10 (bare/no-service is read)
disconnect c integrations.unlink_account 4.10

plugins r
  plugins.list r | plugins.info r
  plugins.enable w plugins.set_enabled 4.10 | plugins.disable w plugins.set_enabled 4.10

onboarding w config.set_autonomy_level 4.10 (only the autonomy step mutates; delegates to /config set)
```

**Planning-time judgment calls (deviate from a naive read, logged so the diff doesn't look arbitrary):**
- `provider.remove`: classified `consequential` (real, irreversible delete) though it has no confirmation gate today — that gap is real and pre-existing, logged to `deferred-work.md`, not fixed by this story (this story only declares severity, never adds new gating).
- `urgent`/`brief`: classified `consequential`/story 4.8 (not 4.10) because both route through the same message-delivery seam `send_message`/`send_file` already declare `consequential`, and epics.md Story 4.8 names "brief" explicitly as a scheduled delivery this platform migrates.
- `owl.pause`/`owl.resume`: bucketed to 4.7 (scheduling), not 4.9 (owl authority) — Story 4.7's AC text explicitly covers "any slash command that changes schedules," and these two literally suspend/resume the owl's cron cadence.
- `owl.edit` stays a single `write` entry in this story even though it can carry authority-widening fields (`--tools`/`--capability_profile`) — splitting it into a separate `consequential` command type is Story 4.9's classification-time call once it actually builds the owl commands, logged to `deferred-work.md` as a flag for that story.

## Verification

**Commands:**
- `uv run pytest tests/tools/ -q -p no:cacheprovider` -- expected: all pass, including the new closure tripwire and the existing `test_tool_severities.py`
- `uv run pytest tests/commands/ -q -p no:cacheprovider` -- expected: all pass, including the new closure tripwire and `test_every_command_meta_is_guarded.py` unchanged
- `uv run pytest tests/authz/ -q -p no:cacheprovider` -- expected: all pass, including the new stale-entry tripwire and story 4.1's tripwire unchanged
- `uv run pytest tests/journeys/commands/test_reachability_guard.py -q -p no:cacheprovider` -- expected: unaffected, still green (proves `commands/state_census.py` didn't perturb registration)
- `uv run pytest -m tripwire -q -p no:cacheprovider` -- expected: passes, exactly what `scripts/tripwires.sh` step 1 runs
- `./scripts/tripwires.sh` -- expected: `TRIPWIRES PASS`

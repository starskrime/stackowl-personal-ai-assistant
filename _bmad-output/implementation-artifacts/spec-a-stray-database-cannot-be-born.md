---
title: 'Reading the platform database can never create one, and a stray database is healed'
type: 'bugfix'
created: '2026-09-12'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '0ce773ccb47c79cba4999a4b0aaa8ccde40421b3'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `~/.stackowl/stackowl.db` keeps reappearing as a 0-byte file (deleted 2026-09-08 in `b7635aff`, recreated twice on 2026-09-11). Proven creator: ad-hoc diagnosis processes (AI subagents) guess the database sits beside the config, run `sqlite3.connect('~/.stackowl/stackowl.db')`, and sqlite creates an empty file instead of failing. WHY it keeps happening: the real location (`StackowlHome.db_path()` → `~/.stackowl/workspace/stackowl.db`) is only discoverable by reading code; the one safe helper (`scripts/db_query.sh`) is documented only in its own header; no instruction file has named either since the root agent-instructions file was removed in `34e022da`; and nothing in the platform notices a stray database beside the real one. `DbContributor`'s own reachability ping (`health/contributors.py:146`) uses a connect that could likewise create the file it checks.

**Approach:** Make the right way the easy way and let the platform heal the wrong way: a read-only `stackowl db query` command that always resolves through `StackowlHome`, opens `mode=ro`, and refuses a missing or empty file loudly — it replaces `scripts/db_query.sh`, which is deleted; a short root `AGENTS.md` names the command and the real database path so agents stop guessing; a health contributor that finds databases under the StackOwl home other than `db_path()` — removing a 0-byte stray (no `-wal`/`-shm`) as a logged heal, reporting a non-empty one down with a remedy and never deleting it; `DbContributor` pings `mode=ro`. The current stray is removed by that heal on restart, proving the fix.

**Decisions (human, 2026-09-12):** J2 — recreate a short `AGENTS.md` with a runtime-state section (via `bmad-project-context`). J3 — `scripts/db_query.sh` is replaced by `stackowl db query` and deleted with its references.

## Boundaries & Constraints

**Always:** every path through `StackowlHome` (no `Path.home()/".stackowl"` literals); read-only opens use `file:…?mode=ro` URIs; the command prints the resolved path it used and exits non-zero with a remedy when the file is missing, empty, or the SQL is not read-only (it runs under a read-only connection, so writes fail by construction); the command covers what `scripts/db_query.sh` did today before the script is deleted, and every reference to the script is updated; `AGENTS.md` stays short — runtime state (database path, read-only query command, `mode=ro` rule) and nothing the code already says; the heal removes only a file that is exactly 0 bytes with no `-wal`/`-shm` siblings and is not `db_path()`; a non-empty stray is reported through the health sweep (so self-healing pages it) and never touched; cross-platform paths; 4-point logging.

**Never:** delete or modify the real database or any non-empty file; follow symlinks out of the StackOwl home; add a second copy of the path logic; keep `scripts/db_query.sh` as a wrapper; touch `src/stackowl/control_plane/`, `src/stackowl/config/`, `src/stackowl/startup/orchestrator.py` (another change is landing there) — `src/stackowl/cli/app.py` only by adding a command group, after that change lands.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Query happy path | `stackowl db query "select count(*) from jobs"` | rows printed, resolved path printed | N/A |
| Write attempt | `stackowl db query "delete from jobs"` | refused, nothing changed | non-zero exit, "read-only" remedy |
| Missing database | fresh home, no db yet | no file created | non-zero exit, remedy names `db_path()` |
| Empty stray at boot | 0-byte `~/.stackowl/stackowl.db`, no wal/shm | health sweep removes it; heal logged | removal failure → contributor down with remedy |
| Non-empty stray | `~/.stackowl/other.db` with data | contributor reports down with its path and size; file untouched | pages via health sweep |
| Real database | `db_path()` | never flagged, never touched | N/A |
| DbContributor ping | db present | ok, file untouched; db absent → down, no file created | N/A |
| Old helper | `scripts/db_query.sh` | gone; nothing references it | N/A |

</frozen-after-approval>

## Code Map

- `src/stackowl/paths.py` -- `StackowlHome` (`db_path()`, `default_db_path()`, home root); the only path authority.
- `src/stackowl/health/contributors.py:146` -- `DbContributor` ping: switch to `mode=ro`; add the stray-database contributor beside it; registration follows the existing contributor registration pattern (`scheduler/assembly.py:649`).
- `src/stackowl/health/status.py:62-92` -- `HealthContributor` / `HealthStatus(remedy)`.
- `src/stackowl/cli/app.py` -- add a `db` Typer group with `query` (follow `serve_app` group pattern :19,31,259).
- `scripts/db_query.sh` -- read its header for what it does today; replace, then delete; grep the repo for references.
- `scripts/migrate_lessons_from_lancedb.py:64`, `scripts/retired_log_messages.py:256` -- hand-built home paths; route through `StackowlHome`.
- Repo root -- new short `AGENTS.md` (use the `bmad-project-context` skill's format).
- Tests: `tests/paths/test_the_state_lives_where_the_accessor_says.py` (natural home for the heal and no-stray regression), `tests/test_no_python_module_reimplements_the_home.py` (scans `src/` only — extend to `scripts/`).

## Tasks & Acceptance

**Execution:**
- [x] `tests/paths/` + `tests/health/` + CLI test -- failing tests first for every matrix row; extend the home-reimplementation guard to `scripts/`.
- [x] `src/stackowl/health/contributors.py` -- `mode=ro` ping; stray-database contributor with the 0-byte heal.
- [x] `src/stackowl/cli/app.py` (+ a small `src/stackowl/cli/db_cli.py`) -- `stackowl db query`, read-only, path-resolved, loud on missing/empty.
- [x] `scripts/db_query.sh` -- delete; update every reference.
- [x] `scripts/migrate_lessons_from_lancedb.py`, `scripts/retired_log_messages.py` -- resolve through `StackowlHome`.
- [x] `AGENTS.md` -- short runtime-state section.
- [x] `AGENTS.md` + `tests/db/test_time_is_stored_one_way.py::test_the_convention_is_written_where_a_human_reads_it` -- that test is red because it reads the root `CLAUDE.md` deleted in `34e022da`; the "one clock, one format" timestamp convention it pins moves into `AGENTS.md` (a short conventions section) and the test reads it there -- repairs a pre-existing red test (owner told 2026-09-12).

**Acceptance Criteria:**
- Given the owner's box with the 0-byte stray, when the platform restarts and the health sweep runs, then the stray is gone, the heal is logged, and the real database is untouched.
- Given a fresh shell, when `stackowl db query "select count(*) from jobs"` runs, then it answers from `~/.stackowl/workspace/stackowl.db` and prints that path.

## Implementation Notes

- Q29 landed as `0ce773cc`; baseline re-captured at `0ce773cc`. Runs in an isolated worktree so the jobs-fix merge and restart can happen in the main tree meanwhile.
- Approved by the owner 2026-09-12 ("Approve both", J1–J3 "yes to all").

- Implemented in worktree branch `worktree-agent-ae1c1adb6ca786ee4`. Design choices recorded: the stray contributor checks only top-level files in the StackOwl home (browser-profile cert9/key4 databases, backups and restore snapshots live deeper and are legitimate); read-only opens also refuse ATTACH because `mode=ro` still creates a file through `ATTACH`/`VACUUM INTO` (proven); `scripts/duplicate_answers.py` also routed through `StackowlHome`; the shell-script count guard was already red at baseline (9 < 10) and now proves its scan on files it creates. Spec suite: 206 passed, 1 failed; tripwire gate 694 passed, 2 failed — both pre-existing at baseline `0ce773cc` in `control_plane/` (from the Q29 commit), fixed separately in the main tree.

- Merged to main as `c0a67831` after tripwires 700 passed / spec suites 251 passed / mypy clean (6 ruff findings all in untouched files, queued). Live acceptance 2026-09-12 21:38 CDT after ./start.sh: healer removed `~/.stackowl/stackowl.db` (0 bytes, last modified 2026-09-12T00:38:25Z) and wrote audit `health.stray_database_removed` — AC1 proven. REGRESSION found live, caused by the review scope patch (scan the db's own directory): a legitimate 14.9 MB `workspace/stackowl-backup-pre-negative-purge-20260625.db` reads as a data-holding stray → degraded every sweep. Sent back to the implementer: data-holding strays reported only at the home root; the db's own directory acts on empty strays only; backup writers named. Push held until fixed.

## Spec Change Log

## Review Triage Log

Review (2026-09-12). BH = blind hunter, VG = verification gap, EC = edge-case hunter. P = patch (sent to the implementer), R = rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| BH1 | Unlink inside `health_check()`: any `collect()` (sweep and dashboard health route) deletes, bypassing heal-then-recheck | medium | `control_plane/server.py:1821` and `assembly.py:883` both collect | P (detect-only contributor + healer) |
| BH2 | Recurrence invisible: removal returns ok, one INFO line, no count | medium | quiet all-healthy exit | P |
| BH3 | A just-opened 0-byte database (no journal yet) is deleted; writer loses data on POSIX | medium | no age check | P |
| BH4 | "Read-only never creates a file" false for WAL (creates -wal/-shm; TEMP tables allowed); fixture uses rollback journal | medium | reviewer probe on SQLite 3.37.2; `db/pool.py:56` WAL | P (narrow claim, WAL fixture) |
| BH5 | `DbContributor` reports a 0-byte live database ok while the query command refuses it | medium | `SELECT 1` succeeds on an empty file | P |
| BH6 | Other plain `sqlite3.connect` readers still create files (audit command, TUI parliament panel, `db backup`, `db restore` on a mistyped path) | medium | `cli/app.py:435,468`; `commands/audit.py:210` | P (+ tripwire) |
| BH7 | ATTACH/VACUUM hint printed for every SQL error | low | direct correction | P |
| BH8 | `| head` BrokenPipe traceback; row count only at DEBUG | low | direct correction | P |
| BH9 | Scripts tripwire re-adds a `scanned >= 10` floor this diff removed elsewhere | low | contradicts the shell test's own docstring | P |
| BH10 | Scripts tripwire misses f-string, `expanduser`, aliased `Path` spellings | low | matcher is exact-constant | P |
| BH11 | Old-helper reference scan narrower than claimed (spec file, `.claude/`, tests, other extensions) | low | spec mention is a planning record; no live reference found; widening adds complexity | R |
| BH12 | Test depends on a block `bmad-project-context` rewrites; stale stamp; docstring cites deleted CLAUDE.md | medium | AGENTS.md generated-block marker | P |
| BH13 | Stray holding data reported `down` (reserved for broken critical subsystems) | medium | `health/aggregator.py:83` | P (degraded) |
| BH14 | CLI `stackowl health` never registers the stray contributor | low | `cli/app.py:296-335` | P |
| BH15 | No end-to-end sweep test against a planted decoy | medium | duplicate of VG1 | P |
| VG1 | Assembly wiring checked by name only; wrong args ship green | medium | pre-verified | P |
| VG2 | Red `test_every_arrived_failure_asks_the_one_place_that_decides` | n/a | pre-existing from `0ce773cc` (`control_plane/password.py:736`); fixed separately in main | R (not this change) |
| VG3 | Scripts tripwire floor | low | duplicate of BH9 | P |
| EC1 | Case-insensitive filesystem makes the live db look like a stray | medium | resolve() keeps case | P (`samefile`) |
| EC2 | Open-but-unwritten stray deleted | medium | duplicate of BH3 | P |
| EC3 | Concurrent removal → FileNotFoundError reported as failed removal | low | direct correction | P |
| EC4 | TEXT with tab/newline breaks rows; BLOB printed as repr | low | direct correction | P |
| EC5 | Empty SQL exits 0 with empty stdout | low | direct correction | P |
| EC6 | BrokenPipe traceback | low | duplicate of BH8 | P |
| EC7 | WAL sidecars | medium | duplicate of BH4 | P |
| EC8 | Tripwire spellings | low | duplicate of BH10 | P |
| EC9 | Tripwire floor | low | duplicate of BH9 | P |
| EC10 | AGENTS.md generated block | medium | duplicate of BH12 | P |
| EC11 | Claim "finds databases under the home": only the root is scanned | medium | nested legitimate DBs exist (browser profiles, backups) | P (also scan the db's own directory, non-recursive) |

Cascade: no intent_gap or bad_spec entries; patches sent to the implementer.

## Verification

**Commands:**
- `uv run pytest tests/paths tests/health tests/test_no_python_module_reimplements_the_home.py -q` -- expected: all pass
- `uv run ruff check src/stackowl/health src/stackowl/cli scripts && uv run mypy src/stackowl/health src/stackowl/cli` -- expected: clean

**Manual checks (if no CLI):**
- After restart: `ls -la ~/.stackowl/stackowl.db` → absent; boot/health log shows the heal; `stackowl db query` answers with the workspace path.

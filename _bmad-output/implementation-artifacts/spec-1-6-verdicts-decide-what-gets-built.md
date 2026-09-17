---
title: 'Story 1.6: Verdicts decide what gets built'
type: 'feature'
created: '2026-09-17'
status: 'done'
baseline_revision: '7606612ea5eec1520f7953902252479a24717474'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '_bmad-output/implementation-artifacts/epic-1-context.md'
  - 'docs/agentic-os-dashboard/full-picture.md'
  - 'docs/agentic-os-dashboard/architecture/ARCHITECTURE-SPINE.md'
  - 'spikes/bridge/README.md'
warnings: ['oversized']
deferred: []
---

<intent-contract>

## Intent

**Problem:** Stories 1.1-1.5 wrote raw pass/fail result files under `spikes/bridge/results/` (B1, the carrier half of B2, B5), but nothing turns them into a verdict per spike and device class against `docs/agentic-os-dashboard/full-picture.md` §9 and the architecture spine's pass criteria, and a device class with no result file has no defined "not run" reporting -- so later epics have no single, honest evidence document to build on.

**Approach:** Add a `verdict.py` module to the throwaway kit (`spikes/bridge/bridge_spike/`) that scans `spikes/bridge/results/` for each of B1, B2 (carrier half), and B5 against four required real-device classes (`iphone-ios26`, `iphone-ios27`, `android-chrome`, `desktop-chrome` -- matching the epic context's own "stock iPhone iOS 26.4+ and iOS 27, Android Chrome 148+, desktop Chrome" list), scores whatever result file exists (or reports "not run" when none does), and writes `docs/agentic-os-dashboard/spikes/epic-1-verdicts.md` listing, per spike and device class: the verdict, the failing steps, whether the spike's gating ADs are confirmed or stay provisional, and the binding fail branch. Wire it as a new `kit.py verdict` subcommand.

## Boundaries & Constraints

**Always:**
- A device class with no matching result file is reported as **NOT RUN** -- never as a pass, and never silently omitted from the report.
- Score a result file's own recorded fields only -- never infer or fabricate a pass/fail the file's own data doesn't support. A malformed or unrecognized result file is reported as a failure with the parsing problem named, never silently skipped and never treated as a pass or as "not run".
- Required device classes for all three spikes are exactly: `iphone-ios26` (stock iPhone, iOS 26.4+, Safari tab and installed app), `iphone-ios27` (stock iPhone, iOS 27, Safari tab and installed app), `android-chrome` (Android, current Chrome stable 148+), `desktop-chrome` (desktop Chrome) -- looked up as `results/{B1|B2-carrier|B5}-{device-class-id}.json`.
- Any other `{B1|B2-carrier|B5}-*.json` file already under `results/` (the automated/build-host check files Stories 1.1-1.5 produced) is reported separately, per spike, as automated/build-host evidence -- clearly labeled as a prerequisite, never counted as satisfying a required device-class row.
- Reuse `RESULTS_DIR` from `bridge_spike.server` rather than re-declaring the path (same drift-avoidance precedent as `check.py` importing `SECURITY_HEADERS`).
- Fail-branch text and gating AD lists are the architecture spine's own verbatim wording (`ARCHITECTURE-SPINE.md` rows 740, 741, 744) -- never paraphrased into something narrower or broader.
- 4-point structured logging (entry, decision, step, exit) on every new method that does real work; no bare/silent `except` -- a read or parse failure is logged and turned into a visible finding in the report, never swallowed.
- `write_verdicts()`'s `results_dir`/`output_path` parameters default to `None` and fall back to the module-level `RESULTS_DIR`/`DEFAULT_VERDICTS_PATH` globals *read inside the function body* (not baked into the signature at import time), so tests can `monkeypatch.setattr` those module globals the same way `tests/test_server.py` already does for `RESULTS_DIR`.

**Never:**
- Never touch `src/stackowl/` -- this stays throwaway kit work under `spikes/bridge/`.
- Never build a new device-result submission UI/endpoint for B2/B5 (the kit's `/api/results` route only ever writes `B1-*.json`, by design, per `server.py:416`) -- that gap belongs to a future story/the operator, not this one. This story only scores whatever result files already exist or will exist under that filename convention.
- Never let a malformed result file silently count as "not run" (a device class WAS attempted; the file is just broken) or as a pass.
- Never modify any Story 1.1-1.5 file's behavior (`server.py`, `check.py`, the static pages, the frontend) beyond `kit.py`'s own CLI wiring.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| No result file for a device class | `results/B1-iphone-ios26.json` absent | That row's verdict is `NOT RUN`; ADs stay provisional; fail branch shows `n/a -- not run` | n/a |
| Automated `steps` schema, all true | `results/B1-desktop-chrome.json` (or any spike) has `{"steps": {...all true...}, "ok": true}` | Verdict `PASS`, failing steps empty, gating ADs `confirmed` | n/a |
| Automated `steps` schema, one false | `steps` dict has one `false` entry | Verdict `FAIL`, that step name listed under failing steps, ADs `provisional`, spike's fail branch shown | n/a |
| Human-submitted `passed` schema (no `steps`) | `{"device_class": "desktop-chrome", "passed": false, "note": "..."}` | Verdict `FAIL`; failing steps notes there is no per-step breakdown and quotes the submitted note | n/a |
| Malformed JSON at the expected path | File exists but `json.loads` raises | Verdict `FAIL`; failing steps names the parse error; a WARNING is logged | Reported, never raised uncaught, never silently skipped |
| Extra automated/build-host file present | e.g. `B1-passkey-desktop-chrome-automated.json` | Listed separately under that spike's "automated/build-host evidence", never merged into a required device-class row | n/a |

</intent-contract>

## Code Map

- `spikes/bridge/bridge_spike/server.py:51` -- `RESULTS_DIR` constant to import and reuse (never re-declare the path).
- `spikes/bridge/bridge_spike/server.py:398-416` -- `_handle_submit_result`: confirms the human-facing `/api/results` route only ever writes `B1-{device_class}.json` (hardcoded `B1-` prefix) -- there is no analogous submission path for B2/B5 today; the verdict command must not assume one exists, just look for the filename convention.
- `spikes/bridge/bridge_spike/check.py:1391,1405,1419,1434,1448` -- the five automated result filenames Stories 1.1-1.5's `check.py` actually writes today (`B1-build-host-chromium.json`, `B1-passkey-desktop-chrome-automated.json`, `B1-push-mic-desktop-chrome-automated.json`, `B2-carrier-desktop-chrome-automated.json`, `B5-desktop-chrome-automated.json`), each with a `{"ok": bool, "steps": {...}, "detail": {...}}` shape -- reference shape for the `steps`-schema scoring branch.
- `spikes/bridge/results/B1-desktop-chrome.json` (gitignored, present on disk today) -- reference shape for the human-submitted `{"device_class", "passed", "note"}` schema (no `steps` key) -- the other scoring branch.
- `docs/agentic-os-dashboard/architecture/ARCHITECTURE-SPINE.md:740` -- B1 row: Gates `AD-13, AD-14, AD-16, AD-17, AD-19, AD-37`; Fail branch: "If B1 fails, the owner decides the path at that time (no preset fallback), given the failing step and device class."
- `docs/agentic-os-dashboard/architecture/ARCHITECTURE-SPINE.md:741` -- B2 row: Gates `AD-6, AD-9, AD-11, AD-12, AD-31, AD-35, AD-38`; Fail branch: "If aioquic interop fails, the carrier is SSE-only until a maintained WebTransport stack is chosen. A client class that shows silent gaps uses SSE + POST. The retention default and budgets are lowered until memory stays bounded."
- `docs/agentic-os-dashboard/architecture/ARCHITECTURE-SPINE.md:744` -- B5 row: Gates `AD-36`; Fail branch: "The failing construct is removed from the build; the policy is never relaxed."
- `docs/agentic-os-dashboard/full-picture.md:190-191` -- §9 table rows for B1 and B2 pass/fail prose (B5 has no §9 row -- only the spine table covers it; the generated report must not fabricate a §9 citation for B5).
- `_bmad-output/implementation-artifacts/epic-1-context.md:21` -- names the four required device classes verbatim ("stock iPhone iOS 26.4+ and iOS 27, Android Chrome 148+, desktop Chrome").
- `spikes/bridge/tests/test_server.py:126-149` -- precedent for testing against `RESULTS_DIR` via `monkeypatch.setattr(server_module, "RESULTS_DIR", tmp_path)`, the same pattern this story's tests should follow to avoid touching the real `results/` directory.
- `spikes/bridge/tests/test_kit_cli_output.py` -- precedent for testing `kit.py` CLI-level behavior via `import kit` (pytest inserts `spikes/bridge` on `sys.path` because `spikes/bridge` itself has no `__init__.py`) and `capsys`.
- `spikes/bridge/kit.py:283-291,308-318` -- `cmd_check`/`build_parser`: the subcommand-wiring pattern to copy for a new `verdict` subcommand; `main()`'s `if args.name is None:` (`kit.py:322`) must become `if getattr(args, "name", None) is None:` since the new `verdict` subparser has no `--name`/`--port` (it never talks to a server).

## Tasks & Acceptance

**Execution:**
- [x] `spikes/bridge/bridge_spike/verdict.py` -- new -- required-device-class registry (`iphone-ios26`, `iphone-ios27`, `android-chrome`, `desktop-chrome`), per-spike spec (`B1`, `B2-carrier`, `B5` with their gate ADs and verbatim fail-branch text), a result-file scorer handling both the `steps` schema and the `passed` schema (and malformed JSON) with 4-point logging, a `build_epic_verdicts(results_dir)` aggregator, and `render_markdown(...)`/`write_verdicts(...)` to produce `docs/agentic-os-dashboard/spikes/epic-1-verdicts.md`.
- [x] `spikes/bridge/tests/test_verdict.py` -- new -- covers every I/O matrix row above against a `tmp_path` results directory (never the real `spikes/bridge/results/`), plus: automated/build-host files never counted as a required device-class row, ADs read `confirmed` only on a full pass and `provisional` otherwise, and the rendered markdown never shows `PASS` on a not-run row.
- [x] `spikes/bridge/kit.py` -- edit -- add a `verdict` subparser (no `--name`/`--port`) wired to a new `cmd_verdict` that calls `verdict.write_verdicts()` and prints the output path; fix `main()`'s `args.name is None` check to `getattr(args, "name", None) is None` so the new subcommand (with no `--name` attribute) doesn't crash.
- [x] `spikes/bridge/tests/test_kit_cli_output.py` -- edit -- add a test that `kit.build_parser()` routes `verdict` to `cmd_verdict`, and that `cmd_verdict` prints the path `verdict.write_verdicts()` returns (monkeypatch `kit.verdict.write_verdicts` to avoid touching real files).
- [x] `spikes/bridge/README.md` -- edit -- document `verdict.py`, the `kit.py verdict` subcommand, the four required device classes, and that a missing result file reports "not run".

**Acceptance Criteria:**
- [x] Given `spikes/bridge/results/` has no file for a required device class, when `uv run spikes/bridge/kit.py verdict` runs, then that device class's row in `docs/agentic-os-dashboard/spikes/epic-1-verdicts.md` reads `NOT RUN`, never a pass.
- [x] Given a `steps`-schema result file with one `false` step, when the verdict command scores it, then the report's failing-steps column names that step and the spike's own verbatim fail branch is shown for that row.
- [x] Given a `passed`-schema (human-submitted) result file, when the verdict command scores it, then it is scored from the `passed` flag (no `steps` key expected), and a `false` value is reported as a failure noting no per-step breakdown exists.
- [x] Given an automated/build-host result file (e.g. `B1-build-host-chromium.json`), when the report is generated, then it appears under that spike's separate automated-evidence listing, never as one of the four required device-class rows.
- [x] Given the full test suite for this file, when it runs, then every I/O matrix row above is covered by a passing test.

## Spec Change Log

## Review Triage Log

- **[patch, high, fixed] verdict.py:151-155, 207-211 -- non-UTF-8 result file crashes the whole report (Edge Case Hunter E1/E2).** Verified by direct repro: `score_result_file`/`_read_ok_field` call `path.read_text()` inside `except OSError` only; `UnicodeDecodeError` is a `ValueError` subclass and isn't caught. Reproduced live: writing invalid-UTF-8 bytes to a required-row file made `build_epic_verdicts()` (and therefore `write_verdicts`/`cmd_verdict`) raise uncaught, killing generation for every spike/device-class, not just the bad file -- directly against the I/O matrix's "malformed result file... Reported, never raised uncaught, never silently skipped." Fixed: `UnicodeDecodeError` now caught alongside `OSError` in both functions; re-verified live -- the same repro now returns `FAIL` with the exact codec error named, no crash. 3 new regression tests added and passing.
- **[patch, low, fixed] verdict.py score_result_file -- genuine FAIL logged at DEBUG, malformed file logged at WARNING (Blind Hunter #7).** Verified: lines ~175-179 and ~194 log real spike failures via `logger.debug`, while parse/format problems use `logger.warning` -- inverted priority for a document whose main signal is spike failure. Fixed: both FAIL-outcome branches now use `logger.warning`; PASS branches stay `logger.debug`.
- **[patch, low, fixed] verdict.py score_result_file -- no "step" stage in its 4-point logging (Edge Case Hunter E4).** Verified against the spec's own "4-point structured logging (entry, decision, step, exit) on every new method that does real work": `score_result_file` has entry/decision/exit debug-or-warning calls but no distinct step-stage log between parsing and schema dispatch, unlike `build_epic_verdicts`'s full trace. Fixed: a step-stage `logger.debug` call added right after successful JSON parse, before schema dispatch.
- **[patch, medium, fixed] test_verdict.py -- no render-level test asserts a PASS row's Fail-branch cell reads "—" or its Gating-ADs cell reads "confirmed" (Verification Gap A+B).** Gap finding, filed pre-verified. I independently confirmed the *code* is already correct (`fail_branch=None if verdict == "PASS" else spec.fail_branch` at verdict.py:263, rendered via `_render_cell(row.fail_branch or "")` at verdict.py:330; `ads_cell = "confirmed" if row.ads_confirmed else "provisional"` at verdict.py:329) -- no live bug, but a regression in either ternary would ship into the evidence document undetected since every existing render test only inspects the Verdict column or a NOT-RUN/empty-results case. Graded medium because the consequence if it did regress (a passing row appearing to carry the fail-branch text, or ADs confirmed/provisional swapped) is exactly the kind of misleading evidence-document defect the story exists to prevent. Fixed: `test_rendered_markdown_pass_row_shows_confirmed_ads_and_no_fail_branch_text` added, asserting both cells exactly.
- **[patch, low, fixed] tests/test_verdict.py::test_malformed_json_row_is_never_reported_as_not_run -- `_write_raw` variable name (Blind Hunter #10).** Verified: the underscore prefix conventionally signals "unused," but the variable is read later (`row.source_path == _write_raw`). Cosmetic, test-only, but the fix is a one-line rename so it survives the low-finding auto-reject. Fixed: renamed to `bad_path`.
- **[defer, medium] `spikes/bridge/results/B1-desktop-chrome.json` scores as a `desktop-chrome` PASS/confirmed row, but its own `note` field reads "Automated build-host Chromium check." (Blind Hunter #4).** Verified on disk: the file matches the required-row filename convention exactly (`B1-desktop-chrome.json`, no `-automated` suffix) and carries `"passed": true`, so `verdict.py` correctly scores it per its own documented contract (score the file's own recorded fields; match by filename, not content). The mismatch is a pre-existing data-provenance issue in the real, gitignored `results/` directory -- this file already existed before this story's diff (the spec's own Code Map cites it as "present on disk today") -- not a defect in the new `verdict.py` logic. The spec's Boundaries explicitly forbid building a new submission/validation mechanism for this story, so a code fix here would be out of the story's scope; flagging for the operator before any real commit of `epic-1-verdicts.md` to `main` is the right remedy.
- **[false] `cmd_verdict()` always returns 0 even when the report contains FAIL rows (Blind Hunter #2).** Refuted by the spec itself: `## Verification` states `uv run spikes/bridge/kit.py verdict` is expected to "exit 0, print the output path" unconditionally -- exit-0-always is the spec's own explicit, deliberate contract for this report-generating command, not a gap.
- **[false] `cmd_verdict()` calls `write_verdicts()` with no CLI flags to override `results_dir`/`output_path` (Blind Hunter #3).** Refuted by the spec itself: `## Tasks & Acceptance` explicitly specifies `cmd_verdict` "calls `verdict.write_verdicts()`" with no argument-passing requirement; no CLI flag was ever asked for.
- **[false] `epic-1-verdicts.md` was committed to `main` with no staleness guard (Blind Hunter #9).** Refuted: verified via `git status` that the file is untracked on disk, matching the spec's explicit instruction that committing a real run is an operator action after real-device testing. Nothing in this review turn committed it (an earlier `git add -A -N` used only to stage a diff for review was reverted with `git reset`).
- **[low, rejected] `main()`'s `getattr(args, "name", None)` fix still resolves a default install name via `socket.gethostname()` for the `verdict` subcommand even though `cmd_verdict` never reads `args.name` (Blind Hunter #1).** Verified: `_default_install_name()` is a cheap local hostname lookup with no network/mDNS cost, and the result is discarded for `verdict`. Rejected as low: unlikely to be noticed in everyday use (no observable effect) and the fix requires adding a new branch/guard, failing the low-finding trivial-fix bar.
- **[low, rejected] `score_result_file`'s `passed` schema never cross-checks the file's own `device_class` field against the filename (Blind Hunter #5).** Verified: `_handle_submit_result` (`server.py:398-416`) derives the output filename directly from the submitted `device_class` field (`RESULTS_DIR / f"B1-{device_class}.json"`), so the only real submission path can never produce a mismatch -- only manual file tampering could. Rejected as low: unlikely in everyday use, and the fix (a new validation branch plus a new failure mode) is more than a direct correction.
- **[low, rejected] Gate citations (spine rows 740/741/744, full-picture.md §9) are hardcoded literal strings with no test tying them to the live documents (Blind Hunter #6).** Real but unexceptional: this matches how the rest of the throwaway kit already cites line numbers in comments/docstrings (e.g. `check.py`'s own references to `server.py:416`) with no doc-sync test anywhere in the codebase. Rejected as low: the fix (a doc-drift-detection test) is well beyond a direct correction.
- **[low, rejected] `_read_ok_field` treats a present-but-non-boolean `"ok"` value identically to an absent one, with no log line either way (Blind Hunter #8).** Verified against verdict.py:215-216. Rejected as low: every real writer in this codebase (`check.py`'s five automated files) always writes a boolean `ok`, so this is unlikely to be encountered, and the fix (a new branch plus a new log call) is more than a direct correction.
- **[low, rejected] `cmd_verdict()` has no try/except around `verdict.write_verdicts()`, so an infra-level failure (permission denied, disk full) surfaces as a raw traceback instead of a `return 1` (Edge Case Hunter E3).** Verified against `kit.py`: `cmd_start`/`cmd_renew` show the same "let it propagate" pattern for infra-level exceptions (only `cmd_check`'s own domain-level PASS/FAIL uses `return 1`), so this isn't a deviation from an established convention. Rejected as low: unlikely in everyday local-operator use, and the fix (a new try/except plus an error-reporting branch) is more than a direct correction.

## Design Notes

- **Why four fixed device-class ids instead of discovering arbitrary device classes from disk:** the epic context (`epic-1-context.md:21`) and the operator checklist name exactly four real-device classes the owner is expected to run; an open-ended discovery scheme would let a typo'd or ad hoc `device_class` string silently masquerade as a required row. Any *other* file matching `{spike}-*.json` is still surfaced, just as separate automated/build-host evidence, so nothing on disk is ever hidden from the report.
- **Why `results_dir`/`output_path` default to `None` and are resolved from module globals inside the function body:** so `monkeypatch.setattr(verdict_module, "RESULTS_DIR", tmp_path)` works for a CLI-level test of `cmd_verdict()` the same way `tests/test_server.py` already patches `RESULTS_DIR` for `_handle_submit_result` -- a plain `def write_verdicts(results_dir: Path = RESULTS_DIR, ...)` would bind the pre-patch value at import time and defeat that pattern.
- **B5 has no `full-picture.md` §9 row:** only B1 and B2 appear in that table; B5 only appears in the architecture spine's own gate table (`ARCHITECTURE-SPINE.md:744`). The generated report cites `full-picture.md` §9 for B1/B2 and the spine alone for B5, rather than fabricating a §9 citation that doesn't exist.

## Verification

**Commands:**
- `uv run --with cryptography --with aiohttp --with webauthn --with python-telegram-bot --with pywebpush --with http-ece --with requests --with aioquic python -m pytest spikes/bridge/tests` -- expected: all existing tests plus the new `test_verdict.py` and the extended `test_kit_cli_output.py` pass.
- `uv run spikes/bridge/kit.py verdict` -- expected: exits 0, prints the output path, and `docs/agentic-os-dashboard/spikes/epic-1-verdicts.md` is generated correctly reflecting the real current `spikes/bridge/results/` contents (this run's output is for verification only in this story -- committing it to `main` for real is an operator action after real-device testing, per the epic's own `operator_actions`).

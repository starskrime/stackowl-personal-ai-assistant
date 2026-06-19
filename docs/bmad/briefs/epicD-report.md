# Epic D — Cleanup/Polish Commits Report

## Status: COMPLETE — 4 commits shipped

| # | Short hash | Subject |
|---|-----------|---------|
| 1 | `f09d46c7` | `chore(channels): remove unused telegram/discord/whatsapp slash bridges` |
| 2 | `5a7d22a5` | `fix(commands): /owls surfaces no-DB DNA skip instead of silent` |
| 3 | `c8e587ed` | `fix(commands): /skill add git URL detection handles more URL shapes` |
| 4 | `b7d10150` | `fix(commands): /notifications description matches implemented scope` |

## Test summary

- **Commit 1**: Grep confirmed zero non-test imports of the 3 deleted bridges; 396 channel + journey tests pass; 1 pre-existing failure in `test_no_mock_only_command_tests.py` (unrelated, verified via stash).
- **Commit 2**: 4 new dispatch-level tests in `tests/journeys/commands/test_owls_command.py` — add/edit with `db=None` asserts note present; add/edit with `db` present asserts note absent. All pass.
- **Commit 3**: 13 parametrized cases in `tests/skills/test_git_repo_heuristic.py` covering owner/repo, trailing slash, `.git` suffix, `git@` SSH, known forges, archives, self-hosted, and edge paths. All pass.
- **Commit 4**: No behavior change; ruff + mypy clean; 105 journey tests pass.

## Skips

None — all four commits were necessary.

## Key findings

- The Slack bridge at `channels/slack/slash_bridge.py` is the only live bridge (imported by `startup/orchestrator.py`); the three deleted files had zero non-test imports.
- `_NO_DB` constant in `owls_command.py` was genuinely orphaned — `_add()` and `_edit()` silently skipped DNA persistence when `_db is None` with only a debug log. Fix: inline `suffix` appended to both return strings.
- `_looks_like_git_repo` required `len(segs) == 2` exactly, so `owner/repo/` (trailing slash) returned `False` and any `.git`-suffixed URL on a non-forge host was also missed. Fixed to: `.git`/`git@` marker first, then `len(segs) >= 2` on known forges.
- `/notifications` description "View notification history." overclaimed; narrowed to "View missed notifications." matching the single `missed` subcommand.

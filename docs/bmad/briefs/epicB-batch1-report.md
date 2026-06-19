# Epic B Batch 1 — Slash Command Wiring Report

Date: 2026-06-19
Branch: feat/slash-command-overhaul

## Summary

Seven atomic commits landing /why, /whoami, /audit, /brief, and /webhook into
the DI command registry, plus moving the single registration point to after
SchedulerAssembly.build() so all scheduler-dependent deps are available.

## Commits

| # | Hash | Description |
|---|------|-------------|
| 1 | 7453c8e8 | refactor(startup): move register_all_commands after scheduler; populate full CommandDeps |
| 2 | 6f07c553 | feat(commands): wire /why |
| 3 | 710b5666 | fix(commands): wire /whoami + return advertised role/tier/provider |
| 4 | 2a64f662 | feat(commands): wire /audit with audit_logger |
| 5 | fe271af3 | feat(commands): wire /brief with MorningBriefHandler |
| 6 | 13c3c22d | feat(commands): wire /webhook + honest description |
| 7 | (this commit) | docs(epicB): batch 1 wiring report |

## Changes per file

### src/stackowl/startup/orchestrator.py
- Removed early `register_all_commands` block (was after NotificationAssembly, missing
  scheduler deps: audit_logger, parliament, scheduler, morning_brief_handler,
  preference_store, plugin_registry, integration_registry).
- Added single registration point after `SchedulerAssembly.build()` with full
  CommandDeps population.

### src/stackowl/commands/assembly.py
- Added `_register_di_commands` blocks for: /why, /whoami, /audit, /brief, /webhook.
- Added `cast()` calls for audit_logger and morning_brief_handler (typed `object | None`
  in CommandDeps for import-cost reasons) to satisfy mypy at call sites.

### src/stackowl/commands/whoami.py
- Added `owl_registry: OwlRegistry | None = None` constructor parameter.
- `handle()` now appends role, model_tier, provider_name from the registry when present;
  gracefully degrades (shows basic info) if registry lookup fails.

### src/stackowl/commands/audit.py
- Removed `import logging` / `log = logging.getLogger(...)`.
- Added `from stackowl.infra.observability import log`.
- `__init__` signature changed to `audit_logger: AuditLogger | None = None`.
- `handle()` returns `"✗ /audit: not configured"` when logger is None.
- All `log.debug/error` calls changed to `log.gateway.debug/error`.
- `float(ts_raw)` -> `float(str(ts_raw))` to satisfy mypy on `object` type.

### src/stackowl/commands/brief_command.py
- `__init__` signature changed to `handler: MorningBriefHandler | None = None`.
- `handle()` returns `"✗ /brief: not configured"` when handler is None.

### src/stackowl/commands/webhook_command.py
- `__init__` signature changed to `db: DbPool | None = None, settings: Settings | None = None`.
- `handle()` returns `"✗ /webhook: not configured"` when either is None.
- Description softened from `"Manage webhook sources"` to
  `"Show webhook source config instructions and audit disable requests"`.
- Added `assert self._db is not None` / `assert self._settings is not None` at tops of
  `_list()` and `_disable()` for mypy narrowing (only reachable post-guard).

## Test results

All 7 new dispatch journey tests pass (plus 1 xfail reachability guard, expected):

```
tests/journeys/commands/test_audit_command.py     3 passed
tests/journeys/commands/test_brief_command.py     3 passed
tests/journeys/commands/test_webhook_command.py   4 passed
tests/journeys/commands/test_whoami_command.py    2 passed
tests/journeys/commands/test_why_command.py       2 passed
tests/journeys/commands/test_reachability_guard.py  1 xfailed (expected)
```

16 passed, 1 xfailed — full journeys/commands/ suite.

## Lint / type-check

- `ruff check`: All checks passed (2 import-ordering fixes auto-applied).
- `mypy`: Success: no issues found in 5 source files.

## Pre-existing flakes (not caused by this batch)

Two tests fail only under specific ordering in the full suite but pass in
isolation — confirmed pre-existing by bisecting against the base branch:

- `tests/config/test_config_reload_safety.py::test_reload_rejected_keeps_prior_and_logs_error`
- `tests/journeys/test_budget_cap.py::test_durable_step_cap_stops_deterministically`

Both are test-isolation pollution from shared mutable state in some upstream
test; not introduced by this batch.

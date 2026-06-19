# Epic A Spine — Implementation Report

Branch: `feat/slash-command-overhaul`  
Date: 2026-06-19  
Status: **DONE**

---

## Commit 1 — `14625f09` — refactor(commands): single command assembler + SHIPPED_COMMANDS manifest

### Files created / modified

| File | Action |
|---|---|
| `src/stackowl/commands/assembly.py` | Created — `CommandDeps` dataclass + `register_all_commands()` |
| `src/stackowl/commands/manifest.py` | Created — `SHIPPED_COMMANDS` (29) + `EXEMPT_COMMANDS` |
| `src/stackowl/commands/registry.py` | Modified — `load_builtin_commands()` now re-registers cached `_CMD` instances after `CommandRegistry.reset()` |
| `src/stackowl/notifications/assembly.py` | Modified — `build()` now constructs but does NOT register the 4 notification commands |
| `src/stackowl/startup/orchestrator.py` | Modified — removed `load_builtin_commands()` + 3 scattered `create_and_register` calls; single `register_all_commands(deps)` call after `NotificationAssembly.build()` |
| `tests/commands/test_assembly.py` | Created — 7 tests asserting assembler behavior |
| `tests/notifications/test_notification_assembly.py` | Modified — `test_build_registers_four_router_dependent_commands` → `test_build_returns_four_router_dependent_command_objects` |

### SHIPPED_COMMANDS exact strings

```
# Pattern A (dependency-free, 8 live)
"help", "config", "settings", "cost", "tools", "provider", "tier", "browser"

# Pattern B DI (7 live, Epic A wired)
"skill", "memory", "owls", "focus", "urgent", "quiet", "notifications"

# Pattern B DI (14 to be wired, Epic B)
"agents", "agent", "reset", "permissions", "audit", "whoami", "why",
"brief", "parliament", "staged", "webhook", "connect", "disconnect", "plugins"
```

### EXEMPT_COMMANDS
```
"audit export"   # AuditExportCommand — space in name is scanner-unmatchable; folded into /audit in Epic B
```

### Key design decision: `load_builtin_commands()` re-registration fix

`importlib.import_module` is a no-op for already-cached modules, so the
module-level `_CMD = register_command(...)` lines do NOT re-execute after
`CommandRegistry.reset()`. Added a `sys.modules` walk at the end of
`load_builtin_commands()` to re-register any `_CMD` `SlashCommand` instances
from cached `*_command` modules. This is idempotent (register overwrites the
same slot) and fixes the pre-existing `test_provider_command.py` test that
would have broken without it.

### Insertion point in orchestrator

`register_all_commands(deps)` is called at orchestrator line ~456, immediately
after `notification_router = notification_components.router` — so `router` is
guaranteed to exist in `CommandDeps`. It runs BEFORE the channel loops and
Telegram `setMyCommands`.

### Tests run

```
uv run pytest tests/commands/ tests/notifications/test_notification_assembly.py \
  tests/test_memory_command_registration.py tests/test_owls_command_registration.py -q
# 59 passed in 33s
```

---

## Commit 2 — `9d300cef` — test(commands): reachability guard (xfail burndown) + drift + anti-mock lint

### Files created

| File | Purpose |
|---|---|
| `tests/journeys/commands/__init__.py` | Package init |
| `tests/journeys/commands/test_reachability_guard.py` | `test_every_shipped_command_is_reachable` — xfail(strict=True); passes when Epic B removes marker |
| `tests/journeys/commands/test_command_manifest_drift.py` | `test_no_command_subclass_is_unmanifested` — walks all SlashCommand subclasses, asserts each is in SHIPPED | EXEMPT |
| `tests/journeys/commands/test_no_mock_only_command_tests.py` | `test_no_direct_handle_or_eventbus_in_gateway_command_tests` — grep guard |

### Tests run

```
uv run pytest tests/journeys/commands/ -v
# 2 passed, 1 xfailed in 0.43s
```

The xfail is `strict=True`: if the test unexpectedly passes (all 29 wired too
early), CI fails — intentional behaviour to prevent silent partial states.

---

## Commit 3 — `71ccaf9b` — fix(commands): scanner tolerates leading whitespace before slash

### Change

`src/stackowl/gateway/scanner.py` line 29:

```python
# Before (broken)
_SLASH_CMD_RE = re.compile(r"^/(\w+)", re.UNICODE)

# After (fixed)
_SLASH_CMD_RE = re.compile(r"^\s*/(\w+)", re.UNICODE)
```

`@owl` and `!panic/!panic` precedence are unaffected — panic uses `re.search`
on the full text, and `@owl` uses `_AT_OWL_RE.match(text)` where `text` is
the NFC-normalised string. Both still fire correctly.

### Scanner tests

`tests/gateway/test_scanner_slash.py` — 13 cases:

| Input | Expected route | Expected target |
|---|---|---|
| `'/help'` | command | help |
| `' /help'` | command | help |
| `'\t/help'` | command | help |
| `'   /help'` | command | help |
| `'/help@MyBot'` | command | help |
| `'/provider list'` | command | provider |
| `'/unknowncmd'` | command | unknowncmd |
| `' /unknowncmd'` | command | unknowncmd |
| `'hello'` | owl | secretary |
| `'@hoot hi'` | owl | hoot |
| `''` | owl | secretary |
| `'   '` | owl | secretary |
| `'/panic'` | panic | panic |

```
uv run pytest tests/gateway/test_scanner_slash.py -q
# 13 passed in 0.10s
```

---

## Full batch test result

```
uv run pytest tests/commands/ tests/gateway/test_scanner_slash.py \
  tests/journeys/commands/ tests/notifications/test_notification_assembly.py \
  tests/test_memory_command_registration.py tests/test_owls_command_registration.py \
  tests/test_story_7_4.py tests/test_story_7_4b.py -q
# 96 passed, 1 xfailed in 78s
```

---

## Concerns

None. All three commits are behavior-preserving (15-command live registry
unchanged), ruff-clean, and mypy-clean on touched files. Pre-existing mypy
errors in `orchestrator.py` (lines 718, 1006) were present before this work
and are not introduced by these changes.

---

## Task-Review Fix Commit — `fb96f534` — fix(commands): thread registry through load_builtin_commands + stronger assembler type tests

Date: 2026-06-19

### Findings addressed

**Finding 1 (Important):** `load_builtin_commands()` always registered Pattern-A
`_CMD` instances to `CommandRegistry.instance()` (the global singleton), ignoring
the `registry` argument threaded from `register_all_commands`. When a caller passed
an explicit isolated registry (e.g. in tests after `CommandRegistry.reset()`),
Pattern-A commands landed in the singleton while Pattern-B DI commands landed in
the isolated registry — a split that could silently hide commands from the target
registry.

**Finding 2 (Important):** `tests/commands/test_assembly.py` only asserted command
*names* were present. A swap of two concrete types (e.g. `FocusCommand` stored under
`"urgent"`) would pass all existing tests silently.

**Finding 3 (Minor):** `src/stackowl/notifications/assembly.py` docstring (lines
19-20) still claimed the four notification commands "self-register via their
`create_and_register` factories" — false since the slash-command-overhaul; they
are now constructed in `NotificationAssembly.build()` and registered by
`register_all_commands`.

### Changes made

| File | Change |
|------|--------|
| `src/stackowl/commands/registry.py` | `load_builtin_commands` gains optional `registry: CommandRegistry \| None = None` param; all `_CMD` re-registrations now target `registry if registry is not None else CommandRegistry.instance()` instead of always the singleton. Docstring updated. |
| `src/stackowl/commands/assembly.py` | `register_all_commands` passes `registry=reg` to `load_builtin_commands(registry=reg)` so both Pattern-A and Pattern-B commands land in the same isolated target. |
| `src/stackowl/notifications/assembly.py` | Stale docstring bullet corrected — says commands are "constructed here and returned in `NotificationComponents`; they are registered centrally by `commands.assembly.register_all_commands`". |
| `tests/commands/test_assembly.py` | Added `test_registered_commands_are_correct_types`: builds full fake deps, calls `register_all_commands`, then asserts each of the 7 DI slots holds the correct concrete class (`FocusCommand`, `UrgentCommand`, `QuietHoursCommand`, `NotificationsMissedCommand`, `MemoryCommand`, `OwlsCommand`, `SkillCommand`). |

### Test run

```
uv run pytest tests/commands/test_assembly.py tests/journeys/commands/ \
  tests/notifications/test_notification_assembly.py \
  tests/commands/test_provider_command.py -q
# 39 passed, 1 xfailed in 17.98s
```

The `1 xfailed` is the reachability guard burndown marker (`test_every_shipped_command_is_reachable`
in `tests/journeys/commands/test_reachability_guard.py`) — expected until Epic B
wires all 29 commands.

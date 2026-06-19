# Epic B Batch 3b — Implementation Report

Branch: `feat/slash-command-overhaul`  
Date: 2026-06-19

## Status: COMPLETE — 4 commits shipped, 58 journey tests passing

---

## Commits

| # | Hash | Title |
|---|------|-------|
| 1 | `bb31b178` | feat(commands): wire /staged + reject existence-check |
| 2 | `487d1d42` | feat(commands): wire /plugins + enable/disable existence-check |
| 3 | `31c255c9` | feat(commands): wire /connect + /disconnect with public credential deletion |
| 4 | `1870ac2c` | test(commands): prove all 29 shipped commands register |

---

## Commit 1 — `/staged`

**Wired:** `StagedCommand(bridge, promoter, event_bus)` registered unconditionally.

**Honesty fix:** `_reject` previously called `bridge.delete(fact_id)` with no existence check, returning `"✓ Rejected <id>"` for any bogus id. Fixed: `find_staged_by_id(bridge, fact_id)` is called first; returns `"✗ Staged fact not found: '<id>'"` if not found — only calls delete and claims success when a real fact is found.

**Type safety:** `__init__` now accepts `MemoryBridge | None` and `FactPromoter | None` (matching all other DI commands). `handle()` guards with an honest "not configured" return; sub-methods use `assert self._bridge is not None` to narrow for mypy.

**Tests:** `tests/journeys/commands/test_staged_command.py` — 4 tests:
- Real deletion on valid reject
- Honest not-found on bogus reject (NOT false success)
- List returns table
- Not-configured when bridge is None

---

## Commit 2 — `/plugins`

**Wired:** `PluginsCommand(plugin_registry)` registered unconditionally.

**Honesty fix:** `_handle_enable`/`_handle_disable` previously called `registry.set_enabled(name, ...)` unconditionally — a silent `UPDATE ... WHERE name = ?` no-op for missing names returned false success. Fixed: added `PluginRegistry.exists(name)` method (queries all rows, enabled + disabled via `SELECT 1 ... LIMIT 1`) and checks it before claiming success. Returns `"Plugin 'x' not found."` for absent names.

**Type safety:** `__init__` accepts `PluginRegistry | None`; None guard in `handle()`.

**Tests:** `tests/journeys/commands/test_plugins_command.py` — 6 tests:
- Enable existing plugin → success
- Enable bogus name → honest not-found
- Disable existing plugin → success  
- Disable bogus name → honest not-found
- List shows installed plugin
- Not-configured when registry is None

---

## Commit 3 — `/connect` + `/disconnect`

**Wired:** Both `ConnectCommand` and `DisconnectCommand` registered unconditionally.

**Honesty fix for `/disconnect`:** Old code used `hasattr(adapter, "_oauth")` to reach into the private attribute, silently skipping credential deletion for adapters without `_oauth` while still reporting `"✓ disconnected and credentials removed"` (false success).

Fixed via three-layer change:
1. **`IntegrationAdapter.delete_credentials() -> bool`** added to the ABC as a concrete non-abstract default returning `False` (backwards-compatible; adapters without oauth degrade gracefully)
2. **`GmailAdapter.delete_credentials()`** and **`GoogleCalendarAdapter.delete_credentials()`** implemented — delegate to `self._oauth.exists()` + `self._oauth.delete()`, return `True` when creds existed
3. **`DisconnectCommand`** calls the public `adapter.delete_credentials()` — reports `"credentials removed"` only when it returns `True`, otherwise honest `"(no stored credentials to remove)"`

**Tests:** `tests/journeys/commands/test_connect_command.py` — 6 tests:
- Connect lists adapters
- Disconnect with creds → calls delete_credentials, reports "removed"
- Disconnect no-creds → calls delete_credentials, honest "no stored credentials"
- Disconnect unknown service → not-found
- Connect not-configured when registry None
- Disconnect not-configured when registry None

---

## Commit 4 — All 29 reachable positive gate

**Test:** `tests/journeys/commands/test_all_29_reachable.py` — 1 positive (non-xfail) test that asserts `register_all_commands(CommandDeps()) == SHIPPED_COMMANDS` exactly.

This is the hard permanent gate: turns RED immediately if any command is accidentally unwired in future.

The xfail burndown guard (`test_reachability_guard.py`) is now XPASS(strict) — it will fail CI until its marker is removed in the final dedicated Epic B commit (NOT this batch's job per spec).

---

## Test Summary

```
58 passed in 15.34s   (tests/journeys/commands/, excluding xfail guard)
```

All new tests:
- `test_staged_command.py` — 4 passed
- `test_plugins_command.py` — 6 passed  
- `test_connect_command.py` — 6 passed
- `test_all_29_reachable.py` — 1 passed

---

## Concerns / Notes

1. **XPASS guard:** `test_reachability_guard.py::test_every_shipped_command_is_reachable` is now XPASS(strict=True) and fails CI. This is intentional per the spec — the final Epic B commit removes the xfail marker to turn it into a hard gate. That commit is NOT part of this batch.

2. **Pre-existing mypy errors:** Two pre-existing type errors remain in `assembly.py` (lines 248, 257: `scheduler` typed as `object` vs `JobScheduler | None`). These predate this batch and are unchanged.

3. **Google SDK no-untyped-call:** `gmail.py` and `google_calendar.py` have pre-existing `[no-untyped-call]` errors from `google.oauth2.credentials.Credentials` (no stubs). The new `delete_credentials()` methods are clean.

4. **`PluginRegistry.list()` is enabled-only:** The new `exists()` method correctly queries all rows (enabled + disabled) so that `enable` on a currently-disabled plugin is found rather than falsely reported as not-found.

---

## Batch 3b Fix Pass — Review Finding Remediation

Commit: `0932b623`  
Date: 2026-06-19

### IMPORTANT — Signing-key dual source eliminated

**Problem:** `CommandDeps.export_key` (assembly.py) was a parallel source of truth for the signing secret alongside the canonical `settings.governance.audit_export_key` (GovernanceSettings). Tests injected the key via `CommandDeps(export_key=...)`, bypassing settings entirely.

**Fix:**
- Removed `export_key: str | None = None` field from `CommandDeps`
- Removed the `deps.export_key`-preferring fallback block in `_register_di_commands`
- Assembly wiring simplified to one line: `deps.settings.governance.audit_export_key if deps.settings is not None else ""`
- Tests now inject the key via `monkeypatch.setenv("STACKOWL_GOVERNANCE__AUDIT_EXPORT_KEY", "test-signing-key")` + `Settings()` — the established project pattern for `BaseSettings` nested fields (direct kwarg construction does NOT work; `BaseSettings` ignores nested model kwargs and uses env/defaults)

### MINOR 1 — AuditLogger.db_path public property

Added `db_path` property to `AuditLogger` (logger.py). `audit.py:_fetch_all_audit_rows` now reads `self._logger.db_path` instead of `self._logger._db_path`. The `noqa: SLF001` comment removed.

### MINOR 2 — Staged not-configured assertion tightened

`test_staged_not_configured_when_bridge_none`: replaced bare `assert result` with `assert "not configured" in result.lower() or "✗" in result`.

### Test result

```
11 passed in 0.62s
  test_audit_command.py   — 7 passed
  test_staged_command.py  — 4 passed
  (test_reachability_guard.py included, still XPASS-pending marker removal)
```

### Notes

- Pre-existing mypy errors in assembly.py (scheduler arg-type, lines 240/249) are unchanged and predate this batch.
- `BaseSettings` nested-kwarg limitation: `Settings(governance=GovernanceSettings(audit_export_key="x"))` silently ignores the kwarg — env var injection via `monkeypatch.setenv` is the only reliable test pattern.

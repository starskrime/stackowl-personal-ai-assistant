# Epic B Batch 2 — Wiring Report

**Branch:** feat/slash-command-overhaul  
**Date:** 2026-06-19  
**Commits:** 4 atomic commits (ce51c0e9 → 06ae7a2b)

## Commands wired

| Command | Commit | Key changes |
|---------|--------|-------------|
| `/permissions` | `ce51c0e9` | Switch from bare `logging` to `log.gateway.*`; make all three deps None-tolerant; wire unconditionally |
| `/agents` | `405b0df0` | Wire with scheduler/db/event_bus; no-scheduler degrades honestly |
| `/agent` | `f298cd37` | Fix SIM108 ternary; lazy template load (no startup crash); add `provider_registry` + `parliament_session_store` to `CommandDeps`; wire unconditionally |
| `/parliament` | `06ae7a2b` | Wire with orchestrator/session_store/owl_registry/event_bus; uses `parliament_session_store` field |

## Test results

- 32 passed, 1 xfailed (reachability guard — expected until all 29 commands wired)
- 4 new test files: `test_permissions_command.py`, `test_agents_command.py`, `test_agent_command.py`, `test_parliament_command.py`
- 15 new test cases total across the 4 files

## Notable fixes

**permissions.py:** The original file used bare `logging.getLogger("stackowl.gateway")` creating a non-namespaced logger out of step with the rest of the codebase. Switched to `log.gateway.*` throughout. All three constructor params made Optional (were typed as required, crashed with None deps — violates the unconditional-registration invariant).

**agent_create_command.py (lazy template):** The eager `env.get_template(_TEMPLATE_NAME)` in `__init__` would crash the entire startup if the template file were absent. Moved to lazy load on first `_create()` call; stored `Environment` at init time (cheap), defer `get_template` until actually needed.

**agent_create_helpers.py (SIM108):** Collapsed 3-line if/else to a single ternary per ruff SIM108.

**CommandDeps:** Added `provider_registry` (for `/agent create`) and `parliament_session_store` (for `/parliament log`) as `object | None = None` to avoid heavy imports at assembly load time. Both use `cast()` at the call site.

## Registration invariant status

All 4 new commands register unconditionally (even when deps are None), matching the Epic A invariant: "shipped ⟺ registered regardless of wiring state."

## Remaining xfail

`test_reachability_guard.py::test_every_shipped_command_is_reachable` remains xfail(strict=True). After batch 2, the remaining unwired commands from SHIPPED_COMMANDS are: `reset`, `staged`, `connect`, `disconnect`, `plugins`. Wiring those will be the final batch needed to flip the guard green.

---

## Batch 2 Post-Review Fixes — commit `7fec379a`

**C1 (Critical — production wiring gap):** `orchestrator.py` was building `CommandDeps(...)` without `provider_registry` or `parliament_session_store`, so `/agent create` always returned "Provider registry not configured" and `/parliament log` always returned "Session store not configured" in production, even though both objects existed in scope.

Fix:
- Hoisted `SessionStore(db_pool)` to `parliament_session_store = SessionStore(db_pool)` (line ~722), used for both `ParliamentOrchestrator(session_store=parliament_session_store)` and `CommandDeps(parliament_session_store=parliament_session_store)`.
- Added `provider_registry=provider_registry` to `CommandDeps(...)` — already in scope at line 309.

**I1 (Important — silent template crash):** Wrapped the lazy `get_template(_TEMPLATE_NAME)` call in `agent_create_command.py` with a try/except that logs `log.scheduler.error("[commands] agent._create: template load failed", ...)` before re-raising. The existing generic `except Exception` handler in `handle()` then surfaces `"✗ /agent create: ..."` to the user.

**I2 (Important — getattr-on-None):** Replaced `getattr(self._settings, "autonomy_level", "medium")` in `permissions.py` with `self._settings.autonomy_level if self._settings is not None else "medium"` — explicit, mypy-clean.

**I3/M1/M2 (Minor — loose test assertions):**
- `test_agent_command.py::test_agent_confirm_no_scheduler`: `assert "not configured" in result or _NO_PENDING in result` → `assert _NO_SCHEDULER in result` (imports `_NO_SCHEDULER` from `agent_create_command`; scheduler guard fires before pending-proposal check).
- `test_agents_command.py::test_agents_no_subcommand_returns_usage`: `assert "Usage:" in result or "list" in result` → `assert "Usage:" in result`.
- `test_parliament_command.py::test_parliament_no_args_returns_usage`: `assert "Usage:" in result or "/parliament" in result` → `assert "Usage:" in result`.

**New tests added:**
- `test_agent_create_with_provider_registry_populated_skips_not_configured_guard` — proves C1: wiring `ProviderRegistry.from_settings(Settings(providers=[]))` into `CommandDeps` causes `/agent create` to bypass `_NO_PROVIDER` guard (mocked `get_by_tier` raises `RuntimeError`; asserts `_NO_PROVIDER not in result`).
- `test_agent_create_missing_template_returns_honest_error` — proves I1: patching `Environment.get_template` to raise `TemplateNotFound` causes result to start with `"✗ /agent create:"`.

**Final test count:** 42 passed, 1 xfailed (same reachability xfail).

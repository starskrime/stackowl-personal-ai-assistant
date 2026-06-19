# Defect Audit — Group 1: Config / System Commands

Auditor: Murat (Test Architect). Skeptical pass — "does the command ACTUALLY do its job?"
Scope: `help_command.py`, `config_command.py`, `settings_command.py`, `cost_command.py`,
`tools_command.py`, `provider_command.py`, `tier_command.py`, `browser_command.py`,
`whoami.py`, `why.py`. Repo root `/ssd/projects/stackowl-personal-ai-assistant`.

## Verdict table

| Command | File | Registered? | Reachable (CLI/TG) | Does its job? | Defects (file:line) | Real side-effect test? | Effort |
|---|---|---|---|---|---|---|---|
| help | help_command.py | YES (`_CMD = register_command` L43) | YES | YES | none material | NO direct test (only registry presence elsewhere) | n/a |
| config | config_command.py | YES (module-level L208) | YES | **PARTIAL — hot-reload dead** | bus=None in prod ⇒ `settings_reloaded` never emitted (L164–165); `/config set` returns `✓` with NO "restart required" when `hot_reload` defaults True (L166–172) ⇒ **lies about taking effect live** | YES for YAML write (helpers tested) / **NO** for live-reload side effect | medium |
| settings | settings_command.py | YES (module-level L99) | YES | **PARTIAL — event dead** | bus=None in prod ⇒ `settings_changed` never emitted (L87–91); writes YAML fine but nothing live picks it up | NO real side-effect test (no `tests/commands/test_settings*`) | small–medium |
| cost | cost_command.py | YES (module-level L136) | YES | YES | minor: short-lived DbPool won't see uncommitted in-flight writes (acceptable for SQLite) | **NO** (no `test_cost*`); privacy DELETE untested | small |
| tools | tools_command.py | YES (module-level L61) | YES | YES | `except Exception` swallows severity lookup → "?" glyph (L45–48) is acceptable degradation, not a defect | NO direct test | n/a |
| provider | provider_command.py | YES (module-level L299) | YES | **PARTIAL — hot-reload dead** | bus=None in prod ⇒ `_emit_reloaded` is a no-op (L113–115); message says "applies on the next reload/restart" so at least honest, BUT the live ProviderRegistry never reloads even though `provider_reload.py` handler exists and is subscribed (orchestrator L697–700) | YES (`test_provider_command.py`) **but masks the bug**: tests inject their own bus; `test_auto_registered_in_registry` checks presence only; dispatch test never asserts emission | medium |
| tier | tier_command.py | YES (module-level L135) | YES | YES (persists via PreferenceStore, in-mem fallback) | owner_key scoped to `session_id` not real owner (L26–30, documented TODO) → "propagates across all channels for same owner" claim in docstring is **not yet true** | NO direct test of persisted read-back | small |
| browser | browser_command.py | YES (module-level L181) | YES | MOSTLY | **`profile delete` swallows OSError then unconditionally returns "Deleted"** (L165–167) — overclaim on failed rmtree; `watch` subcmd is a read-only hint, never registers a job (L170–178) — matches help text "(read-only)" so honest but inert | NO direct test | small (delete) |
| whoami | whoami.py | **NO — UNREGISTERED / DEAD** | **NO** | **NO — unreachable** | file does NOT end in `_command.py` ⇒ skipped by `load_builtin_commands` (registry.py L91–92); never imported/`create_and_register` anywhere; PLUS even if reached, advertises "role, model tier, and provider" (L15) but returns only owl/channel/session (L19) — **description contradicts output** | NO | trivial (wire) + small (fields) |
| why | why.py | **NO — UNREGISTERED / DEAD** | **NO** | **NO — unreachable** | file does NOT end in `_command.py` ⇒ never registered; never imported anywhere | NO | trivial (wire) |

## Per-command notes

### help (OK)
Pulls live from `CommandRegistry.instance().list()` (L29), handles empty case (L34). Genuinely
does its job. The catch is downstream: it can only list what is actually registered — so the
two dead commands (`whoami`, `why`) will never appear here, which is the honest symptom of
their non-registration.

### config (hot-reload is a no-op — PRIMARY defect)
- Registered at module level: `_CMD = register_command(ConfigCommand())` (L208) → `__init__`
  runs with `event_bus=None` (L49).
- `load_builtin_commands()` (orchestrator L322) imports the module, locking in the bus=None
  singleton. The orchestrator **never re-creates ConfigCommand with the event_bus** (grep:
  only `SkillCommand/MemoryCommand/OwlsCommand` get `create_and_register` with a bus).
- **PROVEN at runtime**: registered `config` instance has `_bus = None`.
- Consequence: `_set` L164 `if self._bus is not None:` is always False ⇒ `settings_reloaded`
  never fires ⇒ `provider_reload` handler never runs for config edits.
- **Honesty defect**: `_set` (L166–172) computes `suffix = "" if hot` and `hot` defaults True
  (`extra.get("hot_reload", True)`), so most fields return `✓ key = value` with NO "restart
  required" — telling the user it took effect live when nothing reloaded it.
- Good parts: sensitive-field rejection (L141–150), schema validation before save (L155–162),
  atomic comment-preserving write (`save_yaml`), `_export`/`_get`/`_reset` correct.
- Test gap: helpers are unit-tested but no test drives `/config set` and asserts the live
  config object / registry actually changed. The gateway-integration mandate is unmet here.

### settings (event dead, same root cause)
- Same module-level-no-bus pattern (L99). Registered instance `_bus = None` (proven).
- `_set_autonomy` writes `autonomy_level` to YAML correctly (L83–86) but `settings_changed`
  emit (L87–91) is dead ⇒ no live subsystem learns of the autonomy change until restart.
- No `tests/commands/test_settings_command.py` exists. Zero coverage of the write or the
  (broken) event.

### cost (functional — the good one)
- Uses `default_db_path()` via `DbPool()` (pool.py L102–103) — the SAME db the server's
  `CostTracker` writes to (`cost_tracker.py` `_table = "cost_records"`). So `/cost` reads real
  data, and `/cost privacy YES` runs a real `DELETE FROM cost_records` (L123).
- Defensive: missing-table / db-open failures degrade to "No cost data yet" (L75–80, L124–129)
  — that's a legitimate degraded message, not a hidden error (it's logged at warning).
- Gap: NO test. The privacy wipe (destructive) has zero coverage — high-value place for a
  real side-effect test (insert rows → `/cost privacy YES` → assert table empty).

### tools (functional)
- Reads `get_services().tool_registry` (L37), handles None/empty (L38–42), sorts, renders
  severity glyph. Does its job. The bare `except Exception` (L45) only falls back to "read"
  severity → "?" glyph; non-fatal. No defect of substance. No direct test.

### provider (hot-reload dead; tests mask it — SECONDARY defect)
- Registered module-level no-bus (L299); registered instance `_bus = None` (proven).
- `_emit_reloaded` (L113–115) is a no-op in prod for add/remove/set-tier.
- The infrastructure to hot-reload providers EXISTS and is wired: orchestrator L697–700
  subscribes `make_settings_reload_handler(provider_registry)`. But that handler only acts on
  a `Settings` payload (provider_reload.py L38) and ignores the dict payload the command would
  emit — AND the command emits nothing anyway. So even the design intent (dict = "UI-side
  notification") would not reload the registry. Net: provider changes require a restart.
- Honesty: response says "applies on the next reload/restart" (L229/259/296) — so unlike
  config, provider is at least truthful about it.
- Security is solid: token never logged/echoed, stored via `store_secret`, only ref in YAML,
  validated before secret store (L200–207). Good.
- **Test masks the prod bug**: `test_provider_command.py` constructs `ProviderCommand(event_bus=bus)`
  itself (fixture L64) and asserts emission (L130/215/242) — but the PRODUCTION singleton has
  no bus. `test_auto_registered_in_registry` (L286) only asserts the name is present, not that
  it is wired. A green suite hides a dead feature — exactly the "looks wired but never fires"
  class.

### tier (functional, scope caveat)
- Persists via `PreferenceStore` (L84–90) with in-memory fallback; `get_session_tier` lets the
  sync router read it (L100–111). Genuinely works.
- Caveat: `_owner_key_for_state` returns `state.session_id` (L26–30) — a documented TODO. The
  module docstring claims it "propagates across all channels for the same owner"; that is
  **not yet true** while owner_key == session_id. Docstring overclaims current behavior.
- No test reads a written tier back through the store.

### browser (one overclaim defect)
- **`profile delete`**: `contextlib.suppress(OSError)` around `shutil.rmtree` (L165–166), then
  unconditionally returns `f"Deleted profile '{args[1]}'"` (L167). If rmtree fails (perms,
  busy), the user is told it was deleted when it was not. Classic swallowed-failure overclaim.
  Fix: check `target_dir.exists()` after, or report the error.
- `watch` subcommand (L170–178) never registers anything — returns a hint. Help text labels it
  "(read-only)" so it is honest, but it is an inert command surface.
- `fetch-binary` (L139–145), `sessions`, `close`, `settings` all do real work against the live
  runtime/registry and report real outcomes (close returns real count, fetch reports
  binary_ok/error). Those are fine.
- No direct command test.

### whoami (DEAD + contradictory — flagged as instructed)
- File `whoami.py` does NOT end in `_command.py` ⇒ `load_builtin_commands` skips it
  (registry.py L91–92 `if not mod_info.name.endswith("_command"): continue`).
- Grep proves it is never imported, never `register_command`'d, never `create_and_register`'d
  anywhere in `src/`. It is **dead code — `/whoami` is unreachable in production.**
- Even if reached: description (L15) advertises "owl name, role, model tier, and provider" but
  `handle` (L19) returns only `Owl / Channel / Session`. Output contradicts the contract.

### why (DEAD — flagged as instructed)
- Same non-`_command.py` filename problem ⇒ never registered, never imported. `/why` is
  unreachable in production. The handler body itself is plausible (reads `state.pipeline_step`,
  `tool_calls`, `errors`) but it never runs.

## Cross-cutting root cause

Two distinct registration patterns collide:
1. **Module-level `_CMD = register_command(X())`** — runs at import with NO dependencies, so any
   command needing an `event_bus` (config, settings, provider) gets `bus=None` permanently. The
   orchestrator does not re-instantiate them with the bus. → hot-reload / live-event features
   are structurally dead for the whole group.
2. **Filename-gated discovery** — `load_builtin_commands` only imports `*_command.py`. Files not
   following that suffix (`whoami.py`, `why.py`) are silently never registered. No error, no
   warning — they just don't exist at runtime.

Recommended fixes: (a) for bus-needing commands, register them in the orchestrator via a
`create_and_register(event_bus=...)` like the notification commands, OR have the orchestrator
inject the bus post-import; (b) rename `whoami.py`→`whoami_command.py`, `why.py`→`why_command.py`
(trivial), and fix whoami's output to match its description; (c) add gateway-integration tests
that assert the REAL side effect (live config object mutated / registry reloaded / cost rows
deleted), not just the return string or a self-injected bus.

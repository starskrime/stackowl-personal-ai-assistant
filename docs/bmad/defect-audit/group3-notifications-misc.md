# Group 3 Defect Audit — Notifications & Misc Slash Commands

Auditor: Murat (Test Architect). Date: 2026-06-19.
Scope: focus, urgent, quiet, notifications, brief, parliament, staged, webhook, connect, disconnect, plugins.

## How registration actually works (the load-bearing fact)

`CommandRegistry.load_builtin_commands()` (`registry.py:78-107`) only **imports** every `*_command.py`
module so it can *self-register*. It does **not** call any classmethod factory. A command therefore reaches a
user via `CommandRegistry.dispatch()` **only if** one of these is true at import/startup:

1. The module runs a **module-level (column-0)** `register_command(...)` / `CommandRegistry.instance().register(...)` — i.e. a `_CMD = register_command(...)`; **OR**
2. Some startup path explicitly calls its `create_and_register(...)` factory.

Verified: assembly.py:170-173 calls the factories for **focus / urgent / quiet / notifications** only.
**None** of the six files brief/parliament/staged/webhook/connect/plugins has a column-0 register call
(grep for `^register_command|^CommandRegistry|^_CMD` returns nothing in all six), and **no** `create_and_register`
for brief/parliament/staged/webhook is called anywhere in `src/`. connect/disconnect/plugins have **no factory at all**.
=> 6 commands (7 rows counting disconnect) are dead on arrival in production.

## Findings Table

| Command | File | Registered? | Reachable (CLI/TG) | Does its job? | Defects (file:line) | Real side-effect test? | Effort |
|---|---|---|---|---|---|---|---|
| focus | focus_command.py | YES — `assembly.py:170` | YES | YES | none material | Partial — tests call `handle()` directly, not via `dispatch` | trivial |
| urgent | urgent_command.py | YES — `assembly.py:171` | YES | YES | `channels=["cli"]` hardcoded default at `urgent_command.py:35` — broadcasts only to CLI unless wiring overrides; `assembly.py:171` passes no `channels`, so in prod it broadcasts to **CLI only**, contradicting "all channels" docstring | Partial — `handle()`-level only | small |
| quiet | quiet_command.py | YES — `assembly.py:172` | YES | YES | docstring says "session-scoped" but the INSERT (`quiet_command.py:28-32,105`) stores **no session_id** — override is **global**, not session-scoped | Partial | small |
| notifications | notifications_command.py | YES — `assembly.py:173` | YES | YES | only `missed` subcommand; description "View notification history" overclaims breadth (minor) | Partial | trivial |
| brief | brief_command.py | **NO** — factory never called | **NO** | Handler works, but unreachable | UNREGISTERED (no call site for `create_and_register`, `brief_command.py:111`) | NO real dispatch test | small (wiring) |
| parliament | parliament_command.py | **NO** — factory never called | **NO** | Handler logic sound; deps default `None` → would emit "not configured" if wired without orchestrator | UNREGISTERED (`parliament_command.py:279`); ALSO even if wired, `_start` passes `state.session_id` as the parliament `session_id` (`parliament_command.py:137`) — fine, but no validation | NO dispatch test | medium (wiring + ensure orchestrator injected, else all subs return "not configured") |
| staged | staged_command.py | **NO** — factory never called | **NO** | Handler works | UNREGISTERED (`staged_command.py:205`); `_reject` calls `bridge.delete(fact_id)` (`staged_command.py:176`) **without verifying the fact exists** → returns `"✓ Rejected"` for a bogus id (hardcoded success / false positive) | Tests call `handle()` directly; `test_story_6_7.py:101` tests the factory registers but nothing calls it in prod | small (wiring) + trivial (reject validation) |
| webhook | webhook_command.py | **NO** — factory never called | **NO** | Handler works; intentionally print-only (no config write) | UNREGISTERED (`webhook_command.py:169`); `register`/`disable` only **print YAML** + audit-log — by design, but description "Manage webhook sources" overclaims (it instructs, doesn't manage) | NO dispatch test | small (wiring) |
| connect | connect_command.py | **NO** — no factory, no module-level register | **NO** | Handler works | UNREGISTERED + **no registration mechanism exists at all** (no `create_and_register`, no `_CMD`) | NO — `test_story_11_*` only construct + `handle()` | small (add factory + wire) |
| disconnect | connect_command.py | **NO** — no factory, no module-level register | **NO** | Handler works | UNREGISTERED + no mechanism; reaches **private** `adapter._oauth.delete()` (`connect_command.py:164-165`) — brittle private-attr access, breaks for adapters lacking `_oauth` (silently skipped via `hasattr`) | NO dispatch test (`test_story_11_4.py:170` constructs directly) | small (wiring) + trivial (encapsulation) |
| plugins | plugins_command.py | **NO** — no factory, no module-level register | **NO** | Handler works | UNREGISTERED + **no registration mechanism exists at all**; `enable`/`disable` return hardcoded `"Plugin 'x' enabled."` even when name doesn't exist (`set_enabled` on missing name is a no-op per `test_story_10_6` — so success message is a **false positive** for bad names) | NO command-dispatch test; `test_story_10_6.py:163` constructs + `handle()`; the `set_enabled` DB tests (`:233+`) test the REGISTRY, not the command | small (wiring) + trivial (existence check) |

## Per-command notes

### focus (WIRED, working)
`handle()` sets router focus mode + emits `focus_mode_changed`, event-emit failure logged not swallowed (`focus_command.py:66-71`). Returns `focus_mode:{mode}`. No required-arg issues. Only gap: no test drives it through `CommandRegistry.dispatch`.

### urgent (WIRED, working — but CLI-only in prod)
Correctly fans out via `asyncio.gather(..., return_exceptions=True)` and counts delivered/failed honestly (`urgent_command.py:76-96`). **Defect:** default `channels=["cli"]` (`:35`) and `assembly.py:171` injects no channel list, so the "broadcast to all channels" promise (docstring `:4-6`) is unmet — it hits CLI only. Either inject the live channel roster at wiring or derive channels from `ChannelRegistry`.

### quiet (WIRED, working — scope mismatch)
Validates HH:MM (`:80-89`), inserts override with 24h TTL, DB error surfaced not swallowed (`:109-115`). **Defect:** docstring/description claim "session-scoped" / "for the current session" but the row has no `session_id` column written (`:28-32`) — the override is global. Either drop the "session" wording or add session scoping.

### notifications (WIRED, working)
Only `missed` is implemented; anything else returns usage. Query failure surfaced (`:63-68`). Honest `missed:0` on empty. Description slightly overclaims ("View notification history"). Fine.

### brief (UNREGISTERED)
Logic is correct: builds a synthetic non-persisted `Job` and calls `MorningBriefHandler.execute` (verified exists, returns `success=True`, `morning_brief.py:116,184`); handler failure surfaced as `✗ /brief: ...` (`:73-78`). The **only** problem is it never registers — `create_and_register` (`:111`) has no caller. Fix = call the factory at startup with the live `MorningBriefHandler`. **No real feature work needed.**

### parliament (UNREGISTERED)
Full subcommand surface (start/log/push/expand/unsuppress) with sound not-configured guards and honest degraded-synthesis messaging (`:160-173`). Underlying `ParliamentOrchestrator.run` + `inject_interjection` exist (`orchestrator.py:81,159`). Two issues: (1) unregistered; (2) the factory defaults every dep to `None`, so wiring MUST inject a real orchestrator/store/registry or every subcommand degrades to "not configured" — registering it bare would look working but do nothing. Effort medium because correct wiring requires the orchestrator be constructed and passed.

### staged (UNREGISTERED + reject false-positive)
list/review/promote correct; `promote` honestly returns not-found when `force_promote` returns falsy (`:191-196`). **Defect:** `_reject` (`:157-181`) deletes by id with **no existence check** — `bridge.delete` is fire-and-forget, so a non-existent id still yields `"✓ Rejected {id}"`. Should look the fact up (reuse `find_staged_by_id`) before claiming success. Plus the wiring gap. `test_story_6_7.py:101` even tests `create_and_register` registers it, but production never calls that path.

### webhook (UNREGISTERED — print-only by design)
`register`/`disable` deliberately never write config (docstring `:9-12`); `disable` does an audit-log write (`:152-158`), `list` reads `webhook_events_log` (table exists, `0020_webhook_rate_log.sql`) with query failure tolerated (`:118-123`). Correct for its stated design, but unregistered, and "Manage webhook sources" oversells an instruct-only command. Fix = wire + soften description.

### connect / disconnect (UNREGISTERED — no mechanism at all)
Both handlers are correct: `connect` lists adapters with per-adapter status, status-check failure logged not swallowed (`:68-75`); real OAuth via `adapter.connect()` (`:102`). `disconnect` resolves adapter, calls `disconnect()` + deletes creds. **Defects:** (1) neither has any registration mechanism — no `create_and_register`, no module-level register; they are pure dead code in prod. (2) `disconnect` reaches into the **private** `adapter._oauth.delete()` (`:164-165`); the `hasattr` guard means adapters without `_oauth` silently skip credential deletion while still reporting `"✓ disconnected and credentials removed"` — a potential false-positive on credential removal. Add a public `delete_credentials()` on the adapter protocol.

### plugins (UNREGISTERED — no mechanism + false-positive enable/disable)
list/info correct and honest (not-found handled, `:117-125`). **Defects:** (1) no registration mechanism at all. (2) `enable`/`disable` (`:151-177`) call `registry.set_enabled(name, ...)` then unconditionally return `"Plugin 'x' enabled."`; per `test_story_10_6` `set_enabled` on a missing name is a **silent no-op**, so the command reports success for a non-existent plugin. Check existence (or have `set_enabled` return a bool) before claiming success. NOTE: a separate `plugins` **Typer** sub-app exists at `cli/app.py:35` — that is a CLI-binary surface, NOT the in-chat slash command; the slash `/plugins` is still unreachable.

## Test-coverage verdict
No command in this group has a test that drives it through `CommandRegistry.dispatch(...)` asserting a real side effect. Existing tests either:
- construct the command and call `handle()` directly (bypasses the registration gap entirely — green tests, dead feature), or
- test the underlying subsystem (`PluginRegistry.set_enabled`, `MorningBriefHandler`) not the command path.
Recommend gateway-style tests: `load_builtin_commands()` + the startup wiring, then `dispatch("brief"/"staged reject ..."/"plugins enable ...")` asserting the real DB/registry effect AND that the command is even present in the registry.

## Effort rollup
- **Wiring (the dominant fix):** small per command — add/call a factory in the appropriate assembler (notifications/assembly.py-style or an integrations/plugins assembler). brief/staged/webhook/connect/disconnect/plugins = 6 wirings; parliament = medium (must also construct + inject the orchestrator).
- **Correctness nits (trivial each):** staged-reject existence check; plugins enable/disable existence check; disconnect public credential-delete; urgent real channel roster; quiet "session" wording.
- **No large/feature work:** every underlying subsystem (morning brief, parliament orchestrator, integration registry, plugin registry, webhook log) already exists and works. This group's risk is almost entirely "looks wired but never fires."

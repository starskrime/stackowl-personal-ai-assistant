# Slash-Command Defect Matrix — master (Phase 0 output)

Single source of truth for the slash-command overhaul. Compiled from three deep QA audits
(`docs/bmad/defect-audit/group{1,2,3}-*.md`) + reproduced scanner/registry behavior.
Auditor: Murat (bmad-tea) ×3 + systematic-debugging reproduction. Date: 2026-06-19.

## Reproduced facts (not hypotheses)

- Clean `/help` routes correctly (`scanner.scan` → `route=command`). Commands are **not**
  universally broken at the scanner.
- **Leading whitespace/tab → silent LLM fall-through** (`' /help'`, `'\t/help'` →
  `route=owl/secretary`). Real, but mostly masked because TUI (`compose_area.py:238`
  `.strip()`) and Telegram strip upstream.
- **After full startup only 15 commands are live**: 8 module-level (`help config settings cost
  tools provider tier browser`) + 3 DI (`skill memory owls`) + 4 DI
  (`focus urgent quiet notifications`). `load_builtin_commands()` alone yields **8**.
- `/unknowncmd` routes to `command` then dispatch raises `CommandNotFoundError` → "Unknown
  slash command" (NOT silent fall-through).

## Root causes (two structural defects)

1. **Filename-gated discovery** — `load_builtin_commands()` (`registry.py:91`) imports only
   `*_command.py`. `reset.py why.py whoami.py permissions.py audit.py audit_export.py` are
   invisible. + factory commands whose `create_and_register` is never called
   (`agents agent_create brief parliament staged webhook`) + commands with no registration
   mechanism at all (`connect disconnect plugins`).
2. **Module-level register ⇒ `event_bus=None` forever** — `config settings provider` register
   at import with no deps, so their hot-reload/event emission is structurally dead. The
   orchestrator never re-injects the bus.

Plus a scanner gap: `^/(\w+)` (`scanner.py:29`) can't match leading whitespace OR multi-word
command names (`/audit export`).

## Full command matrix (26 rows)

Status: ✅ live & correct · ⚠️ live but defective · ❌ dead (unreachable)

| # | Command | File | Live? | Job done? | Key defect | Real dispatch test? |
|---|---|---|---|---|---|---|
| 1 | /help | help_command.py | ✅ | ✅ | — | no |
| 2 | /config | config_command.py | ⚠️ | ✗ hot-reload | bus=None; `set` returns `✓` claiming live effect (lie) | no |
| 3 | /settings | settings_command.py | ⚠️ | ✗ event | bus=None; `settings_changed` never fires | no |
| 4 | /cost | cost_command.py | ✅ | ✅ | privacy DELETE untested | no |
| 5 | /tools | tools_command.py | ✅ | ✅ | — | no |
| 6 | /provider | provider_command.py | ⚠️ | ✗ hot-reload | bus=None no-op (honest msg though) | masks bug |
| 7 | /tier | tier_command.py | ✅ | ~ | owner_key=session_id ≠ "across channels" docstring | no |
| 8 | /browser | browser_command.py | ⚠️ | ~ | `profile delete` swallows OSError, claims Deleted | no |
| 9 | /whoami | whoami.py | ❌ | ✗ | unregistered (not `*_command.py`); output ≠ description | no |
| 10 | /why | why.py | ❌ | n/a | unregistered (not `*_command.py`) | no |
| 11 | /memory | memory_command.py | ✅ | ✅ | `delete` no prefix-resolution (false success on prefix) | ✅ |
| 12 | /skill | skill_command.py | ✅ | ✅ | git-vs-archive heuristic brittle | ✅ |
| 13 | /owls | owls_command.py | ✅ | ✅ | dead `_NO_DB`; silent no-DB DNA skip | ✅ |
| 14 | /agents | agents_command.py | ❌ | ✗ | factory never called | mock-only |
| 15 | /agent | agent_create_command.py | ❌ | ✗ | factory never called; eager template load in `__init__` | mock-only |
| 16 | /reset | reset.py | ❌ | ✗ **P0 lie** | no-op "cleared"; no `clear_session` API; unregistered | none |
| 17 | /permissions | permissions.py | ❌ | ~ | unregistered; bare logger | mock-only |
| 18 | /audit | audit.py | ❌ | ~ | unregistered; bare logger | mock-only |
| 19 | /audit export | audit_export.py | ❌ | ✗ **P0** | two-word name unmatchable + unregistered + empty-key sign | mock-only |
| 20 | /focus | focus_command.py | ✅ | ✅ | — | no |
| 21 | /urgent | urgent_command.py | ✅ | ~ | `channels=["cli"]` default → CLI-only ≠ "all channels" | no |
| 22 | /quiet | quiet_command.py | ✅ | ~ | global, no session_id ≠ "session-scoped" docstring | no |
| 23 | /notifications | notifications_command.py | ✅ | ✅ | desc overclaims breadth (minor) | no |
| 24 | /brief | brief_command.py | ❌ | ✗ | factory never called | none |
| 25 | /parliament | parliament_command.py | ❌ | ✗ | factory never called; needs orchestrator injected | none |
| 26 | /staged | staged_command.py | ❌ | ✗ | factory never called; `reject` false success (no existence check) | none |
| 27 | /webhook | webhook_command.py | ❌ | ~ | factory never called; print-only ≠ "Manage" | none |
| 28 | /connect | connect_command.py | ❌ | ~ | no registration mechanism | none |
| 29 | /disconnect | connect_command.py | ❌ | ~ | no mechanism; private `_oauth`; false "credentials removed" | none |
| 30 | /plugins | plugins_command.py | ❌ | ~ | no mechanism; enable/disable false success on bad name | none |

(30 rows: 24 command classes; `/audit export` & `/disconnect` are separate command surfaces.)

Tally: **15 live** (10 fully correct, 5 with defects) · **15 dead/unreachable**.

## Defect classes (drives the epics)

**Class A — Reachability (the spine):**
- A-regex: scanner can't match leading whitespace or two-word names; silent fall-through.
- A-discovery: `load_builtin_commands` filename gate; 6 files invisible.
- A-wiring: 6 factory commands never called; 3 with no mechanism.
- A-menu: Telegram `setMyCommands` + TUI autocomplete only show what's registered.
- A-guard: no test asserts a shipped command is reachable via `registry.dispatch`.

**Class B — Honesty / false success (lies about doing the job):**
- B-P0: `/reset` no-op; `/audit export` empty-key signing.
- B-false-success: `/staged reject`, `/plugins enable|disable`, `/browser profile delete`,
  `/disconnect`, `/memory delete` (prefix) all claim success without verifying the effect.
- B-dead-bus: `/config set` claims live effect; `/config`+`/settings`+`/provider` events dead.

**Class C — Scope/contract mismatches (does a narrower job than advertised):**
- `/urgent` CLI-only; `/quiet` global; `/tier` session-scoped; `/whoami` missing fields;
  `/webhook` print-only; `/notifications` breadth.

**Class D — Robustness/polish:**
- `/agent` eager template load; `/disconnect` private `_oauth`; bare loggers in
  permissions/audit; `/owls` dead `_NO_DB`; `/skill` git heuristic.

**No large feature work required** — every underlying subsystem (parliament orchestrator,
morning brief, integration/plugin/scheduler registries, webhook log, audit chain) already
exists and works. The dominant fix is wiring + honesty.

## Proposed epic structure

- **Epic A — Dispatch spine** (Class A): reachability guard (failing-first), scanner harden
  (whitespace + decide unknown-command contract), discovery/registration sweep for all 15 dead
  commands, two-word `/audit export` → `/audit` subcommand, menu completeness, dead-bridge
  cleanup. Stories A1–A6.
- **Epic B — Honesty** (Class B): `/reset` real `clear_session`; `/audit export` empty-key
  refuse; existence-check the false-success commands; fix dead-bus hot-reload (re-inject bus or
  honest "restart required"). Stories B1–B6.
- **Epic C — Contracts** (Class C): align each command's behavior or wording to its
  description. Stories C1–C6.
- **Epic D — Robustness** (Class D): polish items. Stories D1–D5.

Every story ships with a gateway integration test (dispatch through `registry.dispatch`,
mock only the provider, assert the real side effect).

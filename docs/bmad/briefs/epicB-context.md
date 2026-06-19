# Epic B — shared context (wire the 14 dead commands)

Branch: feat/slash-command-overhaul. Repo root /ssd/projects/stackowl-personal-ai-assistant.

## The spine you build on (already landed)

- `src/stackowl/commands/assembly.py`:
  - `CommandDeps` — frozen-ish dataclass; every dep Optional (None default). Fields include:
    event_bus, db, router, settings, owl_registry, tool_registry, plugin_registry,
    integration_registry, bridge, preference_store, lancedb, promoter, embedding_registry,
    skills_store, skills_loader, skills_root, audit_logger, scheduler,
    parliament_orchestrator, morning_brief_handler. Add a field if a command needs one not
    listed.
  - `register_all_commands(deps, registry=None)` — the SINGLE registration entry. Calls
    `load_builtin_commands(registry)` (the 8 module-level) then `_register_di_commands(deps, registry)`.
  - `_register_di_commands` registers each DI command **UNCONDITIONALLY** (no `if dep is not
    None` guard) — registration must be dep-INDEPENDENT so the reachability guard is a true
    proxy. To wire a new command: add ~4 lines constructing it with `deps.<fields>` and
    `registry.register(...)`. The command must tolerate None deps at construction and emit an
    honest "✗ /<cmd>: not configured" at handle() when a required dep is None (mirror the
    existing 6 DI commands).
- `src/stackowl/commands/manifest.py`: `SHIPPED_COMMANDS` already contains ALL 29 target names.
  Do NOT edit it (the 14 you wire are already listed). `EXEMPT_COMMANDS = {"audit export"}`.
- `tests/journeys/commands/test_reachability_guard.py`: asserts `set(registered) ==
  SHIPPED_COMMANDS`, marked `xfail(strict=True)`. As you wire commands it gets closer to green;
  DO NOT remove the xfail marker (the final A6 commit does that once all 29 register).
- Anti-mock rule: gateway command tests live in `tests/journeys/commands/` and must drive
  `CommandRegistry.dispatch(...)`, NOT call `.handle()` directly or construct `EventBus(`.
  A meta-test enforces this. Put your per-command dispatch tests there.

## Orchestrator dep sources (where to get each dep for CommandDeps)

In `src/stackowl/startup/orchestrator.py` (the big `start()` method):
- `audit_logger` — already constructed at ~line 405 (`AuditLogger(default_db_path())`).
- `parliament` (a `ParliamentOrchestrator`) — constructed at ~line 740.
- `scheduler` — `scheduler_components.scheduler` after `SchedulerAssembly.build(...)` at ~line 754.
- `event_bus`, `db_pool`, `memory_bridge`, `self._settings`, `preference_store`, `owl_registry`,
  `tool_registry`, skills_components, memory_components — all already in scope (see existing
  CommandDeps build site).
- `PluginRegistry` — `from stackowl.plugins.registry import PluginRegistry; PluginRegistry(default_db_path())`.
- `IntegrationRegistry` — `from stackowl.integrations.registry import IntegrationRegistry; IntegrationRegistry.instance()`.
- `MorningBriefHandler` — find where it is constructed (heartbeat/notifications); if not already
  built at startup, construct it for the command (read brief_command.py:32 for its ctor).

## CRITICAL ordering

`register_all_commands(deps)` is currently called ~line 456 (after NotificationAssembly.build).
`parliament` (740) and `scheduler` (754) are built AFTER that. So the single
`register_all_commands` call MUST MOVE to after `scheduler_components` is built (~after 754),
where ALL deps exist. It must still run BEFORE the channel loops (~1226+) and before the
Telegram adapter's `setMyCommands`. Verify nothing between the old and new call site depends on
commands already being registered (registration only populates the dispatch table used in the
channel loops — safe to register later). Make this move ONE behavior-preserving commit.

## Command ctor signatures (read each file to confirm before wiring)

- why.py / whoami.py: handler takes only `state` (no deps). whoami currently NOT a *_command.py
  file and output contradicts its description (advertises role/tier/provider, returns
  name/channel/session) — FIX the output to include the advertised fields (read state for what's
  available; owl role/tier/provider come from state/owl_registry — investigate).
- audit.py: `AuditCommand(audit_logger)`.
- brief_command.py: `BriefCommand(handler: MorningBriefHandler)`.
- webhook_command.py: `WebhookCommand(db, settings)` — also soften description from "Manage
  webhook sources" to match its print-only/instruct behavior.
- permissions.py: `PermissionsCommand(settings, integration_registry, plugin_registry, ...)`
  (read its __init__) — also switch its bare `logging.getLogger` to the structured `log` namespace.
- agents_command.py / agent_create_command.py / parliament_command.py / staged_command.py:
  multi-dep __init__ — read each.

## Process / constraints

- TDD. One ATOMIC COMMIT per command (+ the one orchestrator-move commit). Tree green each commit.
- Each command gets a test in tests/journeys/commands/ that drives `CommandRegistry.dispatch`
  through `register_all_commands` (real path) and asserts the command is reachable AND its real
  behavior/side-effect (mock only the provider/external; assert outcomes, not return strings).
- No silent excepts; structured `log.gateway` (or the command's module log). No hidden errors.
- `uv run ruff check src/` + `uv run mypy src/` clean on touched files. Run tests in BATCHES.
- Commit trailer (end every commit message):
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UuQ83Rw9SSJLnhveVMhK2A

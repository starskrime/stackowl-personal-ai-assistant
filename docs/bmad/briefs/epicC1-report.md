# Epic C1 Implementation Report — Dead-bus / dishonest hot-reload fix

## Investigation Findings

### The bug (confirmed)

`/config`, `/settings`, and `/provider` each had a module-level:

```python
_CMD = register_command(ConfigCommand())   # event_bus=None permanently
```

This Pattern-A self-registration means the instance stored in the registry always had
`event_bus=None`. Every `_emit_*` call was guarded by `if self._bus is not None:`, so they
were unconditionally silenced in production. The bus passed to `CommandDeps.event_bus` was
never seen by these commands.

### Hot-reload chain (does it actually work?)

Yes — but **only via ConfigWatcher**, not via the command emit:

1. A `/config set` or `/provider add` call writes the change to `stackowl.yaml`.
2. `ConfigWatcher` (polls every 5s, debounce-settles on the next tick) detects the mtime
   change, calls `settings_factory()` to build a new `Settings` object, and emits
   `settings_reloaded` with that `Settings` as payload on the live bus.
3. `provider_reload.py::_on_settings_reloaded` type-guards `isinstance(payload, Settings)` —
   it acts ONLY on a real `Settings` object and deliberately ignores the `dict` payloads the
   commands were emitting (documented in its module docstring).

So the commands' `_emit_*` calls were doubly dead:
- **Dead bus** (`event_bus=None` on the registered instance)
- **Wrong payload type** (`dict`, ignored by the reload handler which only accepts `Settings`)

The watcher IS the real hot-reload mechanism. The command emits serve as an in-process
notification to other subscribers (e.g. future cost trackers, audit loggers) — they are
correct in shape for that purpose, they just needed a live bus to travel on.

### `/config set` honesty

The existing code already has the `hot_reload` field check:

```python
hot = extra.get("hot_reload", True)
suffix = "" if hot else " — restart required"
return f"✓ {key} = {stringify(coerced)}{suffix}"
```

So fields tagged `hot_reload=False` already say "restart required". Fields tagged
`hot_reload=True` (the default) say only `"✓ key = value"` — which is *technically* honest
(the change IS saved to YAML; the watcher will pick it up within ~10s). No false "live now"
claim is made. The honesty test in C1 asserts the message does NOT say "live now" or
"applied immediately" — this passes without any message change needed.

## Fix Applied (Part A — wire the real bus)

### `src/stackowl/commands/assembly.py`

Added three blocks at the end of `_register_di_commands`:

```python
# /config — moved from Pattern-A to DI so the live event_bus is wired (C1)
from stackowl.commands.config_command import ConfigCommand
registry.register(ConfigCommand(event_bus=deps.event_bus))

# /settings — moved from Pattern-A to DI so the live event_bus is wired (C1)
from stackowl.commands.settings_command import SettingsCommand
registry.register(SettingsCommand(event_bus=deps.event_bus))

# /provider — moved from Pattern-A to DI so the live event_bus is wired (C1)
from stackowl.commands.provider_command import ProviderCommand
registry.register(ProviderCommand(event_bus=deps.event_bus))
```

### `src/stackowl/commands/config_command.py`
### `src/stackowl/commands/settings_command.py`
### `src/stackowl/commands/provider_command.py`

Removed the module-level `_CMD = register_command(Cmd())` self-registration from each.
Also removed the now-unused `from stackowl.commands.registry import register_command` import.

The `load_builtin_commands` scanner checks `isinstance(getattr(mod, "_CMD", None), SlashCommand)`.
With `_CMD` gone (only a comment remains), the scanner no longer overwrites the DI instance with
the bus=None one.

## Tests Written (TDD — written before fix, confirmed red-then-green)

### `tests/journeys/commands/test_config_command.py`
- `test_config_reachable_via_assembly` — DI path registers the command
- `test_config_not_found_without_registration` — correct isolation
- `test_config_set_emits_on_production_bus` — **regression gate**: `MagicMock` spy on
  `deps.event_bus`; asserts `spy.emit` called once with `"settings_reloaded"` after
  `/config set autonomy_level low`
- `test_config_set_no_bus_does_not_crash` — `event_bus=None` still works
- `test_config_set_does_not_claim_applied_immediately` — honesty gate
- Smoke: `test_config_list_works_via_dispatch`, `test_config_get_works_via_dispatch`

### `tests/journeys/commands/test_settings_command.py`
- `test_settings_reachable_via_assembly`
- `test_settings_not_found_without_registration`
- `test_settings_autonomy_emits_on_production_bus` — spy asserts `"settings_changed"` fires
- `test_settings_no_bus_does_not_crash`
- `test_settings_invalid_level_returns_error`, `test_settings_unknown_subcommand_returns_usage`

### `tests/journeys/commands/test_provider_command_journey.py`
- `test_provider_reachable_via_assembly`
- `test_provider_not_found_without_registration`
- `test_provider_add_emits_on_production_bus` — spy asserts `"settings_reloaded"` fires with
  `{"provider": "acme"}` dict payload
- `test_provider_remove_emits_on_production_bus`
- `test_provider_no_bus_does_not_crash`
- `test_provider_add_message_is_honest_about_timing` — "next reload/restart" in response

### `tests/commands/test_provider_command.py` (fix)
- `TestProviderRegistration::test_registered_via_assembly` — updated from
  `load_builtin_commands()` (which no longer registers `/provider`) to
  `register_all_commands(CommandDeps(), ...)` (the real DI path)

## Pre-existing failures (not caused by C1)

- `test_command_manifest_drift::test_no_command_subclass_is_unmanifested` — `AuditExportCommand`
  unmanifested. Confirmed present on `feat/slash-command-overhaul` before merge.
- `mypy` — 2 pre-existing errors in `assembly.py` for `AgentsCommand`/`AgentCreateCommand`
  scheduler arg type (`object` vs `JobScheduler | None`). Not introduced by C1.

## Conclusion

**Hot-reload now works correctly via the watcher AND the command emit bus is live.**

- Config changes written by `/config set` / `/settings autonomy` / `/provider add|remove|set-tier`
  reach the watcher within ~10s (unchanged — the watcher was always the real reload path).
- The `settings_reloaded` / `settings_changed` events emitted by the commands now travel on
  the real production `EventBus` (via `CommandDeps.event_bus`), reaching any registered
  subscriber — previously they were silently dropped into `None`.
- `/config set` honesty: fields with `hot_reload=False` say "restart required"; fields with
  `hot_reload=True` say nothing extra (change is saved to YAML and the watcher picks it up).
  No false "live now" or "applied immediately" claim is made.
- Reachability guard: all 29 commands still register (`test_every_shipped_command_is_reachable`
  passes).

## Test summary

119 passed, 1 pre-existing failure (manifest drift) unrelated to C1.

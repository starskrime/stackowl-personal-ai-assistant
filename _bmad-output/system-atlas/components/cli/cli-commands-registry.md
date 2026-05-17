---
id: cli-commands-registry
path: src/cli/v2/commands/registry.ts
subsystem: cli
type: command-registry
loc: 456
wired: true
status: mapped
mapped_week: 4
links:
  imports_from:
    - cli-bridge
    - cli-store
    - gateway-core
    - memory-db
    - gateway-mcp-manager
    - gateway-owl-gateway
  imported_by:
    - cli-commands-dispatcher
    - cli-commands-completion
    - cli-composer
---

# src/cli/v2/commands/ — Command Registry + Dispatcher + Completion

> **Status:** mapped · **Wiring:** ✅ wired · **Mapped:** 2026-05-16 (squad audit)

## Purpose

Implements the slash-command system: definition, dispatch routing, and tab completion. Three files collaborate:

- **`registry.ts`** — Command definitions, argument schemas, handler wiring (456 LoC)
- **`dispatcher.ts`** — Parse raw `/command arg1 arg2` input, lookup, execute (40 LoC)
- **`completion.ts`** — Tab completion engine (4 modes, 75 LoC)

## CommandContext

Every handler receives a `CommandContext` object:

```ts
interface CommandContext {
  bridge: UiBridge;
  getStore: () => UiState;
  getMemoryRepo: () => MemoryRepository;
  getMcpManager: () => McpManager;
  getOwlGateway: () => OwlGateway;
}
```

## Registered Commands — 14 Top-Level

| Command | Aliases | Subcommands | Handler File |
|---|---|---|---|
| `/help` | — | — | `misc.ts` |
| `/status` | — | — | `status.ts` |
| `/clear` | — | — | `clear.ts` |
| `/quit` | `/exit` | — | `exitConfirm.ts` |
| `/memory` | `/mem` | list, search, get, invalidate, stats, history, export | `memory.ts` |
| `/mcp` | — | list, status, add, remove, enable, disable, tools, reconnect | `mcp.ts` |
| `/config` | — | provider, tier, engine, cost, channel, gateway, parliament, heartbeat, logging, research, pellets, browser, mcp, global | `config/*.ts` |
| `/provider` | — | list, test, delete, edit | `provider.ts` |
| `/owl` | — | list, show, create, from-bmad, delete, pin, unpin, reset | `owl.ts` |
| `/session` | — | list, new, switch, delete, rename, info | session handler |
| `/parliament` | — | — | Opens parliament panel |
| `/skills` | — | — | Opens skills panel |
| `/learning` | — | — | `misc.ts` |
| `/capabilities` | — | — | `misc.ts` |

## Completion Engine — 4 Modes

| Mode | Trigger | Behavior |
|---|---|---|
| **Command** | `/` prefix, no space | Matches all registered top-level command names |
| **Subcommand** | `/command ` (with space) | Lists subcommands for that command |
| **Argument** | `/command sub ` | Calls command's `complete(args)` function if defined |
| **Memory key** | `/memory get ` | Calls `completeMemoryKeys()` async resolver |

## Bug Fixed (2026-05-16)

**B-CLI-00 (fixed, commit be40fab):** Enter handler in `Composer.tsx` for subcommand completion used `value.replace(/\S+$/, "")` which stripped the entire `/config` token when no space was present. Fixed to use `value.trimEnd().split(/\s+/)[0]` — same extraction as Tab handler.

## Cross-references

- [[cli-composer]] — calls `getCompletions()` and `dispatcher.dispatch()`
- [[cli-bridge]] — handlers emit UiEvents via `bridge.emit()`
- [[cli-store]] — handlers read state via `getStore()`
- [[subsystem-cli]] — subsystem overview

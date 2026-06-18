# Comprehensive /config Command System — Design Spec

**Date:** 2026-05-15
**Status:** Approved

---

## Goal

Replace the current 3-subcommand `/config` with a complete CLI control plane that covers every field in `stackowl.config.json`, exposes 14 hardcoded constants as first-class config fields, and supports hot-reload where structurally possible — giving users full control without ever hand-editing JSON.

## Background

The current `/config` implementation has three subcommands:
- `/config` — generic recursive drill-down panel (reachable but poor UX for 150+ fields)
- `/config tiers` — view intelligence tiers
- `/config set-tier <low|mid|high> <provider> <model>` — quick tier update

This leaves ~100 fields unreachable without JSON editing, has no provider management CLI, no cost/rate-limit CLI, no channel token management, and exposes none of the tunable constants currently hardcoded in `runtime.ts` and `proactive.ts`.

---

## Architecture

### Three new layers

**1. Namespace handlers**

`src/cli/v2/commands/handlers/config/` is split into one file per namespace instead of one monolithic `config.ts`. The existing generic drill-down panel stays as an escape hatch for any edge cases.

Namespace files:
```
src/cli/v2/commands/handlers/config/
├── index.ts          # Router — dispatches to namespace handlers
├── provider.ts
├── tier.ts
├── engine.ts
├── cost.ts
├── channel.ts
├── mcp.ts
├── gateway.ts
├── parliament.ts
├── heartbeat.ts
├── research.ts
├── pellets.ts
├── browser.ts
├── voice.ts
├── logging.ts
├── perches.ts
├── skills.ts
├── tools.ts
└── global.ts         # validate, diff, reload, export, import, reset
```

**2. Config patch API**

`src/config/loader.ts` gains a new exported function:

```typescript
async function patchConfig<K extends keyof StackOwlConfig>(
  section: K,
  patch: DeepPartial<StackOwlConfig[K]>
): Promise<{ saved: true; hotReloaded: boolean; restartRequired: boolean }>
```

- Deep-merges `patch` into the live in-memory config object
- Writes atomically to disk (write `.tmp` → rename)
- On disk failure: rolls back in-memory state, throws
- Fires `configReloadBus.emit(section, next, prev)` for hot-reloadable sections
- Returns `hotReloaded: false` and `restartRequired: true` for structural sections

**3. Hot-reload bus**

New file `src/config/reload-bus.ts`:

```typescript
type ReloadHandler<K extends keyof StackOwlConfig> = (
  next: StackOwlConfig[K],
  prev: StackOwlConfig[K]
) => Promise<void>;

export const configReloadBus: TypedEventEmitter<ReloadHandlerMap>;
```

Subsystems subscribe at boot. If a handler throws, `patchConfig` rolls back the disk write, restores the in-memory value, and returns an error to the CLI.

---

## Namespace Definitions

17 namespaces cover all 34 config sections.

| Namespace | Config sections |
|---|---|
| `provider` | `providers`, `defaultProvider`, `defaultModel`, `roles` |
| `tier` | `intelligence` |
| `engine` | `engine`, `owlDna`, `synthesis` |
| `cost` | `costs`, `rateLimiting` |
| `channel` | `telegram`, `slack`, `discord`, `whatsapp` |
| `mcp` | `mcp` |
| `gateway` | `gateway` |
| `parliament` | `parliament` |
| `heartbeat` | `heartbeat` |
| `research` | `research`, `cognition` |
| `pellets` | `pellets`, `storage` |
| `browser` | `browser`, `camofox`, `webFetch` |
| `voice` | `voice` |
| `logging` | `logging`, `tracing` |
| `perches` | `perches` |
| `skills` | `skills`, `plugins` |
| `tools` | `tools`, `sandboxing`, `execution`, `queue` |

---

## Complete Command Surface

### provider

```
/config provider list
/config provider add <name> --type <anthropic|openai|ollama|openai-compatible>
                             --base-url <url> --api-key <key> --model <model>
/config provider remove <name>                           ⚠ confirm required
/config provider set-key <name> <api-key>
/config provider set-model <name> <model>
/config provider set-url <name> <url>
/config provider set-default <name>
/config provider test <name>
```

Restart required: `add`, `remove` (provider clients init at boot).

### tier

```
/config tier list
/config tier set <low|mid|high> <provider> <model>
/config tier set-default <task> <low|mid|high>
    tasks: conversation|parliament|evolution|extraction|episodic|
           classification|synthesis|summarization|clarification
/config tier reset                                       ⚠ confirm required
```

Hot-reloadable: yes (next turn).

### engine

```
/config engine list
/config engine set <key> <value>
/config engine planning <enable|disable>
/config engine reset [key]                               ⚠ confirm required if no key
```

Valid keys for `engine set`:

| Key | Default | Source |
|---|---|---|
| `maxToolIterations` | 15 | existing config |
| `maxContextTokens` | 8000 | existing config |
| `maxToolResultLength` | 6000 | existing config |
| `contextKeepRecent` | 10 | existing config |
| `deepMaxToolIterations` | 50 | **moved from runtime.ts** |
| `maxRetries` | 3 | **moved from runtime.ts** |
| `maxToolFailStreak` | 50 | **moved from runtime.ts** |
| `baseRetryDelayMs` | 1500 | **moved from runtime.ts** |
| `contextWindowThreshold` | 20 | **moved from runtime.ts** |
| `contextCompressionBatch` | 10 | **moved from runtime.ts** |
| `toolWindowSize` | 12 | **moved from runtime.ts** |
| `dnaBaseTemp` | 0.7 | **moved from runtime.ts** |
| `synthesizeEarlyThreshold` | 0.3 | **moved from runtime.ts** |
| `evolutionBatchSize` | 5 | existing owlDna config |
| `decayRatePerWeek` | 0.1 | existing owlDna config |
| `synthesisProvider` | anthropic | existing synthesis config |
| `synthesisModel` | — | existing synthesis config |
| `synthesisMinQuality` | 0.6 | **moved from synthesizer.ts** |
| `synthesisTargetQuality` | 0.75 | **moved from synthesizer.ts** |

Hot-reloadable: yes (next turn).

### cost

```
/config cost show
/config cost set [--max-daily <usd>] [--max-monthly <usd>]
                 [--max-request-tokens <n>] [--warn-at <pct>]
/config cost <enable|disable>
/config cost rate-limit list
/config cost rate-limit set <provider> --per-minute <n> [--per-hour <n>]
/config cost rate-limit remove <provider>
```

Hot-reloadable: yes (immediate).

### channel

```
/config channel list
/config channel telegram set-token <token>
/config channel telegram allow-user <id>
/config channel telegram remove-user <id>
/config channel slack set-tokens --bot <token> --app <token> [--signing-secret <s>]
/config channel slack allow-channel <id>
/config channel discord set-token <token>
/config channel discord allow-guild <id>
/config channel discord set-dm-policy <open|pairing>
/config channel whatsapp <enable|disable>
/config channel whatsapp set-session-path <path>
```

Restart required: all (bot clients init at boot).

### mcp

```
/config mcp list
/config mcp add <name> --transport stdio --command <cmd> [--args a b c]
                        [--env KEY=val ...]
/config mcp add <name> --transport sse --url <url>
/config mcp remove <name>                                ⚠ confirm required
/config mcp <enable|disable> <name>
/config mcp test <name>
/config mcp set-env <name> <KEY> <value>
```

Restart required: `add`, `remove` (MCP handshake at boot).

### gateway

```
/config gateway show
/config gateway set-port <port>                          ⚠ restart required
/config gateway set-host <host>                          ⚠ restart required
/config gateway set-output-mode <normal|debug>           ✓ hot-reload
/config gateway rate-limit --per-minute <n> --per-hour <n>  ✓ hot-reload
```

### parliament

```
/config parliament show
/config parliament set [--max-rounds <n>] [--max-owls <n>]  ✓ hot-reload
```

### heartbeat

```
/config heartbeat <enable|disable>                       ✓ hot-reload
/config heartbeat set [--interval <minutes>]
                      [--min-cooldown <minutes>]
                      [--max-unanswered <n>]             ✓ hot-reload
```

New fields `minCooldownMinutes` (default: 60) and `maxUnansweredPings` (default: 1) are moved from hardcoded values in `proactive.ts`.

### research

```
/config research show
/config research set <key> <value>
    keys: autoDeep|selfCheckInterval|maxIterations|similarityThreshold|
          cloudFallbackAfter|enableDiminishingReturns
/config cognition show
/config cognition set <key> <value>
    keys: tickIntervalMinutes|minIdleMinutes|maxActionsPerDay|enabled
```

Hot-reloadable: yes (next turn / next tick).

### pellets

```
/config pellets show
/config pellets set-embedding-model <model>
/config pellets set-cache-size <n>                       (new field, default: 1000)
/config pellets dedup <enable|disable>
/config pellets dedup set [--similarity <n>] [--skip <n>] [--max-candidates <n>]
/config storage set-backend <file|sqlite>                ⚠ restart required
/config storage set-sqlite-path <path>                   ⚠ restart required
```

### browser

```
/config browser <enable|disable>
/config browser set [--pool-size <n>] [--headless <bool>] [--stealth <bool>]
/config browser set-proxy <url>
/config camofox <enable|disable>
/config camofox set-url <url>
/config camofox set-key <key>
/config webfetch obscura <enable|disable>
```

Hot-reloadable: yes (next pool use).

### voice

```
/config voice show
/config voice set [--model <m>] [--system-voice <v>] [--speak-rate <wpm>]
                  [--silence <rms>] [--silence-duration <ms>]
```

Hot-reloadable: yes (next recording).

### logging

```
/config logging set-level <debug|info|warn|error>        ✓ hot-reload immediate
/config logging sink <enable|disable> <file|ring-buffer|pretty-console>
/config logging set-retention <days>
/config logging set-ring-size <n>
/config logging redact <add|remove> <tokens|emails|paths>
/config tracing <enable|disable>
/config tracing set [--sample-rate <n>]
```

### perches

```
/config perches show
/config perches consent <grant|revoke> <source>
    sources: git|active_file|time_of_day|system|perch|heartbeat|user_pattern|
             clipboard|email|calendar|weather
/config perches sources <enable|disable> <source>
/config perches set-max-signals <n>
/config perches watch-paths <add|remove> <path>
```

Hot-reloadable: yes (immediate).

### skills

```
/config skills <enable|disable>
/config skills list
/config skills add-dir <path>
/config skills remove-dir <path>
/config skills watch <enable|disable>
/config skills set-debounce <ms>
```

Hot-reloadable: watch/debounce yes; directory changes need reload.

### tools

```
/config tools show
/config tools permission <category> <allowed|prompt|denied>  ✓ hot-reload
/config tools intent-routing <enable|disable>                ✓ hot-reload
/config tools set-max-routing <n>                            ✓ hot-reload
/config sandboxing <enable|disable>
/config sandboxing debug <enable|disable>
/config queue set [--concurrency <n>] [--max-size <n>]
```

### Global operations

```
/config validate
/config diff
/config reload
/config export [--path <file>] [--include-secrets]
/config import <path>
/config reset <namespace>                                ⚠ confirm required
```

---

## New config.json Fields

14 new fields added to the schema. All have defaults equal to current hardcoded values — existing deployments are unaffected.

**`engine` section (9 new):**

```typescript
engine?: {
  // existing fields unchanged
  deepMaxToolIterations?: number;     // default: 50
  maxRetries?: number;                // default: 3
  maxToolFailStreak?: number;         // default: 50
  baseRetryDelayMs?: number;          // default: 1500
  contextWindowThreshold?: number;    // default: 20
  contextCompressionBatch?: number;   // default: 10
  toolWindowSize?: number;            // default: 12
  dnaBaseTemp?: number;               // default: 0.7
  synthesizeEarlyThreshold?: number;  // default: 0.3
}
```

**`heartbeat` section (2 new):**

```typescript
heartbeat: {
  // existing fields unchanged
  minPingCooldownMinutes?: number;    // default: 60
  maxUnansweredPings?: number;        // default: 1
}
```

**`pellets` section (1 new):**

```typescript
pellets?: {
  // existing fields unchanged
  embeddingCacheSize?: number;        // default: 1000
}
```

**`synthesis` section (2 new):**

```typescript
synthesis?: {
  // existing fields unchanged
  minQualityThreshold?: number;       // default: 0.6
  targetQualityThreshold?: number;    // default: 0.75
}
```

---

## Hot-Reload Reference

| Section | Hot-reloadable | Handler location |
|---|---|---|
| `logging.level` / `logging.sinks` | ✓ immediate | `src/logger.ts` |
| `gateway.outputMode` / `rateLimit` | ✓ immediate | `src/gateway/core.ts` |
| `heartbeat` | ✓ reschedules timer | `src/heartbeat/proactive.ts` |
| `engine.*` | ✓ next turn | `src/engine/runtime.ts` |
| `parliament` | ✓ next session | `src/parliament/orchestrator.ts` |
| `research` / `cognition` | ✓ next turn/tick | respective modules |
| `costs` / `rateLimiting` | ✓ immediate | cost tracker / rate limiter |
| `tools.permissions` / `intentRouting` | ✓ immediate | tool registry |
| `perches` | ✓ immediate | ambient mesh |
| `browser` | ✓ next pool use | browser pool |
| `providers` (add/remove) | ✗ restart | provider client init |
| `mcp.servers` | ✗ restart | MCP handshake |
| `gateway.port` / `.host` | ✗ restart | HTTP server rebind |
| `telegram` / `slack` / `discord` / `whatsapp` | ✗ restart | bot client init |
| `storage.backend` / `.sqlitePath` | ✗ restart | DB connection |

---

## UX Patterns

**Output format** — every command ends with exactly one of:
```
✓ Saved.
✓ Saved. ⚠ Restart StackOwl to apply.
✗ Error: <reason>
```

**Secrets** — always masked in display (`sk-ant-...****`), never pre-filled. `/config export` masks secrets by default; `--include-secrets` writes plaintext.

**Destructive operations** — `provider remove`, `mcp remove`, `reset <namespace>` require `--confirm` flag or an interactive typed-name confirmation prompt.

**Validation errors** — field path + expected type + received value:
```
✗ engine.maxRetries: expected integer 1–20, got "three"
✗ costs.budget.warnAtPercent: expected number 0–100, got 150
```

**Unknown key** — lists valid keys:
```
✗ Unknown key "maxFails". Valid keys: maxToolIterations, maxRetries, ...
```

**`/config validate` output:**
```
✓ providers       2 configured, all reachable
✓ intelligence    tiers: low/mid/high set, 9 task defaults assigned
⚠ costs           enabled but no budget set — unlimited spend
✗ telegram        botToken is placeholder value
```

**`/config diff` output** — only fields differing from defaults:
```
engine.maxToolIterations     15  →  25
heartbeat.intervalMinutes    30  →  60
logging.level                info →  debug
```

**`/config reload` output:**
```
✓ logging         reloaded
✓ engine          reloaded
✓ heartbeat       rescheduled (60min interval)
— providers       skipped (restart required)
```

**Hot-reload failure** — rolls back disk write and in-memory value:
```
✗ Hot-reload failed for "heartbeat": <reason>. Previous value restored.
```

---

## Testing

**Unit — namespace handlers** (`__tests__/cli/v2/commands/config/<namespace>.test.ts`):
- Valid inputs persist correctly via `patchConfig` (mocked)
- Invalid inputs reject with correct error message and field path
- Secrets masked in output
- Destructive ops require confirmation before `patchConfig` is called

**Unit — `patchConfig`** (`__tests__/config/patch.test.ts`):
- Deep-merge preserves untouched fields
- Atomic write (`.tmp` → rename)
- Disk failure rolls back in-memory state
- Returns correct `{ hotReloaded, restartRequired }` flags per section

**Unit — reload bus** (`__tests__/config/reload-bus.test.ts`):
- Subscriber receives `(next, prev)` with correct values
- Multiple subscribers all fire
- Handler throw triggers rollback and error reporting
- Non-hot-reload sections do not emit

**Integration — global operations** (`__tests__/cli/v2/commands/config/global.test.ts`):
- `/config validate`: known-good config passes; placeholder token fails correctly
- `/config diff`: equal-to-defaults → empty; changed fields shown correctly
- `/config export` / `/config import`: secrets masked by default; round-trip produces identical config; bad import rejected before any write

**What is NOT tested here:**
- Subsystem behavior post-hot-reload (owned by each subsystem's test suite)
- MCP handshake after `mcp add` (covered by existing MCP tests)
- Live provider connectivity for `provider test` (mocked at HTTP level)

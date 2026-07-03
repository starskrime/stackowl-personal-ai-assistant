# Platform-wide self-healing — design

## Problem

While confirming tonight's scheduler-hardening deploy (see
`2026-07-03-scheduler-single-authority-design.md`), the embeddings
provider hit a network-unavailable cache-miss and permanently degraded to
a cruder hash-embedding fallback with no retry — masking the gap instead
of healing it. A first fix added a bounded retry-once at load time
(commit `0ba23e52`), but that only fires once at process boot; if network
comes back mid-run, nothing notices.

Investigating further: StackOwl already has a general-purpose mechanism
for exactly this — the ADR-6 self-heal loop (`HealthSweepHandler` +
`HealableResource` protocol + `RecoveryActuator`-driven recycle-then-
reverify), already live for `db`, each `provider:{name}`, and `browser`.
It is centralized under the same `JobScheduler` hardened tonight
(`health_sweep` is a recurring scheduled job, same `jobs` table, same poll
loop). The gap isn't a missing framework — it's 9 subsystems that should
plug into this existing loop but don't.

## What already exists (do not rebuild)

- `HealableResource` protocol (`src/stackowl/infra/resilience.py:47`):
  `available` (property), `unavailable_reason` (property), async
  `ensure_available()`, `register_on_recycled(cb)`.
- `HealthSweepHandler` (`src/stackowl/scheduler/handlers/health_sweep.py`):
  a recurring `JobScheduler` handler. When `settings.health_loop` is True
  (default) and a down/degraded `HealthStatus.name` has a matching entry
  in its `healers: dict[str, HealableResource]`, it calls
  `ensure_available()` (bounded via `RecoveryActuator`), then re-collects
  to verify before alerting.
- 3 existing implementations, all wired: `DbPool` (`db/pool.py`),
  `ModelProvider` (`providers/base.py` — stateless no-op, recovery via
  `CircuitBreaker` instead), `CamoufoxRuntime`
  (`tools/browser/runtime.py`). Wired into `healers` at
  `scheduler/assembly.py:351-363`.
- `HealthAggregator` (`src/stackowl/health/aggregator.py`) — collects
  `HealthStatus` from registered `HealthContributor`s.
  `_build_health_aggregator` (`scheduler/assembly.py:598-631`) currently
  registers: `DbContributor`, `FilesystemContributor`, `GraphContributor`
  (import-probe only — see Component 3 below), `ProviderContributor`s,
  and (Telegram only) two `ChannelLivenessContributor`s.
- Durable-task recovery (`src/stackowl/pipeline/durable/recovery.py`) —
  boot-time claim→reconstruct→resume for tasks orphaned by a crashed
  process. `DurableTaskStore` has the full API needed
  (`claim_for_recovery`, `list` by status, `load_checkpoint`,
  `update_status`) — a periodic wrapper needs no new store capability.

## Scope (confirmed with user)

All 9 components below, in one plan. Effort is heterogeneous — the plan
must scale each task to its actual work, not treat all 9 as the same
mechanical pattern.

## Components

### 1. Embeddings — `EmbeddingRegistry`
`src/stackowl/embeddings/registry.py:19`. Real work: `ensure_available()`
retries `SentenceTransformerProvider.create(self._model_name)`; on
success, swap `self._provider`/`self._is_semantic` back to semantic (the
registry owns this state, not the provider — wrap the registry, not
`SentenceTransformerProvider`). `available` = `self._is_semantic`.
`health_check()` already exists (line 113) but is never registered as a
contributor — register it in `_build_health_aggregator`.

### 2. LanceDB — `LanceDBAdapter`
`src/stackowl/memory/lancedb_adapter.py:51`. `self._connection` is opened
once (`_connect()`, lines 314-320) and never recreated on failure. Real
work: `ensure_available()` drops `self._connection` and reconnects. Its
existing `health()` (line 243) returns `HealthReport`, a **different
type** than the aggregator's `HealthStatus` — add a thin adapter/shim
(`LanceDBHealthContributor`) mapping `HealthReport` → `HealthStatus`
rather than changing `health()`'s existing callers. Register in
`_build_health_aggregator` (currently absent entirely).

### 3. Kuzu — `KuzuAdapter`
`src/stackowl/memory/kuzu_adapter.py:62`. `self._db`/`self._conn` (on a
dedicated single-thread executor — F067 thread-confinement invariant)
are created once in `__init__` and never recreated. Real work:
`ensure_available()` tears down and reconstructs
`self._db`/`self._conn`/executor, respecting F067 (all reconstruction
must happen on that same confined thread). **Bundled correctness fix**:
`GraphContributor.probe()` (`health/contributors.py:36`) only checks
`import kuzu` succeeds — it never touches the live adapter, so it reports
healthy even when the real DB handle is dead. Replace/supplement it with
a contributor that calls the live `KuzuAdapter.health()` (mapped
`HealthReport` → `HealthStatus`, same shim pattern as LanceDB).

### 4. Telegram — `TelegramChannelAdapter`
`src/stackowl/channels/telegram/adapter.py:79`. Already self-heals via
`_liveness_heartbeat()` → `_beat_once()` → `_self_heal_polling()` →
`RecoveryActuator`, on its own timer, independent of ADR-6. Thin wrapper:
`ensure_available()` checks current polling state first — no-op if
already healthy (avoid a double-heal race between the adapter's own
heartbeat and a `health_sweep` tick both firing recovery at once) —
otherwise triggers `_self_heal_polling()` once. Register
`health_check()` (line 1081) directly as a contributor (today only
reachable indirectly via `ChannelLivenessContributor`'s DB-stamped
liveness row, not the adapter's own live state).

### 5. Discord — `DiscordChannelAdapter`
`src/stackowl/channels/discord/adapter.py:49`. discord.py's `reconnect`
default (`True`) already handles a dropped websocket — no custom code
needed for that case. Thin wrapper: `available` = `self._client is not
None`; `ensure_available()` re-runs `start()` only when `self._client is
None` (never started / crashed before assignment). Register
`health_check()` (line 583) as a contributor.

### 6. Slack — `SlackChannelAdapter`
`src/stackowl/channels/slack/adapter.py:73`. Thin wrapper:
`ensure_available()` restarts the socket-mode client if
`_last_ping_at` exceeds `_HEALTH_STALE_AFTER_S` (the same threshold
`health_check()`, line 829, already uses to report degraded). Register
as a contributor.

### 7. WhatsApp — `WhatsAppChannelAdapter` / `WhatsAppBrowserDriver`
`src/stackowl/channels/whatsapp/adapter.py:62`,
`src/stackowl/channels/whatsapp/browser.py:63`. Standalone Playwright
driver, NOT built on the shared `CamoufoxRuntime`. `_poll_loop()` catches
every exception and keeps looping, but nothing restarts the underlying
browser/page if the browser session itself (not just one poll) dies.
Real work: `ensure_available()` stops and restarts
`WhatsAppBrowserDriver` (relaunch browser, re-attach WhatsApp Web
session) — no existing recovery capability today; closest precedent is
`CamoufoxRuntime`. Register `health_check()` (line 390) as a contributor.

### 8. MCP — `McpClient`
`src/stackowl/mcp/client.py:46`. Fully stateless per call — a fresh
`sse_client`/`stdio_client`/`ClientSession` is opened and torn down
inside `async with` on every `discover_tools`/`call_tool`, with an
existing bounded retry-once on `McpCallError(kind="transport")`. No
persistent handle to recycle, so `ensure_available()` is a genuine no-op
(matches the `ModelProvider` reference pattern). The actual gap: **no
`HealthContributor` exists for MCP at all** — add one (using
`McpLivenessProbe.probe_all()`, `mcp/probe.py:18`) so the aggregator can
even detect an MCP outage; wire the no-op `HealableResource` into
`healers` for consistency with the other 8, even though its heal action
does nothing.

### 9. Durable-task liveness watchdog
`src/stackowl/pipeline/durable/recovery.py`. Task recovery
(claim→reconstruct→resume from a ReAct checkpoint) exists but runs
**only at boot** (`startup/orchestrator.py:3017-3019`, role != "gateway").
No `last_heartbeat` column exists on `tasks` — `updated_at` is the only
signal, and it's not a true heartbeat (any status/checkpoint write bumps
it). A task whose backing process dies mid-drive while the server keeps
running stays stuck in `status='running'` until the next restart.

New recurring handler `task_liveness_sweep`: finds `tasks` rows with
`status='running'` AND `updated_at` older than a staleness threshold,
reclaims them through the **same** claim→reconstruct→resume logic
`DurableTaskRecoverer` already uses at boot — factor the per-task
reclaim unit out of `recovery.py` so boot and this periodic sweep share
one implementation, not two. Also exposed as a
`HealthContributor`/`HealableResource` pair (`available` = zero stale
tasks; `ensure_available()` triggers an immediate reclaim sweep) so
`health_sweep` surfaces "N stuck agent tasks" and can remediate
on-demand between the sweep's own periodic ticks.

## Architecture

No new mechanism. All 9 plug into the existing ADR-6 loop:
`HealableResource` implementation + `HealthContributor` registration,
wired into `healers`/`health_aggregator.register(...)` at
`scheduler/assembly.py:351-363` / `:598-631` (component 9's handler is
also registered as a new recurring `JobScheduler` job, same pattern as
`health_sweep` itself).

## Data flow

Unchanged from today's working loop: `HealthSweepHandler.execute()` →
`aggregator.collect()` → for each down/degraded name with a matching
`healers` entry, `RecoveryActuator`-bounded `ensure_available()` → the
sweep re-collects to verify → alert only if still unhealthy after the
heal attempt. Component 9 additionally runs its own independent
recurring tick (not solely reactive to the sweep) since a stuck agent
task is time-sensitive in a way the other 8 (mostly connection/handle
recycling) are not.

## Testing

Per component: one test that `ensure_available()` recovers from a
simulated dead-handle/degraded state, and (for components with a new
`HealthContributor`) one test that the contributor correctly reports
`down`/`degraded` — specifically for LanceDB/Kuzu, a test proving the new
contributor does NOT mask a real outage the way `GraphContributor`'s
import-only probe does today. For component 9: a test that a
`status='running'` row with stale `updated_at` gets reclaimed and resumed
without waiting for a restart, and that a fresh (non-stale) running row
is left alone.

## Out of scope

- Any change to `HealthSweepHandler`'s core detect/alert/recycle
  dispatch logic, `RecoveryActuator`, or `JobScheduler` itself — all
  proven working tonight, this arc only adds resources into the existing
  loop.
- Telegram's existing `_self_heal_polling`/`RecoveryActuator` heal
  mechanism is not replaced, only made visible under the same `healers`
  dict.
- The de-complication PRD (FR-21, Week 3) and the scheduler
  single-authority arc (already shipped tonight, commits `c29db56b`,
  `6aa8feaa`, `4ce5f17f`) — untouched, separate tracks.

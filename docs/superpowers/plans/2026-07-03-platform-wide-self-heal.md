# Platform-Wide Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire 9 subsystems (embeddings, LanceDB, Kuzu, 4 channel adapters, MCP client, durable-task liveness) into StackOwl's existing ADR-6 `HealableResource`/`HealthSweepHandler` self-heal loop, which is itself a `JobScheduler`-dispatched recurring job — extending tonight's "single scheduler authority" theme to self-healing.

**Architecture:** No new mechanism. Each task implements the `HealableResource` protocol (`src/stackowl/infra/resilience.py:47`: `available` property, `unavailable_reason` property, async `ensure_available()`, `register_on_recycled(cb)`) on its subsystem, and registers a `HealthContributor` (or adapts an existing one) so `HealthAggregator`/`HealthSweepHandler` can detect and heal it. Wiring lands in `src/stackowl/scheduler/assembly.py` (`healers` dict at ~line 351-363, `_build_health_aggregator` at ~line 598-631).

**Tech Stack:** Python 3.13, pytest/pytest-asyncio, existing `sentence-transformers`, `lancedb`, `kuzu`, `discord.py`, `slack-bolt`, `python-telegram-bot` deps — no new dependencies.

## Global Constraints

- 4-point logging (entry/decision/step/exit) on every new/modified method, per `CLAUDE.md`.
- Every `except` logs with `exc_info` — no catch-and-hide.
- Follow the existing 3 `HealableResource` implementations' pattern (`DbPool` in `db/pool.py`, `ModelProvider` in `providers/base.py`, `CamoufoxRuntime` in `tools/browser/runtime.py`) — read at least one before implementing; mirror its shape (property/method placement, docstring style, error handling).
- `RecoveryActuator`/`HealthSweepHandler`/`JobScheduler` core logic is NOT modified by any task — only new resources plug into the existing seam.
- Reference design doc: `docs/superpowers/specs/2026-07-03-platform-wide-self-heal-design.md` — read the relevant component section before starting; it has the precise behavior contract this plan summarizes.
- Read the target file(s) FIRST before writing any diff — exact line numbers below are from an earlier audit and may have shifted; treat them as pointers, not literal patch coordinates.
- Run targeted pytest paths only, never the full suite (known to hang on this box).
- Commit after each task once its own tests pass.

---

### Task 1: Embeddings — `EmbeddingRegistry` self-heal

**Files:**
- Modify: `src/stackowl/embeddings/registry.py` (class `EmbeddingRegistry`, ~line 19)
- Modify: `src/stackowl/scheduler/assembly.py` (`healers` dict ~line 351-363, `_build_health_aggregator` ~line 598-631)
- Test: `tests/embeddings/test_embedding_registry_healable.py` (new)

**Interfaces:**
- Produces: `EmbeddingRegistry` implements `HealableResource` — `available: bool` (= `self._is_semantic`), `unavailable_reason: str | None` (non-None reason when hash-fallback active), `async ensure_available() -> None`, `register_on_recycled(cb)`.
- Consumes: existing `SentenceTransformerProvider.create(model_name)` (already has its own bounded network-retry from tonight's earlier fix, commit `0ba23e52`) and the existing `EmbeddingRegistry.health_check() -> HealthStatus` (already exists at ~line 113).

- [ ] **Step 1: Read the current file**

Read `src/stackowl/embeddings/registry.py` in full (it's short, ~120 lines) and `src/stackowl/embeddings/sentence_transformer_provider.py`'s `create()` classmethod signature. Confirm `EmbeddingRegistry._provider`/`_is_semantic`/`_model_name` field names match what this task assumes.

- [ ] **Step 2: Write failing tests**

In `tests/embeddings/test_embedding_registry_healable.py`, following whatever mocking pattern the existing embeddings tests use (check `tests/embeddings/` for an existing `EmbeddingRegistry`/`SentenceTransformerProvider` test file first — reuse its fixture style):
- Test: a degraded (`_is_semantic=False`, hash provider active) registry's `ensure_available()` calls `SentenceTransformerProvider.create` again; on success, `self._provider`/`self._is_semantic` flip to the semantic provider/`True`.
- Test: `ensure_available()` on an already-semantic (`_is_semantic=True`) registry is a no-op (doesn't reconstruct anything).
- Test: `ensure_available()` on a still-failing retry raises (propagates the underlying exception — matches the protocol's "raise if it cannot be recovered" contract) and leaves the registry on the hash fallback.
- Test: `available`/`unavailable_reason` properties reflect `_is_semantic` correctly in both states.

- [ ] **Step 3: Run tests, confirm they fail**

Run: `uv run pytest tests/embeddings/test_embedding_registry_healable.py -v` — expect failures (methods/properties don't exist yet, or `AttributeError`).

- [ ] **Step 4: Implement `HealableResource` on `EmbeddingRegistry`**

Add the 4 protocol members to `EmbeddingRegistry`. `ensure_available()`: if `self._is_semantic`, return immediately (no-op). Otherwise, attempt `SentenceTransformerProvider.create(self._model_name)`; on success, set `self._provider` to the new provider and `self._is_semantic = True`, log at INFO ("self-heal: semantic embeddings restored"); on failure, log at WARNING/ERROR with `exc_info` and re-raise (do not swallow — the protocol contract is "raise if it cannot be recovered", and the sweep's own retry-bounding via `RecoveryActuator` handles backoff, not this method). `available` returns `self._is_semantic`; `unavailable_reason` returns `None` when semantic, else a fixed string like `"hash fallback active — semantic model unavailable"`. `register_on_recycled` can be a no-op matching `ModelProvider`'s pattern (log at DEBUG, this resource has no downstream dependents to notify today) unless your Step 1 read finds an existing caller pattern suggesting otherwise — if so, follow that instead and note it in your report.

- [ ] **Step 5: Run tests, confirm they pass**

Run: `uv run pytest tests/embeddings/test_embedding_registry_healable.py -v` — expect PASS.

- [ ] **Step 6: Wire into `assembly.py`**

Read `src/stackowl/scheduler/assembly.py` around the `healers` dict construction (~line 351-363) and `_build_health_aggregator` (~line 598-631) to see the exact current shape (this plan's line numbers are pointers from an earlier audit, confirm against the live file). Add `healers["embeddings"] = embedding_registry` alongside the existing `db`/`provider:*`/`browser` entries (the `embedding_registry` instance must already be in scope at that point — trace where it's constructed/threaded in this file, e.g. it's passed to `LanceDBAdapter` elsewhere per the design doc; if it's not yet in scope in `assembly.py` at the point `healers` is built, that's a real finding — report DONE_WITH_CONCERNS and describe exactly what's missing rather than threading a new constructor parameter through multiple layers on your own judgment). Register `embedding_registry.health_check` as a contributor in `_build_health_aggregator`, matching how another contributor (e.g. `DbContributor`) is registered there.

- [ ] **Step 7: Run the full embeddings + scheduler assembly test directories**

Run: `uv run pytest tests/embeddings/ tests/scheduler/test_assembly*.py -v` (adjust the assembly test path to whatever actually exists — check first) — expect PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/embeddings/registry.py src/stackowl/scheduler/assembly.py tests/embeddings/test_embedding_registry_healable.py
git commit -m "feat(embeddings): wire EmbeddingRegistry into ADR-6 self-heal loop

ensure_available() retries SentenceTransformerProvider.create() and swaps
back to semantic on success -- closes the gap where a network-unavailable
boot degraded to hash embeddings permanently with no later retry."
```

---

### Task 2: LanceDB — `LanceDBAdapter` self-heal + health-type shim

**Files:**
- Modify: `src/stackowl/memory/lancedb_adapter.py` (class `LanceDBAdapter`, `_connect()` ~line 314-320, existing `health()` ~line 243)
- Create: a small `LanceDBHealthContributor` (co-locate in `lancedb_adapter.py` or `src/stackowl/health/contributors.py` — check which file the existing contributors like `DbContributor`/`GraphContributor` live in and match that)
- Modify: `src/stackowl/scheduler/assembly.py` (`healers`, `_build_health_aggregator`)
- Test: `tests/memory/test_lancedb_adapter_healable.py` (new)

**Interfaces:**
- Produces: `LanceDBAdapter` implements `HealableResource`. `LanceDBHealthContributor` (or equivalent) implements whatever protocol `HealthAggregator.collect()` expects (check `src/stackowl/health/aggregator.py`/`src/stackowl/health/status.py` for the exact `HealthContributor` shape — likely a `contributor_name` property + async `health_check() -> HealthStatus`), internally calling `LanceDBAdapter.health()` (existing, returns `HealthReport`) and mapping it to `HealthStatus`.
- Consumes: existing `LanceDBAdapter.health() -> HealthReport` (do not change this method's signature or existing callers — this task ADDS a shim on top, it does not refactor the existing type).

- [ ] **Step 1: Read the current files**

Read `src/stackowl/memory/lancedb_adapter.py` in full, focusing on `__init__`, `_connect()`, `health()`, and every method that uses `self._connection` (to know what `ensure_available()` must reset). Read `src/stackowl/health/status.py` (`HealthReport` and `HealthStatus` shapes) and `src/stackowl/health/contributors.py` (an existing contributor, e.g. `DbContributor`, as the pattern to mirror for `LanceDBHealthContributor`).

- [ ] **Step 2: Write failing tests**

- Test: `ensure_available()` on an adapter with a dead/closed `self._connection` drops it and calls `_connect()` again (mock `lancedb.connect` to raise on the first call inside a health probe, succeed on a fresh connect).
- Test: `available`/`unavailable_reason` reflect connection state.
- Test: `LanceDBHealthContributor.health_check()` (or equivalent) returns a `down` `HealthStatus` when the adapter's `health()` reports failure — proving the mapping is faithful, not silently upgrading a real outage to healthy (this is the specific risk the design doc calls out for Kuzu's existing `GraphContributor`; guard against the same mistake here).

- [ ] **Step 3: Run tests, confirm they fail.**

Run: `uv run pytest tests/memory/test_lancedb_adapter_healable.py -v`

- [ ] **Step 4: Implement `HealableResource` on `LanceDBAdapter`**

`ensure_available()`: set `self._connection = None`, call `self._connect()` (or the equivalent reconnect path), let any failure propagate (raise). `available`/`unavailable_reason` derive from whether `self._connection` is set and/or the last `health()` result — use whichever is cheaper/correct per your Step 1 read (don't force a fresh probe on every property access if `health()` is expensive; a cached-state approach mirroring `DbPool`'s pattern is preferred if `DbPool` does that — check).

- [ ] **Step 5: Implement `LanceDBHealthContributor`**

Wraps a `LanceDBAdapter` reference; `health_check()` calls `adapter.health()` and maps the returned `HealthReport`'s fields to a `HealthStatus` (status/message/latency — match field names to whatever `HealthStatus` actually defines from your Step 1 read). Contributor name should be distinct and stable (e.g. `"lancedb"`) — this becomes the key `health_sweep` uses to look up the matching `healers` entry, so the contributor name and the `healers` dict key in Step 7 MUST match exactly.

- [ ] **Step 6: Run tests, confirm they pass.**

Run: `uv run pytest tests/memory/test_lancedb_adapter_healable.py -v`

- [ ] **Step 7: Wire into `assembly.py`**

Add `healers["lancedb"] = lancedb_adapter` and register `LanceDBHealthContributor(lancedb_adapter)` in `_build_health_aggregator`, matching the existing registration pattern. If `lancedb_adapter` isn't already in scope at the `healers` construction site, report the gap (DONE_WITH_CONCERNS) rather than threading new parameters through unrelated layers on your own judgment.

- [ ] **Step 8: Run the full memory + scheduler assembly test directories.**

Run: `uv run pytest tests/memory/ tests/scheduler/test_assembly*.py -v` (adjust paths as found) — expect PASS.

- [ ] **Step 9: Commit**

```bash
git add src/stackowl/memory/lancedb_adapter.py src/stackowl/scheduler/assembly.py tests/memory/test_lancedb_adapter_healable.py
# add the contributor file wherever it landed
git commit -m "feat(memory): wire LanceDBAdapter into ADR-6 self-heal loop

ensure_available() drops and reconstructs the cached connection handle.
LanceDBHealthContributor shims the adapter's existing HealthReport into
HealthStatus so the aggregator can detect an outage it previously
couldn't see at all."
```

---

### Task 3: Kuzu — `KuzuAdapter` self-heal + fix misleading `GraphContributor`

**Files:**
- Modify: `src/stackowl/memory/kuzu_adapter.py` (class `KuzuAdapter`, `__init__` ~line 94-97, existing `health()` ~line 279, `aclose()` ~line 319)
- Modify: `src/stackowl/health/contributors.py` (`GraphContributor.probe()` ~line 36 — currently import-only, must be changed to probe the live adapter)
- Modify: `src/stackowl/scheduler/assembly.py`
- Test: `tests/memory/test_kuzu_adapter_healable.py` (new)
- Test: update/extend whatever test currently covers `GraphContributor` (find via `grep -rl GraphContributor tests/`) to assert it now reflects live adapter state, not just import success

**Interfaces:**
- Produces: `KuzuAdapter` implements `HealableResource`. `GraphContributor` (existing class — being FIXED, not replaced, unless your Step 1 read shows a clean reason to add a new class instead and deprecate the old) now probes the live `KuzuAdapter.health()` rather than only `import kuzu`.
- Consumes: existing `KuzuAdapter.health() -> HealthReport`.

**Constraint (F067):** `KuzuAdapter` uses a dedicated single-thread `ThreadPoolExecutor` for all Kuzu calls (thread-confinement invariant — Kuzu's Python bindings are not thread-safe across arbitrary threads). `ensure_available()`'s teardown-and-reconstruct of `self._db`/`self._conn` MUST happen via that same confined executor, not from whatever thread/task calls `ensure_available()`. Read `aclose()` (existing teardown) and `__init__` (existing construction) carefully before writing this — get the threading model right; if genuinely unsure after reading, report NEEDS_CONTEXT rather than guess and risk a cross-thread Kuzu call.

- [ ] **Step 1: Read the current files**

Read `src/stackowl/memory/kuzu_adapter.py` in full. Read `src/stackowl/health/contributors.py`'s `GraphContributor` class in full, and whatever calls `GraphContributor.probe()` today (grep for it) to understand what changing its behavior affects.

- [ ] **Step 2: Write failing tests**

- Test: `ensure_available()` on an adapter with a dead `self._db`/`self._conn` tears them down and reconstructs them on the SAME confined executor thread (assert via whatever mechanism the existing test suite uses to verify thread confinement — check `tests/memory/` for an existing Kuzu thread-confinement test and mirror its verification approach).
- Test: `GraphContributor` (fixed version) reports `down` when the live `KuzuAdapter.health()` reports failure — even though `import kuzu` still succeeds. This is the specific regression this task closes: today's `GraphContributor` would report healthy in this exact scenario.
- Test: `available`/`unavailable_reason` reflect adapter state.

- [ ] **Step 3: Run tests, confirm they fail.**

- [ ] **Step 4: Implement `HealableResource` on `KuzuAdapter`**

`ensure_available()`: on the confined executor, close/discard the existing `self._db`/`self._conn`, reconstruct them identically to `__init__`'s construction path (extract a shared private helper if `__init__` and `ensure_available()` would otherwise duplicate the construction logic — DRY). Let failure propagate.

- [ ] **Step 5: Fix `GraphContributor` to probe the live adapter**

Change `GraphContributor.probe()` (or add a constructor parameter giving it a `KuzuAdapter` reference, matching whatever pattern other contributors use to reach a live resource) so it calls `adapter.health()` instead of only `import kuzu`. Preserve the import-check as a fast-path/precondition if useful, but the final verdict must reflect live adapter health.

- [ ] **Step 6: Run tests, confirm they pass.**

- [ ] **Step 7: Wire into `assembly.py`**

Add `healers["kuzu"] = kuzu_adapter` (or whatever contributor-name/healers-key you gave `GraphContributor` in Step 5 — they must match). Update the existing `GraphContributor.probe()` call site in `_build_health_aggregator` (~line 612) to pass the live adapter instead of calling the old import-only static probe.

- [ ] **Step 8: Run the full memory + scheduler assembly test directories.**

- [ ] **Step 9: Commit**

```bash
git add src/stackowl/memory/kuzu_adapter.py src/stackowl/health/contributors.py src/stackowl/scheduler/assembly.py tests/memory/test_kuzu_adapter_healable.py
git commit -m "feat(memory): wire KuzuAdapter into ADR-6 self-heal loop; fix GraphContributor

ensure_available() tears down and reconstructs the db/conn handles on the
F067-confined single thread. GraphContributor previously only checked
'import kuzu' succeeds and would report healthy even with a dead live
adapter connection -- now probes the real adapter."
```

---

### Task 4: Telegram — thin `HealableResource` wrapper

**Files:**
- Modify: `src/stackowl/channels/telegram/adapter.py` (class `TelegramChannelAdapter`, existing `_self_heal_polling()`, existing `health_check()` ~line 1081)
- Modify: `src/stackowl/scheduler/assembly.py`
- Test: `tests/channels/telegram/test_adapter_healable.py` (new, or add to an existing adapter test file if one exists)

**Interfaces:**
- Produces: `TelegramChannelAdapter` implements `HealableResource`, delegating recovery to its EXISTING `_self_heal_polling()` method — this task does not write new recovery logic, only exposes the existing mechanism through the protocol.

- [ ] **Step 1: Read the current file**

Read `src/stackowl/channels/telegram/adapter.py`'s `_liveness_heartbeat()`, `_beat_once()`, `_self_heal_polling()`, and `health_check()` in full, to understand the exact current-state signal that tells you "already healthy, no-op" vs "needs healing."

- [ ] **Step 2: Write failing tests**

- Test: `ensure_available()` on an already-healthy adapter (per whatever state `_beat_once()` checks) is a no-op — does NOT call `_self_heal_polling()` again (avoid the double-heal race the design doc calls out).
- Test: `ensure_available()` on an unhealthy adapter calls `_self_heal_polling()` once.
- Test: `available`/`unavailable_reason` reflect the same state `health_check()` (line 1081) already reports — reuse its logic/threshold rather than inventing a second one.

- [ ] **Step 3: Run tests, confirm they fail.**

- [ ] **Step 4: Implement `HealableResource`**

Add the 4 protocol members. `ensure_available()` checks current health state (reuse the same signal `health_check()`/`_beat_once()` already use); if unhealthy, calls `_self_heal_polling()`; if healthy, returns immediately.

- [ ] **Step 5: Run tests, confirm they pass.**

- [ ] **Step 6: Wire into `assembly.py`**

Add `healers["telegram"] = telegram_adapter` and register `telegram_adapter.health_check` directly as a contributor (this REPLACES the indirect `ChannelLivenessContributor` reliance for detection purposes — check whether `ChannelLivenessContributor` should stay for a different reason (e.g. cross-process visibility) or be removed here; if unsure, keep both and note the redundancy in your report rather than removing existing working code).

- [ ] **Step 7: Run the channels + scheduler assembly test directories.**

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/channels/telegram/adapter.py src/stackowl/scheduler/assembly.py tests/channels/telegram/test_adapter_healable.py
git commit -m "feat(telegram): expose existing self-heal via HealableResource

Delegates to the adapter's own working _self_heal_polling()/RecoveryActuator
loop -- no new recovery logic, just makes it visible under the same
healers dict as every other resource, and guards against a double-heal
race with the adapter's own heartbeat timer."
```

---

### Task 5: Discord — thin `HealableResource` wrapper

**Files:**
- Modify: `src/stackowl/channels/discord/adapter.py` (class `DiscordChannelAdapter`, existing `start()` ~line 122, existing `health_check()` ~line 583)
- Modify: `src/stackowl/scheduler/assembly.py`
- Test: `tests/channels/discord/test_adapter_healable.py` (new)

- [ ] **Step 1: Read the current file** — `src/stackowl/channels/discord/adapter.py` in full.

- [ ] **Step 2: Write failing tests**
- Test: `ensure_available()` when `self._client is None` calls `start()`.
- Test: `ensure_available()` when `self._client` is set is a no-op (SDK handles its own reconnect).
- Test: `available` = `self._client is not None`.

- [ ] **Step 3: Run tests, confirm they fail.**

- [ ] **Step 4: Implement `HealableResource`** — `available`/`unavailable_reason`/`ensure_available()`/`register_on_recycled` per the spec above.

- [ ] **Step 5: Run tests, confirm they pass.**

- [ ] **Step 6: Wire into `assembly.py`** — `healers["discord"] = discord_adapter`, register `health_check` as a contributor (only if Discord is configured/enabled — check how the existing conditional Telegram/Slack/WhatsApp registration in `assembly.py` is gated and match it).

- [ ] **Step 7: Run channels + assembly tests.**

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/channels/discord/adapter.py src/stackowl/scheduler/assembly.py tests/channels/discord/test_adapter_healable.py
git commit -m "feat(discord): wire DiscordChannelAdapter into ADR-6 self-heal loop

ensure_available() restarts the client only when never-constructed --
discord.py's own reconnect=True already handles a dropped websocket."
```

---

### Task 6: Slack — thin `HealableResource` wrapper

**Files:**
- Modify: `src/stackowl/channels/slack/adapter.py` (class `SlackChannelAdapter`, existing `health_check()` ~line 829, `_last_ping_at`/`_HEALTH_STALE_AFTER_S`)
- Modify: `src/stackowl/scheduler/assembly.py`
- Test: `tests/channels/slack/test_adapter_healable.py` (new)

- [ ] **Step 1: Read the current file** — `src/stackowl/channels/slack/adapter.py` in full, specifically how the socket-mode client is constructed/started and what `_last_ping_at`/`_HEALTH_STALE_AFTER_S` actually gate.

- [ ] **Step 2: Write failing tests**
- Test: `ensure_available()` restarts the socket-mode client when `_last_ping_at` is stale beyond `_HEALTH_STALE_AFTER_S`.
- Test: `ensure_available()` is a no-op when the ping is recent.
- Test: `available`/`unavailable_reason` mirror `health_check()`'s existing staleness check.

- [ ] **Step 3: Run tests, confirm they fail.**

- [ ] **Step 4: Implement `HealableResource`.**

- [ ] **Step 5: Run tests, confirm they pass.**

- [ ] **Step 6: Wire into `assembly.py`** — `healers["slack"] = slack_adapter`, register contributor (gated same as Task 5).

- [ ] **Step 7: Run channels + assembly tests.**

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/channels/slack/adapter.py src/stackowl/scheduler/assembly.py tests/channels/slack/test_adapter_healable.py
git commit -m "feat(slack): wire SlackChannelAdapter into ADR-6 self-heal loop

ensure_available() restarts the socket-mode client past the existing
ping-staleness threshold health_check() already uses to report degraded."
```

---

### Task 7: WhatsApp — `HealableResource` with real browser-driver restart

**Files:**
- Modify: `src/stackowl/channels/whatsapp/adapter.py` (class `WhatsAppChannelAdapter`, `_poll_loop()` ~line 136, existing `health_check()` ~line 390)
- Modify: `src/stackowl/channels/whatsapp/browser.py` (class `WhatsAppBrowserDriver`, `start()`/`stop()`)
- Modify: `src/stackowl/scheduler/assembly.py`
- Test: `tests/channels/whatsapp/test_adapter_healable.py` (new)

**Interfaces:**
- Produces: `WhatsAppChannelAdapter` (or `WhatsAppBrowserDriver` directly — decide based on Step 1's read of which object should own the protocol; the adapter is the more likely fit since it's what gets registered in `healers`, delegating internally to the driver) implements `HealableResource`. Real recovery: stop the current `WhatsAppBrowserDriver`, construct+start a fresh one, re-attach the WhatsApp Web session.

- [ ] **Step 1: Read the current files**

Read `src/stackowl/channels/whatsapp/adapter.py` and `src/stackowl/channels/whatsapp/browser.py` in full. This is the one channel adapter NOT built on `CamoufoxRuntime` — also read `src/stackowl/tools/browser/runtime.py`'s `ensure_available()` (an EXISTING working implementation) as the closest precedent for what "restart a browser session" recovery code looks like in this codebase, even though WhatsApp's driver is a separate class.

- [ ] **Step 2: Write failing tests**

- Test: `ensure_available()` when the browser/page is dead stops the current `WhatsAppBrowserDriver` and starts a fresh one (mock the driver's `start`/`stop` — assert both were called, in the right order, and the adapter's driver reference is swapped to the new instance).
- Test: `ensure_available()` when the driver/poll loop is healthy is a no-op.
- Test: `available`/`unavailable_reason` mirror `health_check()`'s existing signal (`_poll_task is None/done`, or no messages polled yet).
- Test: the `_poll_loop()`'s existing catch-log-continue behavior is unaffected by this change (no regression to the always-alive poll task).

- [ ] **Step 3: Run tests, confirm they fail.**

- [ ] **Step 4: Implement `HealableResource`**

`ensure_available()`: if unhealthy, call the driver's `stop()`, construct+`start()` a fresh `WhatsAppBrowserDriver`, swap the adapter's reference. Follow `CamoufoxRuntime.ensure_available()`'s error-handling/logging shape from your Step 1 read (mirror it, don't reinvent).

- [ ] **Step 5: Run tests, confirm they pass.**

- [ ] **Step 6: Wire into `assembly.py`** — `healers["whatsapp"] = whatsapp_adapter`, register contributor (gated same as Task 5/6 — WhatsApp-enabled check).

- [ ] **Step 7: Run channels + assembly tests.**

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/channels/whatsapp/adapter.py src/stackowl/channels/whatsapp/browser.py src/stackowl/scheduler/assembly.py tests/channels/whatsapp/test_adapter_healable.py
git commit -m "feat(whatsapp): wire WhatsAppChannelAdapter into ADR-6 self-heal loop

ensure_available() stops and restarts WhatsAppBrowserDriver (relaunch +
re-attach WhatsApp Web session) -- the one channel adapter with no prior
recovery capability at all, since it's not built on CamoufoxRuntime."
```

---

### Task 8: MCP — no-op `HealableResource` + new `HealthContributor`

**Files:**
- Modify: `src/stackowl/mcp/client.py` (class `McpClient`)
- Create or modify: an `McpHealthContributor` using `src/stackowl/mcp/probe.py`'s `McpLivenessProbe.probe_all()`
- Modify: `src/stackowl/scheduler/assembly.py`
- Test: `tests/mcp/test_client_healable.py` (new)

**Interfaces:**
- Produces: `McpClient` implements `HealableResource` as a genuine no-op (mirrors `ModelProvider`'s stateless pattern — `available` always `True`, `ensure_available()` a no-op, `register_on_recycled` a no-op/log). `McpHealthContributor` wraps `McpLivenessProbe.probe_all()` results into `HealthStatus`.

- [ ] **Step 1: Read the current files**

Read `src/stackowl/mcp/client.py` in full (confirm it's genuinely stateless per-call — no persistent handle) and `src/stackowl/mcp/probe.py`'s `McpLivenessProbe`/`probe_all()` signature. Read `providers/base.py`'s no-op `HealableResource` implementation (lines ~343-360, already read once tonight) as the exact pattern to mirror.

- [ ] **Step 2: Write failing tests**

- Test: `McpClient.ensure_available()` is a genuine no-op (doesn't raise, doesn't do anything observable) — mirrors the existing `ModelProvider` no-op test if one exists (check `tests/providers/` for it and copy the shape).
- Test: `McpHealthContributor.health_check()` correctly maps `probe_all()`'s down/degraded servers into a `HealthStatus`.

- [ ] **Step 3: Run tests, confirm they fail.**

- [ ] **Step 4: Implement the no-op `HealableResource` on `McpClient`** and the `McpHealthContributor`.

- [ ] **Step 5: Run tests, confirm they pass.**

- [ ] **Step 6: Wire into `assembly.py`** — `healers["mcp"] = mcp_client`, register `McpHealthContributor` (this is a NET-NEW contributor — MCP has no aggregator presence at all today per the design doc's audit).

- [ ] **Step 7: Run mcp + assembly tests.**

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/mcp/client.py src/stackowl/scheduler/assembly.py tests/mcp/test_client_healable.py
# add the contributor file wherever it landed
git commit -m "feat(mcp): add MCP health visibility to ADR-6 loop (no-op heal)

McpClient is fully stateless per-call already (fresh connection every
discover_tools/call_tool with its own bounded retry) so ensure_available()
is a genuine no-op, matching the ModelProvider reference pattern. The real
gap closed here is McpHealthContributor -- MCP had zero aggregator
presence before this, so an outage was undetectable."
```

---

### Task 9: Durable-task liveness watchdog

**Files:**
- Modify: `src/stackowl/pipeline/durable/recovery.py` (factor the per-task claim→reconstruct→resume unit out of the boot-only `recover()`/`recover_durable_tasks()` path so it's shared, not duplicated)
- Create: `src/stackowl/scheduler/handlers/task_liveness_sweep.py` (new recurring handler, mirroring `health_sweep.py`'s `JobHandler` shape)
- Modify: `src/stackowl/scheduler/assembly.py` (register the new handler as a recurring job + as a `healers`/`HealthContributor` entry)
- Test: `tests/pipeline/durable/test_task_liveness_sweep.py` (new)

**Interfaces:**
- Produces: a shared function/method (extracted from `recovery.py`) callable both from boot (`recover_durable_tasks`) and from the new periodic handler — e.g. `async def reclaim_stale_task(store, task_row, backend) -> ...` performing the same claim(CAS)→reconstruct-checkpoint→background-resume sequence `recovery.py:267-312` already does per-task, parameterized so it doesn't assume "called only at boot."
- Produces: `TaskLivenessSweepHandler` (new `JobHandler` subclass, `handler_name = "task_liveness_sweep"`) whose `execute()` queries `tasks` for `status='running'` AND `updated_at` older than a staleness threshold (pick a constant, e.g. 10 minutes — justify against typical single-drive duration if you can find one referenced elsewhere, otherwise state your reasoning in the commit message), reclaims each via the shared function above.
- Produces: this handler ALSO exposed as a `HealableResource`/`HealthContributor` pair — `available` = zero currently-stale running tasks; `ensure_available()` triggers an immediate reclaim sweep (calling the same shared function for every currently-stale row, synchronously, not waiting for the next scheduled tick).

- [ ] **Step 1: Read the current files**

Read `src/stackowl/pipeline/durable/recovery.py` in full (the whole boot-recovery flow, especially lines 143-336 per the earlier audit — claim/reconstruct at ~267-294, background drive launch at ~296-336). Read `src/stackowl/scheduler/handlers/health_sweep.py` in full as the shape to mirror for the new handler (constructor signature, `execute()` pattern, `JobResult` construction, alerting). Read `src/stackowl/scheduler/base.py`'s `JobHandler` base class. Read `src/stackowl/pipeline/durable/store.py`'s `DurableTaskStore` API (`claim_for_recovery`, `list`, `load_checkpoint`, `update_status`) to confirm the exact method signatures this task will call.

- [ ] **Step 2: Extract the shared per-task reclaim unit**

Refactor `recovery.py` so the claim→reconstruct→background-resume logic for ONE task row is a standalone function/method, callable with a task row + store + backend, independent of the "scan all orphans at boot" loop around it. `recover_durable_tasks()` (boot path) now calls this shared unit per orphan it finds — behavior must be BYTE-IDENTICAL to today's boot recovery (this is a refactor, not a behavior change, for the existing boot path). Write a test confirming boot-path behavior is unchanged before touching the new periodic caller (regression guard for the refactor itself).

- [ ] **Step 3: Write failing tests for the periodic sweep**

- Test: a `tasks` row with `status='running'` and `updated_at` older than the staleness threshold gets reclaimed (claimed, checkpoint reconstructed, background resume launched) by `TaskLivenessSweepHandler.execute()`.
- Test: a `tasks` row with `status='running'` and RECENT `updated_at` is left alone (not falsely reclaimed while genuinely still executing).
- Test: a `tasks` row with `status='pending'`/`'completed'`/`'failed'` is never touched by the sweep.
- Test: the `HealableResource` pairing — `available` is `False` when a stale row exists, `True` when none do; `ensure_available()` reclaims immediately without waiting for the handler's own scheduled cadence.

- [ ] **Step 4: Run tests, confirm they fail.**

- [ ] **Step 5: Implement `TaskLivenessSweepHandler`**

New file `src/stackowl/scheduler/handlers/task_liveness_sweep.py`, mirroring `health_sweep.py`'s constructor/logging/`JobResult` shape. `execute()` queries `DurableTaskStore.list` (or equivalent) filtered to `status='running'` and stale `updated_at`, calls the Step 2 shared reclaim unit per stale row, returns a `JobResult` summarizing how many were reclaimed.

- [ ] **Step 6: Implement the `HealableResource`/`HealthContributor` pairing**

Add a small wrapper (co-located in the same file or a thin adjacent class) exposing `available`/`unavailable_reason`/`ensure_available()` per the Interfaces section above, backed by the same store query + shared reclaim unit.

- [ ] **Step 7: Run tests, confirm they pass.**

- [ ] **Step 8: Wire into `assembly.py`**

Register `TaskLivenessSweepHandler` as a new recurring `JobScheduler` job (follow whatever pattern seeds `health_sweep`'s own recurring job row — check `scheduler/assembly.py` and/or a seed-migration for how `health_sweep` itself gets its initial `jobs` row, and mirror it for `task_liveness_sweep`). Add `healers["task_liveness"] = <the wrapper from Step 6>` and register its `HealthContributor` in `_build_health_aggregator`.

- [ ] **Step 9: Run the durable-pipeline + scheduler test directories.**

Run: `uv run pytest tests/pipeline/durable/ tests/scheduler/ -v` (this is the broadest test run in the whole plan since Step 2 touched shared boot-recovery code — confirm zero regressions here specifically).

- [ ] **Step 10: Commit**

```bash
git add src/stackowl/pipeline/durable/recovery.py src/stackowl/scheduler/handlers/task_liveness_sweep.py src/stackowl/scheduler/assembly.py tests/pipeline/durable/test_task_liveness_sweep.py
git commit -m "feat(scheduler): add durable-task liveness watchdog

Task recovery previously ran only at boot -- a task whose backing process
died mid-drive while the server kept running stayed stuck in
status='running' until the next restart. New recurring
task_liveness_sweep handler reclaims stale running tasks through the SAME
claim-reconstruct-resume logic boot recovery uses (factored into a shared
unit, not duplicated), and exposes itself as a HealableResource so
health_sweep can trigger an immediate reclaim on demand."
```

---

## After all 9 tasks: deployment note

Same as tonight's earlier arc — none of this is live until the server restarts (mono role, no auto-restart on SIGTERM; manual relaunch required, same procedure used earlier tonight: kill the process group, confirm exit via `pgrep`, relaunch in tmux). Given the scope of this arc (9 subsystems, including a boot-recovery refactor in Task 9), a full targeted test pass across `tests/scheduler/`, `tests/embeddings/`, `tests/memory/`, `tests/channels/`, `tests/mcp/`, `tests/pipeline/durable/` should run clean before that restart — not just each task's own directory in isolation.

# Element 16b — Perches & Ambient Mesh Unification (Design)

**Status:** Spec — awaiting Boss approval
**Date:** 2026-05-04
**Author:** Claude (Opus 4.7) with Boss
**Phase:** BMAD Phase 4 (brainstorm/spec) — output of Phases 1-3 audit + research + architecture review
**Locked new file count:** 2 (`src/signals/pool.ts`, `src/signals/collectors.ts`). Net file delta: **−2**.

---

## 1. Problem

StackOwl ships two parallel ambient observation systems that don't talk to each other:

- **`src/perch/*`** — partly live. `FilePerch` + `PerchManager` are wired into 4 boot paths in `src/index.ts` and broadcast `🔭 PERCH ALERT:` messages via the gateway. But the system is goal-blind, classification is hardcoded (`file-perch.ts:90-122`), and it walks straight into the "too aggressive proactive interjection" failure mode that Cursor Tab and ChatGPT nudges have already shipped to user complaint threads (Phase 2 research).

- **`src/ambient/*`** — fully dead. `ContextMesh` is never instantiated. `AmbientContextLayer` reads `session.ambientContext`, which nothing writes. The collectors (Git, Time, System, ActiveFile, Clipboard) are well-written but unreachable.

The two systems use incompatible types: `PerchEvent` (3 sources) vs `ContextSignal` (11 sources). Neither integrates with the modern primitives the rest of the codebase composes — `GatewayEventBus`, `ContextPipeline`, `Memory` (Element 15), `IntelligenceRouter`, `GoalVerifier`.

This spec unifies them into a single goal-conditioned ambient signal pipeline.

## 2. Goals

1. **One observation system.** Standardize on `ContextSignal`. Delete `PerchEvent` and `PerchManager`. Net delete > add: 4 files removed, 2 added.
2. **Goal-conditioned surfacing.** Signals are observed silently and only surface to the user when an active goal verifies them as `ADVANCES`. No active goal → no interjection, ever.
3. **No hardcoded classification.** Replace `ALLOWED_EXTS` filters and magic-number priority bumps with `IntelligenceRouter` cheap-tier classification.
4. **Compose existing primitives.** `GatewayEventBus`, `ContextPipeline.AmbientContextLayer`, `GoalVerifier`, `IntelligenceRouter`, `MemoryStore` — none of these are new.
5. **Channel parity.** Signal narration is rendered once and dispatched identically to CLI, Telegram, Slack, Voice, Web via the gateway.
6. **Privacy-by-default.** Per-collector consent ledger in `stackowl.config.json`. Clipboard default-OFF (ChatGPT Atlas CVE precedent).

## 3. Non-goals

- **No new collectors beyond what already exists** (Git, Time, System, ActiveFile, Clipboard) plus the file-watcher promoted from Perch. Email and Calendar consent slots ship in the schema but no collectors are added in this element.
- **No Parliament-debated retention.** Out of scope; revisit if telemetry shows pool churn is a problem.
- **No DNA mutation driven by signal patterns.** Out of scope; revisit post-ship.
- **No global telemetry dashboard.** Bus events are recorded; consumption is left to future telemetry work.
- **No cost cap on classifier calls.** Will be measured post-ship; if router calls/min becomes a problem, add throttling then.

## 4. Architecture

### 4.1 Locked architectural decisions (Phase 3 review by Winston)

These are **locked** — they are not up for re-litigation in this spec. They constrain the design that follows.

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Unified type:** merge on `ContextSignal`. Delete `PerchEvent`. 5 touch sites. | `ContextSignal` is the richer 11-source type. PerchEvent is the 3-source subset. Merge direction is obvious. |
| 2 | **SignalPool ownership:** repurpose existing `GatewayContext.contextMesh` slot (`gateway/types.ts:226`) as `signalPool`. Constructed in `buildBootstrap`, started by existing `initFeatureModules` dispatcher (`gateway/core.ts:2730`). | Slot already exists and is dormant. Existing init guard handles startup. No new gateway plumbing. |
| 3 | **Bus contract:** reclaim dormant `perch:event` slot in `event-bus.ts:9` with 5 typed events: `signal:emitted`, `signal:expired`, `signal:promoted`, `signal:suppressed`, `signal:consent_changed`. Emission decoupled from user-surfacing. | Element 1 backbone is the right place for cross-channel pub/sub. Decoupling emission from surfacing is the goal-conditioning gate. |
| 4 | **Goal-conditioning gate:** two-stage. Stage 1: `IntelligenceRouter` cheap-tier prefilter per-signal at pool insert. Stage 2: `GoalVerifier.verify` per-signal on insert (when priority bumped to high) **and** batch sweep at heartbeat tick against `goalGraph.getActive()`. No active goal → never user-surface. | Hybrid (per-signal + batch) catches both fresh signals and goal-drift cases without paying full verifier cost on every signal. |
| 5 | **ContextPipeline integration:** keep `AmbientContextLayer`, repoint `build()` to `signalPool.toContextBlock(8)`. Gate `shouldFire` on `hasHighPrioritySignals()`. Priority `145`, maxTokens `400`. | Layer slot already exists. Just rewire the data source. |
| 6 | **Privacy/consent ledger:** `stackowl.config.json:perches.consent` block. No schema migration. Global, not per-channel. Defaults: file/git/time/system/perch=on, clipboard=off, email/calendar=off. | Config is already gitignored, atomic write-rename available, no DB ceremony. ChatGPT Atlas CVE precedent justifies clipboard default-off. |

### 4.2 File structure

**New (2 files):**

- `src/signals/pool.ts` (~200 LOC) — `SignalPool` class. Owns admission gates, classifier+verifier orchestration, in-memory pool, eviction, context-block rendering.
- `src/signals/collectors.ts` (~350 LOC) — Six `SignalCollector` implementations: `GitStatusCollector`, `TimeContextCollector`, `SystemCollector`, `ActiveFileCollector`, `ClipboardCollector`, `FileSystemCollector` (promoted from `perch/file-perch.ts`).

**Rewrite in place (3 files):**

- `src/context/layers/ambient.ts` (~30 LOC) — repointed to `SignalPool`.
- `src/config/loader.ts` — adds `mutateConsent(source, granted)` method (atomic write-rename).
- `src/gateway/event-bus.ts:9` — replace dormant `perch:event` slot with 5 typed signal events.

**Touch (slot rename + narration template addition):**

- `src/gateway/types.ts:226` — `contextMesh?: ContextMesh` → `signalPool?: SignalPool`.
- `src/gateway/core.ts:2730-2735` — call `signalPool.start()` instead of `contextMesh.start()`.
- `src/intent/proactive-loop.ts:18,40,102` — read-only consumer renamed.
- `src/index.ts:1202` — `undefined` placeholder renamed.
- `src/gateway/narration-formatter.ts` (existing module) — add a single template for `signal:promoted` events; gateway wires the bus subscriber. No new file.

**Keep as-is (canonical types):**

- `src/ambient/types.ts` (46 LOC) — `ContextSignal`, `SignalSource`, `SignalPriority`, `SignalCollector`, `MeshState`, `AmbientRule`. This is the type backbone the new code uses.

**Delete (4 files):**

- `src/perch/manager.ts` (111 LOC).
- `src/perch/file-perch.ts` (229 LOC) — engine (hash + debounce) is preserved by being copied into `FileSystemCollector`.
- `src/ambient/mesh.ts` (197 LOC) — replaced by `SignalPool`.
- `src/ambient/collectors.ts` (304 LOC) — replaced by `src/signals/collectors.ts`.

**Net file delta: −2.** (4 deleted + 2 added; rewrites are in place.)

### 4.3 Component contracts

**`SignalPool` (`src/signals/pool.ts`):**

```typescript
export interface SignalPoolDeps {
  bus: GatewayEventBus;
  classifier: { classify(signal: ContextSignal): Promise<{ keep: boolean; confidence: number }> };
  verifier: GoalVerifier;
  goalGraph: GoalGraph;
  config: { maxSignals: number; enabledSources: SignalSource[]; consent: ConsentMap };
  memoryStore?: MemoryStore;
  workspacePath: string;
}

export class SignalPool {
  constructor(deps: SignalPoolDeps);
  addCollector(c: SignalCollector): void;
  start(): void;            // idempotent
  stop(): void;             // idempotent
  getState(): MeshState;
  toContextBlock(maxSignals?: number): string;     // default 8
  hasHighPrioritySignals(): boolean;                // checks userSurfaceable && priority==="high"
  injectSignal(signal: ContextSignal): Promise<void>; // collector-facing entry point
}
```

**`SignalCollector` (extends `src/ambient/types.ts:27-31`):**

The existing interface only supports poll-mode (`collect(): Promise<ContextSignal[]>`). To support push-mode (file watcher), the interface is widened. This is a type-only change — no existing consumer (the type was unused at runtime).

```typescript
export interface SignalCollector {
  readonly source: SignalSource;
  readonly mode: "poll" | "push";
  readonly intervalMs?: number;                    // required when mode==="poll"
  collect?(): Promise<ContextSignal[]>;             // required when mode==="poll"
  start?(emit: (s: ContextSignal) => void): void;  // required when mode==="push"
  stop?(): void;                                    // required when mode==="push"
}
```

**`AmbientContextLayer` (`src/context/layers/ambient.ts` rewritten):**

```typescript
export class AmbientContextLayer implements ContextLayer {
  name = "AmbientContextLayer";
  priority = 145;
  maxTokens = 400;
  produces = ["ambient"];
  dependsOn = [];
  constructor(private signalPool: SignalPool) {}
  getCacheKey() { return null; }
  shouldFire(t: TriageSignals) {
    return !t.isConversational && this.signalPool.hasHighPrioritySignals();
  }
  async build() { return this.signalPool.toContextBlock(8); }
}
```

**Bus events (`src/gateway/event-bus.ts:9`):**

Phase 3 §3 locked **4 signal-lifecycle events**. This spec adds a 5th meta-event (`signal:consent_changed`) which is a config event, not a signal-lifecycle event — additive, not in conflict with the locked decision.

```typescript
// 4 signal-lifecycle events (Phase 3 §3 locked)
"signal:emitted":         { signal: ContextSignal };
"signal:expired":         { signal: ContextSignal; reason: "ttl" | "evicted" };
"signal:promoted":        { signal: ContextSignal; goal: GoalRef; rationale: string; verdict: "ADVANCES" };
"signal:suppressed":      { signal: ContextSignal; verdict: "NEUTRAL" | "PARTIAL" | "BLOCKED" };

// 5th meta-event (additive)
"signal:consent_changed": { source: SignalSource; granted: boolean };
```

`GoalRef` and `GoalGraph` are the existing types from `src/goals/graph.ts` (composed, not redefined).

**Consent map (`stackowl.config.json:perches.consent`):**

Keys match the existing `SignalSource` union in `src/ambient/types.ts:1-12`. Default-on by safety class. Sources not present in the config default to the matrix below.

```json
{
  "perches": {
    "consent": {
      "git": true,
      "active_file": true,
      "time_of_day": true,
      "system": true,
      "perch": true,
      "heartbeat": true,
      "user_pattern": true,
      "clipboard": false,
      "email": false,
      "calendar": false,
      "weather": false
    }
  }
}
```

Type definition: `type ConsentMap = Partial<Record<SignalSource, boolean>>` — partial because absent keys fall back to defaults.

**`ConfigLoader.mutateConsent(source, granted)`:**

Reads current `stackowl.config.json`, mutates the `perches.consent[source]` field, writes to `stackowl.config.json.tmp`, then `rename()` atomically. In-process mutex ensures sequential ordering. On success, emits `signal:consent_changed` on the bus.

## 5. Data flow

### 5.1 Signal lifecycle (collector tick → user-visible context)

```
[1] Collector ticks (interval or fs event)
        emits raw ContextSignal { id, source, content, priority:"low", ts, ttlMs }

[2] SignalPool.injectSignal(signal)
        ├─ Consent gate: config.consent[signal.source] === false → drop silently
        └─ Source-enabled gate: enabledSources excludes source → drop silently

[3] Stage 1 — Cheap-tier prefilter (per-signal)
        classifier.classify(signal) → { keep, confidence }
          keep=false                  → drop, no bus event
          keep=true, conf<0.7         → admit at "low"
          keep=true, conf 0.7-0.9     → admit promoted to "medium"
          keep=true, conf>0.9         → admit promoted to "high"

[4] Pool admission
        push to pool.signals
        if pool.size > maxSignals: evict lowest-priority + oldest first
        bus.emit("signal:emitted", { signal })

[5] Stage 2 — Per-signal goal-conditioning (only if priority === "high")
        goal = goalGraph.getActive()
        if !goal: skip — high signal stays in pool but never user-surface
        verdict = await verifier.verify({ signal, goal, recentTurns: last 3 })
          ADVANCES → mark userSurfaceable=true; bus.emit("signal:promoted")
          NEUTRAL|PARTIAL|BLOCKED → bus.emit("signal:suppressed")

[6] Heartbeat tick (Stage 3 batch sweep)
        For up to 5 signals where !userSurfaceable && priority>="medium":
          re-run verifier.verify against current goal (catches goal drift)
        TTL expiry: drop expired → bus.emit("signal:expired")

[7] ContextPipeline build (next user turn)
        AmbientContextLayer.shouldFire?
          !isConversational && signalPool.hasHighPrioritySignals()
        → build() returns signalPool.toContextBlock(8)
            renders top-8 userSurfaceable signals as
            <ambient_context>...</ambient_context> block

[8] Memory promotion (long-term retention)
        On signal:promoted, if memoryStore present:
          memoryStore.store({
            kind: "ambient_signal",
            content: signal.content,
            metadata: { source, goal: goal.id, rationale, verdict }
          })
        SleepTimeConsolidator (Element 15) may later cluster these
        into semantic memories — frontier asset reuse, no new code.
```

### 5.2 Channel parity surface

Single template in `narration-formatter.ts`, rendered identically across CLI/Telegram/Slack/Voice/Web:

```
🔭 [{source}] {summary} — {goal-relevance rationale}
```

Example: `🔭 [git] 12 unstaged changes in src/signals/ — advances "ship Element 16b" (verdict: ADVANCES)`

The gateway dispatches the `signal:promoted` event to all active channels via existing channel-handler infrastructure (Element 1 backbone). No per-channel branching code is added.

### 5.3 Timing & cost model

| Decision | Value | Why |
|----------|-------|-----|
| Stage 1 classifier model | cheap tier (haiku-class) | Per-signal call. Volume ~10/min. Cost-bound. |
| Stage 2 verifier model | whatever GoalVerifier already uses | Reuses Element 7 contract. |
| Heartbeat batch size | up to 5 signals/tick | Caps per-tick verifier cost. Lower priority defers. |
| Pool maxSignals | 32 | Empirical guess; revisit post-ship if eviction churn observed. |
| Default TTL | 5 min | From existing ambient/types.ts pattern; per-collector overridable. |

### 5.4 Data isolation

- Pool is in-memory only. Crash → all signals lost. **Acceptable** — collectors retick on next interval.
- Memory promotion only on `ADVANCES` verdict. No raw clipboard or system stats leak to long-term store unless they earned it.
- Consent state lives in `stackowl.config.json` (gitignored). Atomic write-rename on mutation. No DB migration.

## 6. Error handling

### 6.1 Failure modes by component

| # | Component | Failure | Behavior |
|---|-----------|---------|----------|
| 1 | Collector tick throws | fs.watch error, git binary missing | Catch in pool wrapper. Increment `failureCount`. After 3 consecutive failures, deregister + log warn once. Pool continues. |
| 2 | Collector tick hangs | network FS, slow `git status` | 2s soft timeout per tick. Drop signal, increment failureCount. |
| 3 | Stage 1 classifier throws | router unavailable, model timeout | **Fail-closed: drop signal.** Better to lose a signal than admit unclassified. Log debug. |
| 4 | Stage 1 returns malformed JSON | `{keep:"yes"}` etc | Treat as `{keep:false}`. No retry. |
| 5 | Stage 2 verifier throws | GoalVerifier model down | Signal stays in pool with `userSurfaceable=false`. Retried on next heartbeat batch. No bus event. |
| 6 | `goalGraph.getActive()` returns null | No active goal | Skip Stage 2. Signals admitted but never surface. **By design.** |
| 7 | Pool overflow | maxSignals exceeded | Evict by `(priority asc, ts asc)`. `signal:expired` for evicted. |
| 8 | `memoryStore.store()` throws | DB locked, disk full | Catch + log warn. Do **not** block bus emit. |
| 9 | Consent config mutation race | Two channels granting at once | In-process mutex + atomic rename. Both grants converge. |
| 10 | Consent file missing | Fresh install | Defaults applied. Don't write back until first explicit mutation. |
| 11 | Consent prompt timeout | LLM asks, no user response | Default-deny. No retry storm. |
| 12 | `SignalPool.start()` called twice | Double-init from boot | Idempotent: no-op if already started. |
| 13 | FileSystemCollector workspace deleted | Workspace moved | fs.watch errors → caught at #1. |
| 14 | Bus subscriber throws on promoted | Narration formatter bug | Element 1 isolates subscribers. Others continue. |

### 6.2 Fail-closed principle

**Default: fail-closed.** A dropped signal is invisible; a wrongly-promoted signal is user-visible noise (the Cursor Tab failure mode). Every classifier/verifier failure path drops, never admits with degraded confidence.

**Single exception:** memory promotion failure (#8) is fail-open — the signal still narrates, just doesn't get long-term stored. The user-visible event already passed both gates; missing the DB write is recoverable.

### 6.3 Telemetry via existing bus

Element 1's `GatewayEventBus` records every emit. So:

- `signal:emitted` count = pool admission rate
- `signal:emitted` − `signal:promoted` = silent observation rate (should be the bulk)
- `signal:promoted` count = user-visible interjection rate (the Cursor Tab metric — keep it low)
- `signal:suppressed` reason histogram = which verdicts are firing

No dashboard in scope. The data is on the bus; future telemetry consumers or manual queries can audit.

## 7. Testing strategy

### 7.1 Test layers

| Layer | Target | Count target |
|-------|--------|--------------|
| Unit — SignalPool internals | admission, eviction, gates | ~25 |
| Unit — collectors in isolation | fs/git/clipboard mocked | ~15 |
| Integration — pool + classifier + verifier wired with stubs | end-to-end signal lifecycle | ~10 |
| Integration — pool → bus → AmbientContextLayer.build() | render pipeline | ~5 |
| Integration — consent atomic write-rename | tmp dir, mutex | ~5 |
| Channel parity — `signal:promoted` rendered identically | gateway harness | ~3 |
| Regression — boot paths still work | smoke | ~2 |

**Total: ~65 tests.**

### 7.2 Critical test cases

**Stage 1 prefilter (admission)**
- classifier `keep:false` → dropped, no `signal:emitted`
- classifier `keep:true, conf:0.5` → admitted at `low`
- classifier `keep:true, conf:0.8` → promoted to `medium`
- classifier `keep:true, conf:0.95` → promoted to `high`
- classifier throws → dropped, no event
- classifier returns malformed JSON → treated as drop

**Admission gates**
- Consent denied → dropped before classifier (no router cost)
- Source not in `enabledSources` → dropped before classifier
- Pool at capacity → eviction by `(priority asc, ts asc)`, `signal:expired` emitted

**Stage 2 goal-conditioning**
- No active goal → high signal stays `userSurfaceable=false`, no `signal:promoted`
- ADVANCES → `userSurfaceable=true`, `signal:promoted` with rationale
- NEUTRAL → `signal:suppressed`
- BLOCKED → `signal:suppressed`, signal stays in pool
- Verifier throws → signal stays in pool, retried on next heartbeat

**Heartbeat batch sweep**
- NEUTRAL signal under goal A → goal switches to B → batch sweep promotes
- TTL expiry during sweep → `signal:expired`
- Batch size cap (5/tick) honored

**Collectors (per collector)**
- Happy path: emits expected `ContextSignal` shape
- Failure: throws → caught, `failureCount` increments
- Hang: 2s timeout drops signal
- 3 consecutive failures → deregister, warn once

**FileSystemCollector specifics**
- Hash dedup: same content twice → second emit suppressed
- Debounce: 5 events in 5s → 1 emitted
- Coarse prefilter rejects: `node_modules/`, `.git/`, `.tmp`, dotfiles
- Coarse prefilter passes: `.ts`, `.js`, `.md`, anything else (classifier decides)

**ClipboardCollector consent**
- Default off → never emits even if `enabledSources` includes it
- Granted → emits, content truncated to 500 chars

**Consent mutation**
- `mutateConsent("clipboard", true)` writes tmp + renames atomically
- Concurrent mutations to different sources → both persisted
- Config file missing → defaults applied, no write until explicit
- Bus emits `signal:consent_changed`

**AmbientContextLayer integration**
- `shouldFire` false when conversational
- `shouldFire` false when no high-priority surfaceable signals
- `shouldFire` true when ≥1 high-priority surfaceable signal
- `build()` returns top-8 as `<ambient_context>` block
- Output respects `maxTokens:400`

**Channel parity**
- `signal:promoted` renders identically in CLI, Telegram, Slack stubs
- Voice variant matches text variant content

**Boot regression**
- Pool starts in all 4 init paths (`src/index.ts:825, 1893, 1972, 2169`)
- Pool stop is idempotent
- Double-start is no-op
- Existing CLI smoke passes

### 7.3 Shared harness

- `__tests__/helpers/signal-pool-harness.ts` — builds a `SignalPool` with stub bus, controllable stub classifier + verifier, in-memory consent, fake clock for TTL/debounce.
- Reuse Element 7 `GoalVerifier` test harness.
- Reuse Element 1 `GatewayEventBus` event recorder (`bus.recorded`).

### 7.4 Out of scope for tests

- Real LLM calls (stub-only; matches Element 16a precedent).
- Real `fs.watch` macOS-vs-Linux quirks (mock node:fs.watch, matches `FilePerch` precedent).
- Performance under load (measure post-ship via bus telemetry, not in CI).

## 8. Migration & rollout

- **No DB migration.** Schema-free.
- **No config migration.** Missing `perches.consent` block defaults to safe values.
- **Boot guard:** existing `initFeatureModules` dispatcher already runs `if (ctx.contextMesh) ctx.contextMesh.start()`. After rename, becomes `if (ctx.signalPool) ctx.signalPool.start()`. Slot stays optional — gateway boots fine without ambient.
- **Old `🔭 PERCH ALERT:` broadcasts disappear.** They never reached steady-state production usage anyway (goal-blind, hardcoded). Replacement is the goal-conditioned `signal:promoted` narration.
- **Rollback:** revert the merge commit. Pool slot was always optional; absence of pool = no ambient, but no crash.

## 9. Open questions deferred to post-ship

- Does pool churn (eviction rate) call for raising `maxSignals` from 32?
- Does classifier call rate justify a per-source rate limiter?
- Should email/calendar collectors be added in Element 17, or is the consent slot enough for now?
- Is per-signal Stage 2 verification too eager? (Could defer all Stage 2 to heartbeat batch.)

These are measurable post-ship via bus telemetry. They are not blocking this element.

## 10. Acceptance criteria

This element is shipped when:

1. ✅ `src/signals/pool.ts` and `src/signals/collectors.ts` exist; `src/perch/manager.ts`, `src/perch/file-perch.ts`, `src/ambient/mesh.ts`, `src/ambient/collectors.ts` are deleted.
2. ✅ `src/context/layers/ambient.ts` reads from `SignalPool` and produces non-empty output when high-priority surfaceable signals exist.
3. ✅ `src/gateway/event-bus.ts` declares the 5 typed signal events; `perch:event` is gone.
4. ✅ `src/gateway/types.ts:226` slot is renamed; all read sites updated.
5. ✅ `stackowl.config.json` accepts `perches.consent` block; `mutateConsent` writes atomically; defaults match the matrix.
6. ✅ ~65 tests pass, covering all critical cases listed in §7.2.
7. ✅ Existing test suite passes (no regressions).
8. ✅ Boot smoke test passes in all 4 init paths.
9. ✅ Channel parity test confirms identical narration across CLI/Telegram/Slack stubs.
10. ✅ No hardcoded keyword arrays or magic-number priority bumps remain in the new code.

---

**Next step:** invoke `superpowers:writing-plans` to convert this spec into a TDD task plan saved to `docs/superpowers/plans/2026-05-04-element16b-perches.md`.

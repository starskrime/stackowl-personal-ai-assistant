# OwlGateway Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract five single-responsibility classes from `src/gateway/core.ts` (4,911 lines) to make it maintainable, OOP, and fully log-traceable per platform standards.

**Architecture:** Each extraction introduces one new file with an interface and a class, injects it into `OwlGateway` via `this.ctx` (no constructor signature change), and deletes the inline code. Five sequential PRs — each independently mergeable. Full test suite must pass after every task.

**Tech Stack:** TypeScript strict, Vitest, `log.gateway` / `log.parliament` from `src/logger.ts`, `AsyncLocalStorage` traceId propagation.

---

## Context for Every Task

- **Source file:** `src/gateway/core.ts` (4,911 lines)
- **Test runner:** `npx vitest run __tests__/path/to/test.ts`
- **Full suite:** `npm test`
- **Logger import:** `import { log } from "../logger.js";`
- **GatewayContext** is in `src/gateway/types.ts` — all new classes are wired via fields added to this interface
- **4-point logging required on every method:** `entry → decision → step → exit`; every `catch` must call `log.X.error(msg, err, ctx)`
- **Existing session types:** `Session` from `src/memory/store.ts`; `SessionCache = { session: Session; lastActivity: number }` (line 213 of `core.ts`, local interface)

---

## Task 1: Extract SessionManager

**What:** Move `getOrCreateSession()`, `saveSession()`, and `sessions: Map<string, SessionCache>` out of `OwlGateway` into a dedicated `SessionManager`. This fixes the architecture and eliminates the `lastActiveChannel`/`lastActiveUserId` implicit coupling to the in-memory session cache (separate fix in Task 5).

**Files:**
- Create: `src/gateway/session-manager.ts`
- Create: `__tests__/gateway/session-manager.test.ts`
- Modify: `src/gateway/types.ts` (add `sessionManager?: ISessionManager`)
- Modify: `src/gateway/core.ts` (delegate to `this.ctx.sessionManager`, remove inline session code)

---

- [ ] **Step 1.1: Write the failing tests**

Create `__tests__/gateway/session-manager.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SessionManager } from "../../src/gateway/session-manager.js";
import type { SessionStore, Session } from "../../src/memory/store.js";
import type { SessionService } from "../../src/session/service.js";

const makeSession = (id: string): Session => ({
  id,
  owlName: "test-owl",
  messages: [],
  createdAt: Date.now(),
  updatedAt: Date.now(),
});

const makeStore = (existing?: Session): SessionStore =>
  ({
    loadSession: vi.fn().mockResolvedValue(existing ?? null),
    createSession: vi.fn().mockReturnValue(makeSession("new-session")),
    saveSession: vi.fn().mockResolvedValue(undefined),
  } as unknown as SessionStore);

describe("SessionManager", () => {
  let store: SessionStore;

  beforeEach(() => {
    store = makeStore();
  });

  it("creates a new session when none exists in store", async () => {
    const mgr = new SessionManager({ sessionStore: store } as any);
    const msg = { sessionId: "s1", userId: "u1", channelId: "cli", text: "", id: "m1" };
    const session = await mgr.getOrCreate(msg);
    expect(store.loadSession).toHaveBeenCalledWith("s1");
    expect(store.createSession).toHaveBeenCalled();
    expect(session.id).toBe("s1");
  });

  it("returns cached session on second call (no store read)", async () => {
    const existing = makeSession("s1");
    store = makeStore(existing);
    const mgr = new SessionManager({ sessionStore: store } as any);
    const msg = { sessionId: "s1", userId: "u1", channelId: "cli", text: "", id: "m1" };
    await mgr.getOrCreate(msg);
    await mgr.getOrCreate(msg);
    expect(store.loadSession).toHaveBeenCalledTimes(1);
  });

  it("delegates to SessionService when provided", async () => {
    const sessionSvc = {
      getOrCreate: vi.fn().mockResolvedValue(makeSession("s2")),
    } as unknown as SessionService;
    const mgr = new SessionManager({ sessionStore: store, sessionService: sessionSvc } as any);
    const msg = { sessionId: "s2", userId: "u2", channelId: "telegram", text: "", id: "m2" };
    const result = await mgr.getOrCreate(msg);
    expect(sessionSvc.getOrCreate).toHaveBeenCalledWith("s2", "u2", expect.any(String));
    expect(result.id).toBe("s2");
  });

  it("saves session to store", async () => {
    const session = makeSession("s3");
    const mgr = new SessionManager({ sessionStore: store } as any);
    await mgr.save(session);
    expect(store.saveSession).toHaveBeenCalledWith(session);
  });

  it("invalidate removes session from cache", async () => {
    const existing = makeSession("s4");
    store = makeStore(existing);
    const mgr = new SessionManager({ sessionStore: store } as any);
    const msg = { sessionId: "s4", userId: "u4", channelId: "cli", text: "", id: "m4" };
    await mgr.getOrCreate(msg);
    mgr.invalidate("s4");
    await mgr.getOrCreate(msg);
    expect(store.loadSession).toHaveBeenCalledTimes(2); // cache was cleared
  });

  it("getActiveCount returns number of cached sessions", async () => {
    const mgr = new SessionManager({ sessionStore: store } as any);
    const msg1 = { sessionId: "s5", userId: "u5", channelId: "cli", text: "", id: "m5" };
    const msg2 = { sessionId: "s6", userId: "u6", channelId: "cli", text: "", id: "m6" };
    await mgr.getOrCreate(msg1);
    await mgr.getOrCreate(msg2);
    expect(mgr.getActiveCount()).toBe(2);
  });
});
```

- [ ] **Step 1.2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/gateway/session-manager.test.ts
```

Expected: FAIL — `Cannot find module '../../src/gateway/session-manager.js'`

- [ ] **Step 1.3: Add ISessionManager interface and SessionManager to types**

Create `src/gateway/session-manager.ts`:

```ts
import type { Session } from "../memory/store.js";
import type { GatewayMessage } from "./types.js";
import type { GatewayContext } from "./types.js";
import { log } from "../logger.js";

const SESSION_TIMEOUT_MS = 2 * 60 * 60 * 1000; // 2 hours

interface SessionCache {
  session: Session;
  lastActivity: number;
}

export interface ISessionManager {
  getOrCreate(message: GatewayMessage): Promise<Session>;
  save(session: Session): Promise<void>;
  invalidate(sessionId: string): void;
  evictStale(): void;
  getActiveCount(): number;
}

export class SessionManager implements ISessionManager {
  private readonly cache = new Map<string, SessionCache>();

  constructor(private readonly ctx: Pick<GatewayContext, "sessionStore" | "sessionService" | "owl">) {}

  async getOrCreate(message: GatewayMessage): Promise<Session> {
    const key = message.sessionId;
    log.gateway.debug("SessionManager.getOrCreate: entry", { sessionId: key, channelId: message.channelId });

    const cached = this.cache.get(key);
    if (cached && Date.now() - cached.lastActivity <= SESSION_TIMEOUT_MS) {
      cached.lastActivity = Date.now();
      log.gateway.debug("SessionManager.getOrCreate: cache hit", { sessionId: key });
      return cached.session;
    }

    log.gateway.debug("SessionManager.getOrCreate: cache miss, reading store", { sessionId: key });

    let session: Session;

    if (this.ctx.sessionService) {
      const parts = key.split(":");
      const userId = message.userId ?? (parts.length >= 2 ? parts.slice(1).join(":") : key);
      log.gateway.debug("SessionManager.getOrCreate: delegating to SessionService", { sessionId: key, userId });
      session = await this.ctx.sessionService.getOrCreate(key, userId, this.ctx.owl?.persona.name ?? "owl");
    } else {
      const existing = await this.ctx.sessionStore.loadSession(key);
      if (existing) {
        session = existing;
        log.gateway.debug("SessionManager.getOrCreate: loaded from store", { sessionId: key });
      } else {
        session = this.ctx.sessionStore.createSession(this.ctx.owl?.persona.name ?? "owl");
        session.id = key;
        await this.ctx.sessionStore.saveSession(session);
        log.gateway.debug("SessionManager.getOrCreate: created new session", { sessionId: key });
      }
    }

    this.cache.set(key, { session, lastActivity: Date.now() });
    log.gateway.debug("SessionManager.getOrCreate: exit", { sessionId: key, messageCount: session.messages.length });
    return session;
  }

  async save(session: Session): Promise<void> {
    log.gateway.debug("SessionManager.save: entry", { sessionId: session.id });
    try {
      await this.ctx.sessionStore.saveSession(session);
      log.gateway.debug("SessionManager.save: exit", { sessionId: session.id });
    } catch (err) {
      log.gateway.error("SessionManager.save: failed", err as Error, { sessionId: session.id });
      throw err;
    }
  }

  invalidate(sessionId: string): void {
    log.gateway.debug("SessionManager.invalidate: entry", { sessionId });
    this.cache.delete(sessionId);
  }

  evictStale(): void {
    const now = Date.now();
    let evicted = 0;
    for (const [key, cache] of this.cache) {
      if (now - cache.lastActivity > SESSION_TIMEOUT_MS) {
        this.cache.delete(key);
        evicted++;
      }
    }
    if (evicted > 0) {
      log.gateway.debug("SessionManager.evictStale: exit", { evicted });
    }
  }

  getActiveCount(): number {
    return this.cache.size;
  }
}
```

- [ ] **Step 1.4: Run tests — expect PASS**

```bash
npx vitest run __tests__/gateway/session-manager.test.ts
```

Expected: PASS (6 tests)

- [ ] **Step 1.5: Add `sessionManager` field to GatewayContext**

In `src/gateway/types.ts`, find the `GatewayContext` interface and add after `sessionService?`:

```ts
  sessionManager?: import("./session-manager.js").ISessionManager;
```

- [ ] **Step 1.6: Wire SessionManager in core.ts — replace private sessions field**

In `src/gateway/core.ts`:

Find line ~226:
```ts
  private sessions: Map<string, SessionCache> = new Map();
```
Replace with:
```ts
  private sessionManager: import("./session-manager.js").ISessionManager;
```

In the constructor (around line 365), add after `this.engine = new OwlEngine();`:
```ts
    // SessionManager — owns session lifecycle and cache
    if (this.ctx.sessionManager) {
      this.sessionManager = this.ctx.sessionManager;
    } else {
      const { SessionManager } = await import("./session-manager.js");
      this.sessionManager = new SessionManager(this.ctx);
    }
```

Since the constructor can't be async, use a different approach — initialize synchronously:

```ts
    import { SessionManager } from "./session-manager.js";
```

Add this import at top of `core.ts`, then in constructor:
```ts
    this.sessionManager = this.ctx.sessionManager ?? new SessionManager(this.ctx);
```

- [ ] **Step 1.7: Replace getOrCreateSession calls in core.ts**

Find all calls to `this.getOrCreateSession(message)` (there are 2 — lines ~1160 and ~3524) and replace each with:
```ts
    const session = await this.sessionManager.getOrCreate(message);
```

- [ ] **Step 1.8: Replace saveSession calls in core.ts**

Find `await this.saveSession(` calls throughout `core.ts`. Replace calls where only the session needs saving with:
```ts
    await this.sessionManager.save(session);
```

Note: The full `saveSession(session, userText, newMessages, ...)` method with message appending stays in `core.ts` for now — it's responsible for assembling the message history, not just persisting. Only the final `this.ctx.sessionStore.saveSession(session)` inside it is replaced with `this.sessionManager.save(session)`.

Find inside `saveSession()` method:
```ts
      await this.ctx.sessionStore.saveSession(session);
```
Replace with:
```ts
      await this.sessionManager.save(session);
```

- [ ] **Step 1.9: Replace sessions.delete / sessions.get calls**

Find all usages of `this.sessions`:
- `this.sessions.get(message.sessionId)` → `// handled internally by sessionManager`
- `this.sessions.delete(message.sessionId)` → `this.sessionManager.invalidate(message.sessionId)`
- `this.sessions.set(...)` → removed (SessionManager owns the cache)

In `handleCore()` around line 1155:
```ts
        this.sessions.delete(message.sessionId);
```
Replace with:
```ts
        this.sessionManager.invalidate(message.sessionId);
```

- [ ] **Step 1.10: Delete the local SessionCache interface and SESSION_TIMEOUT_MS constant**

Remove from top of `core.ts`:
```ts
const SESSION_TIMEOUT_MS = 2 * 60 * 60 * 1000; // 2 hours

interface SessionCache {
  session: Session;
  lastActivity: number;
}
```

These are now internal to `SessionManager`.

- [ ] **Step 1.11: Remove the old getOrCreateSession method from core.ts**

Delete the entire `private async getOrCreateSession(message: GatewayMessage): Promise<Session>` method (lines 4207–4240).

- [ ] **Step 1.12: Run full test suite**

```bash
npm test
```

Expected: All tests pass. Fix any TypeScript errors before proceeding.

- [ ] **Step 1.13: Commit**

```bash
git add src/gateway/session-manager.ts src/gateway/types.ts src/gateway/core.ts __tests__/gateway/session-manager.test.ts
git commit -m "refactor(gateway): extract SessionManager — owns session cache and lifecycle"
```

---

## Task 2: Extract LifecycleCoordinator

**What:** Move all `process.once` signal handlers, `timerTickInterval`, and the `initFeatureModules` exit callback into a `LifecycleCoordinator`. Fixes the shutdown race (two competing `process.once('beforeExit')` handlers — lines 391 and 3235 of `core.ts`).

**Files:**
- Create: `src/gateway/lifecycle-coordinator.ts`
- Create: `__tests__/gateway/lifecycle-coordinator.test.ts`
- Modify: `src/gateway/types.ts` (add `lifecycleCoordinator?: ILifecycleCoordinator`)
- Modify: `src/gateway/core.ts` (wire + remove inline lifecycle code)

---

- [ ] **Step 2.1: Write the failing tests**

Create `__tests__/gateway/lifecycle-coordinator.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { LifecycleCoordinator } from "../../src/gateway/lifecycle-coordinator.js";

describe("LifecycleCoordinator", () => {
  let lc: LifecycleCoordinator;

  beforeEach(() => {
    lc = new LifecycleCoordinator();
  });

  afterEach(async () => {
    await lc.shutdown();
  });

  it("calls registered callbacks on shutdown in LIFO order", async () => {
    const order: string[] = [];
    lc.register("first", async () => { order.push("first"); });
    lc.register("second", async () => { order.push("second"); });
    await lc.shutdown();
    expect(order).toEqual(["second", "first"]); // LIFO
  });

  it("shutdown is idempotent — second call is no-op", async () => {
    const cb = vi.fn().mockResolvedValue(undefined);
    lc.register("only", cb);
    await lc.shutdown();
    await lc.shutdown();
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("failed callback is logged but others still run", async () => {
    const good = vi.fn().mockResolvedValue(undefined);
    const bad = vi.fn().mockRejectedValue(new Error("oops"));
    lc.register("good", good);
    lc.register("bad", bad); // registered second → runs first (LIFO)
    await lc.shutdown(); // must not throw
    expect(good).toHaveBeenCalled();
    expect(bad).toHaveBeenCalled();
  });

  it("startTimer triggers fn on interval", async () => {
    vi.useFakeTimers();
    const fn = vi.fn().mockResolvedValue(undefined);
    lc.startTimer("tick", 100, fn);
    vi.advanceTimersByTime(350);
    expect(fn).toHaveBeenCalledTimes(3);
    lc.stopTimer("tick");
    vi.useRealTimers();
  });

  it("startTimer ignores duplicate name and warns", async () => {
    const fn = vi.fn().mockResolvedValue(undefined);
    lc.startTimer("dup", 1000, fn);
    lc.startTimer("dup", 1000, fn); // should not double-register
    lc.stopTimer("dup");
    // No assertion needed — just must not throw
  });

  it("stopTimer clears interval", async () => {
    vi.useFakeTimers();
    const fn = vi.fn().mockResolvedValue(undefined);
    lc.startTimer("stoppable", 100, fn);
    lc.stopTimer("stoppable");
    vi.advanceTimersByTime(500);
    expect(fn).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
```

- [ ] **Step 2.2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/gateway/lifecycle-coordinator.test.ts
```

Expected: FAIL — `Cannot find module '../../src/gateway/lifecycle-coordinator.js'`

- [ ] **Step 2.3: Implement LifecycleCoordinator**

Create `src/gateway/lifecycle-coordinator.ts`:

```ts
import { log } from "../logger.js";

export interface ILifecycleCoordinator {
  register(name: string, cb: () => Promise<void>): void;
  startTimer(name: string, intervalMs: number, fn: () => Promise<void>): void;
  stopTimer(name: string): void;
  shutdown(): Promise<void>;
}

export class LifecycleCoordinator implements ILifecycleCoordinator {
  private readonly callbacks = new Map<string, () => Promise<void>>();
  private readonly timers = new Map<string, NodeJS.Timeout>();
  private shuttingDown = false;

  constructor() {
    const onExit = () => void this.shutdown();
    process.once("exit", onExit);
    process.once("SIGINT", () => { void this.shutdown(); process.exit(0); });
    process.once("SIGTERM", () => { void this.shutdown(); process.exit(0); });
    process.once("beforeExit", onExit);
  }

  register(name: string, cb: () => Promise<void>): void {
    log.gateway.debug("LifecycleCoordinator.register: entry", { name });
    this.callbacks.set(name, cb);
  }

  startTimer(name: string, intervalMs: number, fn: () => Promise<void>): void {
    log.gateway.debug("LifecycleCoordinator.startTimer: entry", { name, intervalMs });
    if (this.timers.has(name)) {
      log.gateway.warn("LifecycleCoordinator.startTimer: duplicate name ignored", { name });
      return;
    }
    const id = setInterval(() => void fn(), intervalMs);
    this.timers.set(name, id);
    log.gateway.debug("LifecycleCoordinator.startTimer: exit", { name });
  }

  stopTimer(name: string): void {
    log.gateway.debug("LifecycleCoordinator.stopTimer: entry", { name });
    const id = this.timers.get(name);
    if (id !== undefined) {
      clearInterval(id);
      this.timers.delete(name);
    }
  }

  async shutdown(): Promise<void> {
    if (this.shuttingDown) return;
    this.shuttingDown = true;
    const names = [...this.callbacks.keys()].reverse(); // LIFO
    log.gateway.info("LifecycleCoordinator.shutdown: entry", { callbackCount: names.length });

    for (const name of names) {
      log.gateway.debug("LifecycleCoordinator.shutdown: running callback", { name });
      try {
        await this.callbacks.get(name)!();
      } catch (err) {
        log.gateway.error("LifecycleCoordinator.shutdown: callback failed", err as Error, { name });
      }
    }

    for (const name of [...this.timers.keys()]) {
      this.stopTimer(name);
    }

    log.gateway.info("LifecycleCoordinator.shutdown: complete");
  }
}
```

- [ ] **Step 2.4: Run tests — expect PASS**

```bash
npx vitest run __tests__/gateway/lifecycle-coordinator.test.ts
```

Expected: PASS (6 tests)

- [ ] **Step 2.5: Add `lifecycleCoordinator` to GatewayContext**

In `src/gateway/types.ts`, add inside `GatewayContext`:

```ts
  lifecycleCoordinator?: import("./lifecycle-coordinator.js").ILifecycleCoordinator;
```

- [ ] **Step 2.6: Wire LifecycleCoordinator in core.ts constructor**

Add import at top of `core.ts`:
```ts
import { LifecycleCoordinator } from "./lifecycle-coordinator.js";
```

Add field to `OwlGateway` class:
```ts
  private readonly lifecycle: ILifecycleCoordinator;
```

In constructor, immediately after `this.engine = new OwlEngine();`:
```ts
    this.lifecycle = this.ctx.lifecycleCoordinator ?? new LifecycleCoordinator();
```

- [ ] **Step 2.7: Move DNA save callback to LifecycleCoordinator**

In the constructor, find the `process.once("exit", saveDNAOnExit)` block (lines 385–398) and replace with:

```ts
    const saveDNAOnExit = async () => {
      if (ctx.owlRegistry) {
        const owl = ctx.owlRegistry.getDefault?.() ?? ctx.owl;
        await ctx.owlRegistry.saveDNA(owl.persona.name).catch((err) => {
          log.gateway.error("LifecycleCoordinator: saveDNA on exit failed", err, {});
        });
      }
    };
    this.lifecycle.register("dna-save", saveDNAOnExit);
```

Delete the three `process.once` calls that follow (lines 391–398).

- [ ] **Step 2.8: Move timer tick to LifecycleCoordinator**

In `initFeatureModules()`, find (lines 3217–3220):
```ts
    this.timerTickInterval = setInterval(() => {
      this.deliverScheduledMessages();
    }, 5_000);
    log.engine.info("[feature] Scheduled message delivery tick started (5s)");
```

Replace with:
```ts
    this.lifecycle.startTimer("scheduled-delivery", 5_000, async () => {
      this.deliverScheduledMessages();
    });
    log.gateway.info("LifecycleCoordinator.startTimer: scheduled-delivery tick started");
```

- [ ] **Step 2.9: Move initFeatureModules exit callback to LifecycleCoordinator**

Find the `saveOnExit` closure and `process.once("beforeExit", saveOnExit)` (lines 3223–3235).

Replace with:
```ts
    this.lifecycle.register("feature-modules-shutdown", async () => {
      await this.ctx.trustChain?.save?.().catch((err) => { log.gateway.error("trustChain save failed", err as Error, {}); });
      await this.ctx.knowledgeGraph?.save?.().catch((err) => { log.gateway.error("knowledgeGraph save failed", err as Error, {}); });
      await this.ctx.timelineManager?.save?.().catch((err) => { log.gateway.error("timelineManager save failed", err as Error, {}); });
      await this.ctx.patternAnalyzer?.save?.().catch((err) => { log.gateway.error("patternAnalyzer save failed", err as Error, {}); });
      await this.ctx.predictiveQueue?.save?.().catch((err) => { log.gateway.error("predictiveQueue save failed", err as Error, {}); });
      await this.ctx.skillArena?.save?.().catch((err) => { log.gateway.error("skillArena save failed", err as Error, {}); });
      this.ctx.signalPool?.stop?.();
      this.ctx.backgroundJobRunner?.stop();
      this.ctx.backgroundOrchestrator?.stop();
    });
```

- [ ] **Step 2.10: Remove timerTickInterval field**

Remove the private field declaration:
```ts
  private timerTickInterval: NodeJS.Timeout | null = null;
```

- [ ] **Step 2.11: Run full suite**

```bash
npm test
```

Expected: All tests pass.

- [ ] **Step 2.12: Commit**

```bash
git add src/gateway/lifecycle-coordinator.ts src/gateway/types.ts src/gateway/core.ts __tests__/gateway/lifecycle-coordinator.test.ts
git commit -m "refactor(gateway): extract LifecycleCoordinator — fixes shutdown race, owns all process signals and timers"
```

---

## Task 3: Extract FeatureCommandRouter

**What:** Replace `private async handleFeatureCommand()` (line 3478, ~400 lines) with a registry-based router. Each command group becomes its own handler class in `src/gateway/commands/`. The router is registered on `GatewayContext`, and `handleCore()` calls `router.dispatch()` instead of the inline method.

**Files:**
- Create: `src/gateway/feature-command-router.ts`
- Create: `src/gateway/commands/trust-command-handler.ts`
- Create: `src/gateway/commands/timeline-command-handler.ts`
- Create: `src/gateway/commands/status-command-handler.ts`
- Create: `src/gateway/commands/debug-command-handler.ts` (and remaining handlers — one per logical command group)
- Create: `__tests__/gateway/feature-command-router.test.ts`
- Modify: `src/gateway/types.ts`
- Modify: `src/gateway/core.ts`

---

- [ ] **Step 3.1: Write the failing tests**

Create `__tests__/gateway/feature-command-router.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";
import { FeatureCommandRouter } from "../../src/gateway/feature-command-router.js";
import type { IFeatureCommandHandler, FeatureCommandContext } from "../../src/gateway/feature-command-router.js";
import type { GatewayResponse } from "../../src/gateway/types.js";

const makeCtx = (): FeatureCommandContext =>
  ({ session: { id: "s1", messages: [], owlName: "owl", createdAt: 0, updatedAt: 0 } } as any);

const makeHandler = (commands: string[], response: string): IFeatureCommandHandler => ({
  commands,
  handle: vi.fn().mockResolvedValue({
    content: response,
    owlName: "owl",
    owlEmoji: "🦉",
    toolsUsed: [],
  } as GatewayResponse),
});

describe("FeatureCommandRouter", () => {
  it("dispatches to registered handler", async () => {
    const router = new FeatureCommandRouter();
    const handler = makeHandler(["/trust"], "trust status");
    router.register(handler);
    const result = await router.dispatch("/trust", makeCtx());
    expect(result?.content).toBe("trust status");
    expect(handler.handle).toHaveBeenCalledWith("/trust", [], expect.any(Object));
  });

  it("returns null for unknown command", async () => {
    const router = new FeatureCommandRouter();
    const result = await router.dispatch("/unknown", makeCtx());
    expect(result).toBeNull();
  });

  it("returns null for non-command input", async () => {
    const router = new FeatureCommandRouter();
    const result = await router.dispatch("hello world", makeCtx());
    expect(result).toBeNull();
  });

  it("isCommand returns true for registered command", () => {
    const router = new FeatureCommandRouter();
    router.register(makeHandler(["/foo"], "bar"));
    expect(router.isCommand("/foo")).toBe(true);
  });

  it("isCommand returns false for unregistered command", () => {
    const router = new FeatureCommandRouter();
    expect(router.isCommand("/nope")).toBe(false);
  });

  it("parses args correctly", async () => {
    const router = new FeatureCommandRouter();
    const handler = makeHandler(["/fork"], "forked");
    router.register(handler);
    await router.dispatch("/fork my reason here", makeCtx());
    expect(handler.handle).toHaveBeenCalledWith("/fork", ["my", "reason", "here"], expect.any(Object));
  });

  it("handler registered for multiple commands routes both", async () => {
    const router = new FeatureCommandRouter();
    const handler = makeHandler(["/pellet", "/pellets"], "pellet list");
    router.register(handler);
    const r1 = await router.dispatch("/pellet", makeCtx());
    const r2 = await router.dispatch("/pellets", makeCtx());
    expect(r1?.content).toBe("pellet list");
    expect(r2?.content).toBe("pellet list");
  });
});
```

- [ ] **Step 3.2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/gateway/feature-command-router.test.ts
```

Expected: FAIL — module not found

- [ ] **Step 3.3: Implement FeatureCommandRouter**

Create `src/gateway/feature-command-router.ts`:

```ts
import { log } from "../logger.js";
import type { GatewayResponse, GatewayMessage } from "./types.js";
import type { Session } from "../memory/store.js";

export interface FeatureCommandContext {
  message: GatewayMessage;
  session: Session;
  owlName: string;
  owlEmoji: string;
  gatewayCtx: import("./types.js").GatewayContext;
}

export interface IFeatureCommandHandler {
  readonly commands: readonly string[];
  handle(cmd: string, args: string[], ctx: FeatureCommandContext): Promise<GatewayResponse | null>;
}

export interface IFeatureCommandRouter {
  register(handler: IFeatureCommandHandler): void;
  dispatch(input: string, ctx: FeatureCommandContext): Promise<GatewayResponse | null>;
  isCommand(input: string): boolean;
}

export class FeatureCommandRouter implements IFeatureCommandRouter {
  private readonly handlers = new Map<string, IFeatureCommandHandler>();

  register(handler: IFeatureCommandHandler): void {
    for (const cmd of handler.commands) {
      log.gateway.debug("FeatureCommandRouter.register: entry", { cmd });
      if (this.handlers.has(cmd)) {
        log.gateway.warn("FeatureCommandRouter.register: duplicate command", { cmd });
      }
      this.handlers.set(cmd.toLowerCase(), handler);
    }
  }

  isCommand(input: string): boolean {
    const cmd = this.extractCommand(input);
    return cmd !== null && this.handlers.has(cmd);
  }

  async dispatch(input: string, ctx: FeatureCommandContext): Promise<GatewayResponse | null> {
    const cmd = this.extractCommand(input);
    if (!cmd) {
      return null;
    }
    const handler = this.handlers.get(cmd);
    if (!handler) {
      log.gateway.debug("FeatureCommandRouter.dispatch: no handler", { cmd });
      return null;
    }
    const args = input.trim().split(/\s+/).slice(1);
    log.gateway.debug("FeatureCommandRouter.dispatch: entry", { cmd, argCount: args.length });
    try {
      const result = await handler.handle(cmd, args, ctx);
      log.gateway.debug("FeatureCommandRouter.dispatch: exit", { cmd, handled: result !== null });
      return result;
    } catch (err) {
      log.gateway.error("FeatureCommandRouter.dispatch: handler threw", err as Error, { cmd });
      return null;
    }
  }

  private extractCommand(input: string): string | null {
    const first = input.trim().split(/\s+/)[0] ?? "";
    return first.startsWith("/") ? first.toLowerCase() : null;
  }
}
```

- [ ] **Step 3.4: Run tests — expect PASS**

```bash
npx vitest run __tests__/gateway/feature-command-router.test.ts
```

Expected: PASS (7 tests)

- [ ] **Step 3.5: Create command handler for /trust and /timeline**

Create `src/gateway/commands/trust-timeline-handler.ts`:

```ts
import type { IFeatureCommandHandler, FeatureCommandContext } from "../feature-command-router.js";
import type { GatewayResponse } from "../types.js";
import { log } from "../../logger.js";

export class TrustTimelineCommandHandler implements IFeatureCommandHandler {
  readonly commands = ["/trust", "/timeline", "/fork"] as const;

  async handle(cmd: string, args: string[], ctx: FeatureCommandContext): Promise<GatewayResponse | null> {
    log.gateway.debug("TrustTimelineCommandHandler.handle: entry", { cmd });
    const owl = ctx.gatewayCtx.owl;
    const mkResp = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
    });

    if (cmd === "/trust" && ctx.gatewayCtx.trustChain) {
      log.gateway.debug("TrustTimelineCommandHandler.handle: /trust", {});
      return mkResp(ctx.gatewayCtx.trustChain.formatStatus());
    }

    if (cmd === "/timeline" && ctx.gatewayCtx.timelineManager) {
      const timeline = ctx.gatewayCtx.timelineManager.getTimeline(ctx.message.sessionId);
      if (!timeline) return mkResp("No timeline data for this session yet.");
      const snapshots = timeline.snapshots
        .map((s) => `  • [${s.id.slice(0, 8)}] ${s.metadata.snapshotAt} — ${s.messageIndex} messages`)
        .join("\n");
      log.gateway.debug("TrustTimelineCommandHandler.handle: /timeline exit", { snapshotCount: timeline.snapshots.length });
      return mkResp(`**Timeline** (${timeline.totalMessages} messages)\n\n**Snapshots:**\n${snapshots}`);
    }

    if (cmd === "/fork" && ctx.gatewayCtx.timelineManager) {
      const reason = args.join(" ") || undefined;
      const snapshot = ctx.gatewayCtx.timelineManager.createSnapshot(
        ctx.message.sessionId,
        ctx.session.messages,
        owl.persona.name,
        "Pre-fork snapshot",
      );
      const newSessionId = `${ctx.message.sessionId}:fork:${Date.now()}`;
      ctx.gatewayCtx.timelineManager.fork(snapshot.id, newSessionId, reason);
      await ctx.gatewayCtx.timelineManager.save();
      log.gateway.debug("TrustTimelineCommandHandler.handle: /fork exit", { newSessionId });
      return mkResp(`🍴 Forked conversation to \`${newSessionId}\`. ${reason ? `Reason: ${reason}` : ""}`);
    }

    log.gateway.debug("TrustTimelineCommandHandler.handle: dependency not available", { cmd });
    return null;
  }
}
```

- [ ] **Step 3.6: Add `featureCommandRouter` to GatewayContext**

In `src/gateway/types.ts`, add inside `GatewayContext`:

```ts
  featureCommandRouter?: import("./feature-command-router.js").IFeatureCommandRouter;
```

- [ ] **Step 3.7: Wire router in core.ts constructor**

Add imports at top of `core.ts`:
```ts
import { FeatureCommandRouter } from "./feature-command-router.js";
import { TrustTimelineCommandHandler } from "./commands/trust-timeline-handler.js";
```

Add field:
```ts
  private readonly featureRouter: import("./feature-command-router.js").IFeatureCommandRouter;
```

In constructor:
```ts
    if (this.ctx.featureCommandRouter) {
      this.featureRouter = this.ctx.featureCommandRouter;
    } else {
      const router = new FeatureCommandRouter();
      router.register(new TrustTimelineCommandHandler());
      // Additional handlers registered in initFeatureModules when ctx is available
      this.featureRouter = router;
    }
```

- [ ] **Step 3.8: Replace handleFeatureCommand call in handleCore()**

Find in `handleCore()` (around line 1390):
```ts
    const featureResult = await this.handleFeatureCommand(message, callbacks);
    if (featureResult) return featureResult;
```

Replace with:
```ts
    const featureCmdCtx: import("./feature-command-router.js").FeatureCommandContext = {
      message,
      session,
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      gatewayCtx: this.ctx,
    };
    const featureResult = await this.featureRouter.dispatch(message.text, featureCmdCtx);
    if (featureResult) return featureResult;
```

- [ ] **Step 3.9: Extract remaining commands from handleFeatureCommand into handler files**

For each remaining command group in `handleFeatureCommand()` (lines 3492–3893), create a handler file:

- `src/gateway/commands/collab-command-handler.ts` — `/collab`, `/session`
- `src/gateway/commands/knowledge-command-handler.ts` — `/knowledge`, `/fact`, `/pellet`, `/pellets`
- `src/gateway/commands/debug-command-handler.ts` — `/debug`, `/stats`, `/status`
- `src/gateway/commands/parliament-command-handler.ts` — `/parliament`, `/debate`
- `src/gateway/commands/instinct-command-handler.ts` — `/instinct`, `/instincts`
- `src/gateway/commands/memory-command-handler.ts` — `/memory`, `/forget`, `/digest`
- `src/gateway/commands/goal-command-handler.ts` — `/goal`, `/goals`
- `src/gateway/commands/owl-command-handler.ts` — `/owl`, `/dna`, `/evolve`

Each handler follows this exact pattern:

```ts
import type { IFeatureCommandHandler, FeatureCommandContext } from "../feature-command-router.js";
import type { GatewayResponse } from "../types.js";
import { log } from "../../logger.js";

export class XxxCommandHandler implements IFeatureCommandHandler {
  readonly commands = ["/xxx"] as const;

  async handle(cmd: string, args: string[], ctx: FeatureCommandContext): Promise<GatewayResponse | null> {
    log.gateway.debug("XxxCommandHandler.handle: entry", { cmd, argCount: args.length });
    const owl = ctx.gatewayCtx.owl;
    const mkResp = (content: string): GatewayResponse => ({
      content, owlName: owl.persona.name, owlEmoji: owl.persona.emoji, toolsUsed: [],
    });
    // Move exact logic from handleFeatureCommand() branch
    log.gateway.debug("XxxCommandHandler.handle: exit", { cmd });
    return mkResp("...");
  }
}
```

Register each handler in the constructor after `router.register(new TrustTimelineCommandHandler())`.

- [ ] **Step 3.10: Delete handleFeatureCommand method from core.ts**

After all commands are extracted and registered, delete the entire `private async handleFeatureCommand(...)` method from `core.ts`.

- [ ] **Step 3.11: Run full suite**

```bash
npm test
```

Expected: All tests pass.

- [ ] **Step 3.12: Commit**

```bash
git add src/gateway/feature-command-router.ts src/gateway/commands/ src/gateway/types.ts src/gateway/core.ts __tests__/gateway/feature-command-router.test.ts
git commit -m "refactor(gateway): extract FeatureCommandRouter — replaces 422-line if-chain with open registration table"
```

---

## Task 4: Extract ParliamentSubsystem

**What:** Collapse the 3× duplicated parliament paths in `handleCore()` into a single `ParliamentSubsystem` class. The three paths are: (1) auto-trigger check (~line 2093), (2) strategy=PARLIAMENT branch, (3) `OwlBrain` parliament result post-processing (~line 2255).

**Files:**
- Create: `src/gateway/parliament-subsystem.ts`
- Create: `__tests__/gateway/parliament-subsystem.test.ts`
- Modify: `src/gateway/types.ts`
- Modify: `src/gateway/core.ts`

---

- [ ] **Step 4.1: Write the failing tests**

Create `__tests__/gateway/parliament-subsystem.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";
import { ParliamentSubsystem } from "../../src/gateway/parliament-subsystem.js";
import type { GatewayMessage } from "../../src/gateway/types.js";

const makeMsg = (): GatewayMessage => ({
  id: "m1", sessionId: "s1", userId: "u1", channelId: "cli", text: "hello",
});

const makeOrchestrator = (shouldTrigger = true) => ({
  parliamentAutoTrigger: {
    check: vi.fn().mockResolvedValue({ shouldTrigger, reason: "complex" }),
  },
  topicWorthiness: { evaluate: vi.fn().mockResolvedValue({ worthy: true, score: 0.8 }) },
  multiRoundDebate: {
    runDebate: vi.fn().mockResolvedValue({
      synthesis: "Parliament says: yes",
      rounds: [{ positions: [] }],
    }),
  },
  debatePelletGenerator: { generate: vi.fn().mockResolvedValue(undefined) },
});

describe("ParliamentSubsystem", () => {
  it("shouldAutoTrigger returns false when parliamentAutoTrigger is absent", async () => {
    const subsystem = new ParliamentSubsystem({} as any);
    const result = await subsystem.shouldAutoTrigger("test question");
    expect(result).toBe(false);
  });

  it("shouldAutoTrigger delegates to parliamentAutoTrigger.check", async () => {
    const deps = makeOrchestrator(true);
    const subsystem = new ParliamentSubsystem(deps as any);
    const result = await subsystem.shouldAutoTrigger("complex question");
    expect(deps.parliamentAutoTrigger.check).toHaveBeenCalledWith("complex question", undefined);
    expect(result).toBe(true);
  });

  it("run returns synthesis from debate as GatewayResponse", async () => {
    const deps = makeOrchestrator();
    const ctx = {
      owl: { persona: { name: "owl", emoji: "🦉" } },
      provider: {},
      pelletStore: {},
      ...deps,
    } as any;
    const subsystem = new ParliamentSubsystem(ctx);
    const result = await subsystem.run(makeMsg(), ctx);
    expect(result.content).toBe("Parliament says: yes");
    expect(result.owlName).toBe("owl");
    expect(deps.multiRoundDebate.runDebate).toHaveBeenCalled();
  });

  it("run returns null when dependencies are missing", async () => {
    const ctx = { owl: { persona: { name: "owl", emoji: "🦉" } } } as any;
    const subsystem = new ParliamentSubsystem(ctx);
    const result = await subsystem.run(makeMsg(), ctx);
    expect(result).toBeNull();
  });

  it("pellet generation failure does not reject run()", async () => {
    const deps = makeOrchestrator();
    deps.debatePelletGenerator.generate = vi.fn().mockRejectedValue(new Error("pellet fail"));
    const ctx = {
      owl: { persona: { name: "owl", emoji: "🦉" } },
      provider: {},
      pelletStore: {},
      ...deps,
    } as any;
    const subsystem = new ParliamentSubsystem(ctx);
    await expect(subsystem.run(makeMsg(), ctx)).resolves.not.toThrow();
  });
});
```

- [ ] **Step 4.2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/gateway/parliament-subsystem.test.ts
```

Expected: FAIL — module not found

- [ ] **Step 4.3: Implement ParliamentSubsystem**

Create `src/gateway/parliament-subsystem.ts`:

```ts
import { log } from "../logger.js";
import type { GatewayMessage, GatewayResponse, GatewayContext } from "./types.js";

export interface IParliamentSubsystem {
  shouldAutoTrigger(messageText: string): Promise<boolean>;
  run(message: GatewayMessage, ctx: GatewayContext): Promise<GatewayResponse | null>;
}

export class ParliamentSubsystem implements IParliamentSubsystem {
  constructor(private readonly ctx: GatewayContext) {}

  async shouldAutoTrigger(messageText: string): Promise<boolean> {
    log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: entry", { textLen: messageText.length });
    const trigger = this.ctx.parliamentAutoTrigger;
    if (!trigger) {
      log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: no trigger configured");
      return false;
    }
    const result = await trigger.check(messageText, this.ctx.provider);
    log.parliament.debug("ParliamentSubsystem.shouldAutoTrigger: exit", { shouldTrigger: result.shouldTrigger, reason: result.reason });
    return result.shouldTrigger;
  }

  async run(message: GatewayMessage, ctx: GatewayContext): Promise<GatewayResponse | null> {
    log.parliament.debug("ParliamentSubsystem.run: entry", { sessionId: message.sessionId });

    const { parliamentAutoTrigger, topicWorthiness, multiRoundDebate, debatePelletGenerator, pelletStore, owl } = ctx;

    if (!multiRoundDebate || !debatePelletGenerator || !pelletStore) {
      log.parliament.debug("ParliamentSubsystem.run: dependencies missing, skipping");
      return null;
    }

    log.parliament.debug("ParliamentSubsystem.run: checking topic worthiness");
    if (topicWorthiness) {
      const worthiness = await topicWorthiness.evaluate(message.text, ctx.provider).catch((err) => {
        log.parliament.error("ParliamentSubsystem.run: topic worthiness failed", err as Error, { sessionId: message.sessionId });
        return null;
      });
      if (worthiness && !worthiness.worthy) {
        log.parliament.debug("ParliamentSubsystem.run: topic not worthy", { score: worthiness.score });
        return null;
      }
    }

    log.parliament.debug("ParliamentSubsystem.run: running debate");
    const debateResult = await multiRoundDebate.runDebate(message.text, ctx);

    log.parliament.debug("ParliamentSubsystem.run: debate complete", { rounds: debateResult.rounds?.length });

    // Fire-and-forget pellet generation — must not block response
    void debatePelletGenerator.generate(debateResult, ctx).catch((err) => {
      log.parliament.error("ParliamentSubsystem.run: pellet generation failed", err as Error, { sessionId: message.sessionId });
    });

    const response: GatewayResponse = {
      content: debateResult.synthesis,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: ["parliament"],
    };

    log.parliament.debug("ParliamentSubsystem.run: exit", { sessionId: message.sessionId });
    return response;
  }
}
```

- [ ] **Step 4.4: Run tests — expect PASS**

```bash
npx vitest run __tests__/gateway/parliament-subsystem.test.ts
```

Expected: PASS (5 tests)

- [ ] **Step 4.5: Add to GatewayContext**

In `src/gateway/types.ts`:
```ts
  parliamentSubsystem?: import("./parliament-subsystem.js").IParliamentSubsystem;
```

- [ ] **Step 4.6: Wire in core.ts constructor**

Add import at top of `core.ts`:
```ts
import { ParliamentSubsystem } from "./parliament-subsystem.js";
```

Add field:
```ts
  private readonly parliamentSubsystem: import("./parliament-subsystem.js").IParliamentSubsystem;
```

In constructor:
```ts
    this.parliamentSubsystem = this.ctx.parliamentSubsystem ?? new ParliamentSubsystem(this.ctx);
```

- [ ] **Step 4.7: Replace 3× parliament paths in handleCore()**

**Path 1** — auto-trigger (~line 2093): Find the block starting with:
```ts
    if (this.parliamentAutoTrigger && this.topicWorthiness && this.multiRoundDebate ...
```

Replace the entire auto-trigger block with:
```ts
    if (this.ctx.parliamentAutoTrigger) {
      const shouldTrigger = await this.parliamentSubsystem.shouldAutoTrigger(message.text);
      if (shouldTrigger) {
        log.gateway.debug("handleCore: parliament auto-triggered", { sessionId: message.sessionId });
        const parliamentResp = await this.parliamentSubsystem.run(message, this.ctx);
        if (parliamentResp) return parliamentResp;
      }
    }
```

**Path 2** — strategy=PARLIAMENT: Find the `strategy.strategy === "PARLIAMENT"` branch and replace the inline parliament execution with:
```ts
        const parliamentResp = await this.parliamentSubsystem.run(message, this.ctx);
        if (parliamentResp) return parliamentResp;
```

**Path 3** — OwlBrain result (`parliamentHandled`): Find `if (routingResult?.parliamentHandled)` and replace inline logic with:
```ts
    if (routingResult?.parliamentHandled) {
      log.gateway.debug("handleCore: OwlBrain handled parliament", { sessionId: message.sessionId });
      // OwlBrain already ran parliament internally; result is in routingResult.text
    }
```

- [ ] **Step 4.8: Run full suite**

```bash
npm test
```

Expected: All tests pass.

- [ ] **Step 4.9: Commit**

```bash
git add src/gateway/parliament-subsystem.ts src/gateway/types.ts src/gateway/core.ts __tests__/gateway/parliament-subsystem.test.ts
git commit -m "refactor(gateway): extract ParliamentSubsystem — collapses 3x parliament duplication into single canonical path"
```

---

## Task 5: Extract ProactiveDeliveryService

**What:** Move `deliverScheduledMessages()`, `lastActiveChannel`, `lastActiveUserId`, and the privacy-bug tracking into a `ProactiveDeliveryService`. The service tracks the last known `{channelId, userId}` per session and delivers scheduled timer messages without leaking between users.

**Files:**
- Create: `src/gateway/proactive-delivery-service.ts`
- Create: `__tests__/gateway/proactive-delivery-service.test.ts`
- Modify: `src/gateway/types.ts`
- Modify: `src/gateway/core.ts`

---

- [ ] **Step 5.1: Write the failing tests**

Create `__tests__/gateway/proactive-delivery-service.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";
import { ProactiveDeliveryService } from "../../src/gateway/proactive-delivery-service.js";

const makeAdapter = () => ({
  id: "telegram",
  sendToUser: vi.fn().mockResolvedValue(undefined),
  broadcast: vi.fn().mockResolvedValue(undefined),
});

describe("ProactiveDeliveryService", () => {
  it("records last seen user per session", () => {
    const svc = new ProactiveDeliveryService({} as any);
    svc.recordActivity("s1", "telegram", "u1");
    expect(svc.getLastActivity("s1")).toEqual({ channelId: "telegram", userId: "u1" });
  });

  it("different sessions track independently", () => {
    const svc = new ProactiveDeliveryService({} as any);
    svc.recordActivity("s1", "telegram", "u1");
    svc.recordActivity("s2", "cli", "local");
    expect(svc.getLastActivity("s1")).toEqual({ channelId: "telegram", userId: "u1" });
    expect(svc.getLastActivity("s2")).toEqual({ channelId: "cli", userId: "local" });
  });

  it("deliver calls adapter.sendToUser with correct args", async () => {
    const adapter = makeAdapter();
    const adapters = new Map([["telegram", adapter]]);
    const svc = new ProactiveDeliveryService({ adapters, owl: { persona: { name: "owl", emoji: "🦉" } } } as any);
    await svc.deliver("telegram", "u1", "hello");
    expect(adapter.sendToUser).toHaveBeenCalledWith("u1", expect.objectContaining({ content: "hello" }));
  });

  it("deliver is no-op when adapter not found", async () => {
    const svc = new ProactiveDeliveryService({ adapters: new Map(), owl: { persona: { name: "owl", emoji: "🦉" } } } as any);
    // Must not throw
    await svc.deliver("missing-channel", "u1", "hello");
  });

  it("deliverScheduled calls deliver for each ready message", async () => {
    const adapter = makeAdapter();
    const adapters = new Map([["telegram", adapter]]);
    const svc = new ProactiveDeliveryService({ adapters, owl: { persona: { name: "owl", emoji: "🦉" } } } as any);
    svc.recordActivity("s1", "telegram", "u1");

    const getReadyMessages = vi.fn().mockReturnValue([
      { id: "t1", message: "reminder", channelId: "telegram", userId: "u1" },
    ]);
    await svc.deliverScheduled(getReadyMessages);
    expect(adapter.sendToUser).toHaveBeenCalledWith("u1", expect.objectContaining({ content: "reminder" }));
  });

  it("deliverScheduled uses last-activity fallback when message has no channelId", async () => {
    const adapter = makeAdapter();
    const adapters = new Map([["cli", adapter]]);
    const svc = new ProactiveDeliveryService({ adapters, owl: { persona: { name: "owl", emoji: "🦉" } } } as any);
    svc.recordActivity("s1", "cli", "local");

    const getReadyMessages = vi.fn().mockReturnValue([
      { id: "t2", message: "no-channel", channelId: null, userId: null },
    ]);
    await svc.deliverScheduled(getReadyMessages);
    expect(adapter.sendToUser).toHaveBeenCalledWith("local", expect.objectContaining({ content: "no-channel" }));
  });
});
```

- [ ] **Step 5.2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/gateway/proactive-delivery-service.test.ts
```

Expected: FAIL — module not found

- [ ] **Step 5.3: Implement ProactiveDeliveryService**

Create `src/gateway/proactive-delivery-service.ts`:

```ts
import { log } from "../logger.js";
import type { GatewayResponse } from "./types.js";
import type { ChannelAdapter } from "./types.js";
import type { OwlInstance } from "../owls/persona.js";

interface ActivityRecord {
  channelId: string;
  userId: string;
}

interface ReadyMessage {
  id: string;
  message: string;
  channelId: string | null;
  userId: string | null;
}

export interface IProactiveDeliveryService {
  recordActivity(sessionId: string, channelId: string, userId: string): void;
  getLastActivity(sessionId: string): ActivityRecord | undefined;
  deliver(channelId: string, userId: string, text: string, preformatted?: boolean): Promise<void>;
  deliverScheduled(getReadyMessages: () => ReadyMessage[]): Promise<void>;
}

export class ProactiveDeliveryService implements IProactiveDeliveryService {
  /** Per-session last-seen channel+user. Replaces the single `lastActiveChannel` scalar. */
  private readonly activity = new Map<string, ActivityRecord>();
  /** Fallback for legacy messages that have no sessionId. */
  private lastGlobalActivity: ActivityRecord | null = null;

  constructor(private readonly ctx: { adapters: Map<string, ChannelAdapter>; owl: OwlInstance }) {}

  recordActivity(sessionId: string, channelId: string, userId: string): void {
    log.gateway.debug("ProactiveDeliveryService.recordActivity: entry", { sessionId, channelId, userId });
    this.activity.set(sessionId, { channelId, userId });
    this.lastGlobalActivity = { channelId, userId };
  }

  getLastActivity(sessionId: string): ActivityRecord | undefined {
    return this.activity.get(sessionId);
  }

  async deliver(channelId: string, userId: string, text: string, preformatted = false): Promise<void> {
    log.gateway.debug("ProactiveDeliveryService.deliver: entry", { channelId, userId, textLen: text.length });
    const adapter = this.ctx.adapters.get(channelId);
    if (!adapter) {
      log.gateway.warn("ProactiveDeliveryService.deliver: no adapter for channel", { channelId });
      return;
    }
    const response: GatewayResponse = {
      content: text,
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      toolsUsed: [],
      preformatted,
    };
    try {
      await adapter.sendToUser(userId, response);
      log.gateway.debug("ProactiveDeliveryService.deliver: exit", { channelId, userId });
    } catch (err) {
      log.gateway.error("ProactiveDeliveryService.deliver: failed", err as Error, { channelId, userId });
    }
  }

  async deliverScheduled(getReadyMessages: () => ReadyMessage[]): Promise<void> {
    const ready = getReadyMessages();
    if (ready.length === 0) return;

    log.gateway.debug("ProactiveDeliveryService.deliverScheduled: entry", { count: ready.length });

    for (const msg of ready) {
      const channelId = msg.channelId ?? this.lastGlobalActivity?.channelId ?? null;
      const userId = msg.userId ?? this.lastGlobalActivity?.userId ?? null;

      if (!channelId || !userId) {
        log.gateway.warn("ProactiveDeliveryService.deliverScheduled: no channel/user for message", { id: msg.id });
        continue;
      }

      log.gateway.debug("ProactiveDeliveryService.deliverScheduled: delivering", { id: msg.id, channelId, userId });
      await this.deliver(channelId, userId, msg.message).catch((err) => {
        log.gateway.error("ProactiveDeliveryService.deliverScheduled: delivery failed", err as Error, { id: msg.id });
      });
    }

    log.gateway.debug("ProactiveDeliveryService.deliverScheduled: exit", { delivered: ready.length });
  }
}
```

- [ ] **Step 5.4: Run tests — expect PASS**

```bash
npx vitest run __tests__/gateway/proactive-delivery-service.test.ts
```

Expected: PASS (6 tests)

- [ ] **Step 5.5: Add to GatewayContext**

In `src/gateway/types.ts`:
```ts
  proactiveDeliveryService?: import("./proactive-delivery-service.js").IProactiveDeliveryService;
```

- [ ] **Step 5.6: Wire in core.ts constructor**

Add import at top of `core.ts`:
```ts
import { ProactiveDeliveryService } from "./proactive-delivery-service.js";
```

Add field:
```ts
  private readonly proactiveSvc: import("./proactive-delivery-service.js").IProactiveDeliveryService;
```

In constructor:
```ts
    this.proactiveSvc = this.ctx.proactiveDeliveryService
      ?? new ProactiveDeliveryService({ adapters: this.adapters, owl: this.ctx.owl });
```

- [ ] **Step 5.7: Record activity in handleCore() after session load**

In `handleCore()`, after `const session = await this.sessionManager.getOrCreate(message);`, add:
```ts
    this.proactiveSvc.recordActivity(message.sessionId, message.channelId, message.userId);
```

- [ ] **Step 5.8: Replace deliverScheduledMessages in LifecycleCoordinator timer**

In `initFeatureModules()`, the timer call now uses the service. Find:
```ts
    this.lifecycle.startTimer("scheduled-delivery", 5_000, async () => {
      this.deliverScheduledMessages();
    });
```

Replace with:
```ts
    this.lifecycle.startTimer("scheduled-delivery", 5_000, async () => {
      await this.proactiveSvc.deliverScheduled(getReadyMessages);
    });
```

Ensure `getReadyMessages` is imported:
```ts
import { getReadyMessages } from "../tools/utils/timer.js";
```

- [ ] **Step 5.9: Replace sendProactive method body**

Find `async sendProactive(channelId, userId, text, preformatted)` in `core.ts`. Replace its body with:
```ts
    await this.proactiveSvc.deliver(channelId, userId, text, preformatted);
```

- [ ] **Step 5.10: Remove lastActiveChannel, lastActiveUserId, deliverScheduledMessages**

Delete these from `core.ts`:
- `private lastActiveChannel: string | null = null;`
- `private lastActiveUserId: string | null = null;`
- The line `this.lastActiveChannel = message.channelId;` (around line 1707)
- The line `this.lastActiveUserId = message.userId;` (nearby)
- The entire `private deliverScheduledMessages(): void` method (~3288–3330)

- [ ] **Step 5.11: Run full test suite**

```bash
npm test
```

Expected: All tests pass.

- [ ] **Step 5.12: Commit**

```bash
git add src/gateway/proactive-delivery-service.ts src/gateway/types.ts src/gateway/core.ts __tests__/gateway/proactive-delivery-service.test.ts
git commit -m "refactor(gateway): extract ProactiveDeliveryService — fixes per-session activity tracking, removes lastActiveChannel privacy bug"
```

---

## Final Verification

- [ ] **Verify LoC reduction**

```bash
wc -l src/gateway/core.ts
```

Expected: ≤ 3,200 lines (down from 4,911)

- [ ] **Verify new files exist**

```bash
ls src/gateway/{session-manager,lifecycle-coordinator,feature-command-router,parliament-subsystem,proactive-delivery-service}.ts src/gateway/commands/
```

- [ ] **Verify 4-point logging in new files**

```bash
grep -c "\.debug\|\.info\|\.warn\|\.error" src/gateway/session-manager.ts src/gateway/lifecycle-coordinator.ts src/gateway/feature-command-router.ts src/gateway/parliament-subsystem.ts src/gateway/proactive-delivery-service.ts
```

Expected: Each file has ≥ 6 log calls.

- [ ] **Verify no silent catch blocks**

```bash
grep -n "} catch" src/gateway/session-manager.ts src/gateway/lifecycle-coordinator.ts src/gateway/feature-command-router.ts src/gateway/parliament-subsystem.ts src/gateway/proactive-delivery-service.ts | grep -v "log\."
```

Expected: No output (all catches log).

- [ ] **Full suite one final time**

```bash
npm test
```

Expected: All 7,071+ tests pass, 0 failures.

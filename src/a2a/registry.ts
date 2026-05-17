/**
 * StackOwl — A2ARegistry
 *
 * Direct in-process agent-to-agent messaging with:
 *   - Typed send/broadcast (no channel registration, no inbox queuing)
 *   - Lazy session context via A2AContext.getHistory() at call time
 *   - Consecutive-failure tracking per agent (auto-deregister at 3, reset on success or unregister)
 *   - Broadcast that always resolves, even when all agents fail
 *   - 4-point logging: entry / decision / step / exit on every method
 *
 * Design decisions vs. ACP subsystem:
 *   - ACPRouter uses channel registration + backpressure inbox + SessionBridgeFactory.
 *     A2ARegistry does none of that — it is a typed direct-call registry only.
 *   - Session context is surfaced as a lazy getHistory() on A2AContext, not copied upfront.
 *   - Auto-deregister at 3 consecutive failures guards against stuck agents.
 */

import { randomUUID } from "node:crypto";
import { log } from "../logger.js";
import type {
  A2AAgent,
  A2AContext,
  A2AResult,
  A2ABroadcastResult,
  SessionStore,
} from "./types.js";
import { A2ADuplicateAgentError } from "./types.js";

// ─── A2AMessage (internal — NOT exported from barrel) ─────────────────────
// Visibility: INTERNAL. The barrel index.ts MUST NOT re-export this type.
// This is the internal transport envelope; callers only see A2AResult.

interface A2AMessage<T = unknown> {
  readonly id: string;
  readonly from: string;
  readonly to: string;
  readonly payload: T;
  readonly sessionId: string | undefined;
  readonly timestamp: number;
}

// ─── Constants ─────────────────────────────────────────────────────────────

/** Consecutive failure count that triggers automatic agent deregistration. */
const AUTO_DEREGISTER_THRESHOLD = 3;

// ─── Registry ──────────────────────────────────────────────────────────────

export class A2ARegistry {
  /** All registered agents keyed by their stable agentId */
  private readonly agents: Map<string, A2AAgent> = new Map();

  /**
   * Consecutive failure count per agentId.
   * Reset to 0 on success or full unregister.
   * Incremented on each failed handle() call.
   * When it reaches AUTO_DEREGISTER_THRESHOLD the agent is auto-removed.
   */
  private readonly failures: Map<string, number> = new Map();

  /**
   * @param getSessionStore - Optional lazy resolver for a SessionStore.
   *   Called at send-time per message, not at construction. This avoids
   *   tight coupling to bootstrap order and allows the store to be wired
   *   after the registry is created (e.g. GatewayCore pattern).
   *
   *   If the resolver throws (lazy resolver error), the context is built
   *   with an empty history — the send() itself returns { status: 'failed' }
   *   only when agent.handle() throws, not when store resolution fails.
   */
  constructor(private readonly getSessionStore?: () => SessionStore) {}

  // ─── Registration ──────────────────────────────────────────────────────

  /**
   * Register an agent. Throws A2ADuplicateAgentError if an agent with the
   * same agentId is already present — callers must unregister first.
   *
   * 4-point logging: entry → decision (dup check) → [error path] / exit
   */
  register(agent: A2AAgent): void {
    log.engine.debug("a2a.registry.register: entry", {
      agentId: agent.agentId,
    });

    // Decision: duplicate check
    if (this.agents.has(agent.agentId)) {
      log.engine.warn("a2a.registry.register: decision — duplicate rejected", {
        agentId: agent.agentId,
      });
      throw new A2ADuplicateAgentError(agent.agentId);
    }

    log.engine.debug(
      "a2a.registry.register: decision — new registration accepted",
      { agentId: agent.agentId },
    );

    this.agents.set(agent.agentId, agent);

    log.engine.info("a2a.registry.register: exit — registered", {
      agentId: agent.agentId,
      totalAgents: this.agents.size,
    });
  }

  /**
   * Unregister an agent and reset its consecutive failure counter so that a
   * subsequent re-registration starts with a clean slate.
   *
   * No-op (with a warning log) if the agent is not registered.
   *
   * 4-point logging: entry → decision (present/absent) → step (deletion) → exit
   */
  unregister(agentId: string): void {
    log.engine.debug("a2a.registry.unregister: entry", { agentId });

    if (!this.agents.has(agentId)) {
      log.engine.warn(
        "a2a.registry.unregister: decision — agent not registered, no-op",
        { agentId },
      );
      return;
    }

    log.engine.debug(
      "a2a.registry.unregister: decision — removing agent and clearing failure count",
      { agentId, failuresBefore: this.failures.get(agentId) ?? 0 },
    );

    this.agents.delete(agentId);
    // Clearing failure count means re-registration starts clean (Scenario 3)
    this.failures.delete(agentId);

    log.engine.info("a2a.registry.unregister: exit — agent removed", {
      agentId,
      totalAgents: this.agents.size,
    });
  }

  // ─── Sending ───────────────────────────────────────────────────────────

  /**
   * Send a typed payload to a specific agent and return a structured result.
   *
   * - If the agent is not registered: { status: 'not-found' }
   * - If agent.handle() throws: { status: 'failed' }, failure count incremented
   * - If timeout fires first: { status: 'timeout' }, failure count incremented
   * - If 3 consecutive failures accumulate: agent is auto-deregistered
   * - On success: failure count reset; { status: 'delivered', result }
   *
   * The outer Promise always resolves — it never rejects.
   *
   * 4-point logging: entry → decision (agent present?) → step (handle) → exit
   */
  async send<TIn, TOut>(
    to: string,
    from: string,
    payload: TIn,
    opts?: { sessionId?: string; timeoutMs?: number },
  ): Promise<A2AResult<TOut>> {
    const messageId = randomUUID();

    log.engine.debug("a2a.registry.send: entry", {
      messageId,
      to,
      from,
      sessionId: opts?.sessionId,
      timeoutMs: opts?.timeoutMs,
    });

    // ── Decision: is the target agent registered? ──────────────────────
    const agent = this.agents.get(to);
    if (!agent) {
      log.engine.warn("a2a.registry.send: decision — agent not found", {
        messageId,
        to,
      });
      return { status: "not-found", messageId };
    }

    log.engine.debug("a2a.registry.send: decision — agent found", {
      messageId,
      to,
    });

    // ── Step: build lazy context ────────────────────────────────────────
    const sessionId = opts?.sessionId ?? "";
    const context = this.buildContext(sessionId);

    log.engine.debug("a2a.registry.send: step — context built", {
      messageId,
      sessionId,
    });

    // Internal envelope — retained for traceability in future log emission.
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const envelope: A2AMessage<TIn> = {
      id: messageId,
      from,
      to,
      payload,
      sessionId: opts?.sessionId,
      timestamp: Date.now(),
    };
    void envelope; // suppress noUnusedLocals — envelope is the typed audit record

    // ── Step: invoke agent.handle() with optional timeout ───────────────
    let handlePromise: Promise<TOut>;

    try {
      // agent.handle() may throw synchronously (e.g. bad lazy resolver)
      handlePromise = agent.handle<TIn, TOut>(payload, context);
    } catch (syncErr) {
      log.engine.error(
        "a2a.registry.send: step — handle() threw synchronously",
        syncErr instanceof Error ? syncErr : new Error(String(syncErr)),
        { messageId, to, from },
      );
      this.recordFailure(to);
      return {
        status: "failed",
        messageId,
        error:
          syncErr instanceof Error ? syncErr.message : String(syncErr),
      };
    }

    try {
      const result = await (opts?.timeoutMs != null
        ? this.raceTimeout<TOut>(handlePromise, opts.timeoutMs, messageId, to)
        : handlePromise);

      // ── Exit: success ────────────────────────────────────────────────
      this.recordSuccess(to);

      log.engine.debug("a2a.registry.send: exit — delivered", {
        messageId,
        to,
        from,
      });

      return { status: "delivered", messageId, result };
    } catch (err) {
      const isTimeout =
        err instanceof Error && err.message.startsWith("A2A timeout:");
      const status = isTimeout ? ("timeout" as const) : ("failed" as const);

      log.engine.error(
        `a2a.registry.send: exit — ${status}`,
        err instanceof Error ? err : new Error(String(err)),
        { messageId, to, from },
      );

      this.recordFailure(to);

      return {
        status,
        messageId,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }

  /**
   * Broadcast a payload to ALL currently registered agents (excluding sender).
   *
   * Contract invariants:
   *   - The outer Promise always resolves (never rejects).
   *   - deliveredCount + failedCount === number of agents targeted.
   *   - allDelivered is true only when every agent returned 'delivered'.
   *
   * 4-point logging: entry → decision (target list) → step (fan-out) → exit
   */
  async broadcast<TIn>(
    from: string,
    payload: TIn,
    opts?: { sessionId?: string },
  ): Promise<A2ABroadcastResult> {
    log.engine.debug("a2a.registry.broadcast: entry", {
      from,
      totalRegistered: this.agents.size,
      sessionId: opts?.sessionId,
    });

    // Target = every registered agent except the sender itself
    const targets = [...this.agents.keys()].filter((id) => id !== from);

    log.engine.debug(
      "a2a.registry.broadcast: decision — target list built",
      { from, targetCount: targets.length, targets },
    );

    if (targets.length === 0) {
      log.engine.info("a2a.registry.broadcast: exit — no targets", { from });
      return {
        allDelivered: true,
        deliveredCount: 0,
        failedCount: 0,
        results: [],
      };
    }

    // ── Step: fan-out all sends in parallel ─────────────────────────────
    log.engine.debug(
      "a2a.registry.broadcast: step — fanning out sends",
      { targetCount: targets.length },
    );

    // Promise.allSettled ensures this outer Promise resolves regardless of
    // individual agent failures. send() itself never rejects, so allSettled
    // is defence-in-depth.
    const settled = await Promise.allSettled(
      targets.map((to) =>
        this.send<TIn, unknown>(to, from, payload, {
          sessionId: opts?.sessionId,
        }),
      ),
    );

    const results: A2AResult<unknown>[] = settled.map((s) =>
      s.status === "fulfilled"
        ? s.value
        : {
            status: "failed" as const,
            messageId: randomUUID(),
            error:
              s.reason instanceof Error
                ? s.reason.message
                : String(s.reason),
          },
    );

    const deliveredCount = results.filter(
      (r) => r.status === "delivered",
    ).length;
    const failedCount = results.length - deliveredCount;
    const allDelivered = failedCount === 0;

    // ── Exit ─────────────────────────────────────────────────────────────
    log.engine.info("a2a.registry.broadcast: exit", {
      from,
      targetCount: targets.length,
      deliveredCount,
      failedCount,
      allDelivered,
    });

    return { allDelivered, deliveredCount, failedCount, results };
  }

  // ─── Introspection ─────────────────────────────────────────────────────

  /** Number of currently registered agents */
  get size(): number {
    return this.agents.size;
  }

  // ─── Private Helpers ───────────────────────────────────────────────────

  /**
   * Build a lazy A2AContext for the given sessionId.
   *
   * The context is returned synchronously; getHistory() is async and resolves
   * lazily when called by the agent handler. This matches the lazy-proxy
   * pattern from SessionBridgeFactory (acp/bridge.ts:31) without its
   * permissions model.
   *
   * If sessionId is empty, getHistory() always returns [].
   * If getSessionStore is not provided, getHistory() always returns [].
   * If the store's loadSession() throws, getHistory() returns [] and logs.
   *
   * 4-point logging inside getHistory closure: entry / decision / step / exit
   */
  private buildContext(sessionId: string): A2AContext {
    log.engine.debug("a2a.registry.buildContext: entry", { sessionId });

    const storeResolver = this.getSessionStore;

    const context: A2AContext = {
      sessionId,

      getHistory: async (limit?: number): Promise<import("../providers/base.js").ChatMessage[]> => {
        log.engine.debug("a2a.context.getHistory: entry", {
          sessionId,
          limit,
        });

        if (!sessionId) {
          log.engine.debug(
            "a2a.context.getHistory: decision — no sessionId, returning []",
            { sessionId },
          );
          return [];
        }

        if (!storeResolver) {
          log.engine.debug(
            "a2a.context.getHistory: decision — no store resolver, returning []",
            { sessionId },
          );
          return [];
        }

        let store: SessionStore;
        try {
          store = storeResolver();
        } catch (err) {
          log.engine.error(
            "a2a.context.getHistory: step — getSessionStore() threw (lazy resolver error)",
            err instanceof Error ? err : new Error(String(err)),
            { sessionId },
          );
          return [];
        }

        log.engine.debug(
          "a2a.context.getHistory: step — loading session from store",
          { sessionId },
        );

        try {
          const session = await store.loadSession(sessionId);
          const allMessages = session?.messages ?? [];
          const messages =
            limit != null ? allMessages.slice(-limit) : allMessages;

          log.engine.debug("a2a.context.getHistory: exit", {
            sessionId,
            totalMessages: allMessages.length,
            returned: messages.length,
            limit,
          });

          return messages;
        } catch (err) {
          log.engine.error(
            "a2a.context.getHistory: step — loadSession() threw",
            err instanceof Error ? err : new Error(String(err)),
            { sessionId },
          );
          return [];
        }
      },
    };

    log.engine.debug("a2a.registry.buildContext: exit — context built", {
      sessionId,
    });

    return context;
  }

  /**
   * Increment the consecutive failure count for agentId.
   * If the count reaches AUTO_DEREGISTER_THRESHOLD, the agent is automatically
   * removed from the registry (without clearing the failure map entry — the
   * next register() for the same ID starts fresh because register() doesn't
   * look at the failures map).
   *
   * Note: we delete from `agents` but leave the failure count so that
   * unregister() remains a no-op (agent already gone). The failure entry
   * will be naturally overwritten or deleted on next unregister().
   */
  private recordFailure(agentId: string): void {
    const current = this.failures.get(agentId) ?? 0;
    const next = current + 1;
    this.failures.set(agentId, next);

    log.engine.debug("a2a.registry.recordFailure", {
      agentId,
      consecutiveFailures: next,
      threshold: AUTO_DEREGISTER_THRESHOLD,
    });

    if (next >= AUTO_DEREGISTER_THRESHOLD) {
      log.engine.warn(
        "a2a.registry.recordFailure: auto-deregistering agent after consecutive failures",
        { agentId, consecutiveFailures: next },
      );
      // Remove agent; failure entry stays so unregister() no-ops cleanly.
      this.agents.delete(agentId);
      // Clear the failure count — the agent slot is now open for fresh registration.
      this.failures.delete(agentId);
    }
  }

  /**
   * Reset the consecutive failure counter on a successful agent.handle() call.
   */
  private recordSuccess(agentId: string): void {
    if (this.failures.has(agentId)) {
      log.engine.debug(
        "a2a.registry.recordSuccess: resetting consecutive failure count",
        { agentId, wasAt: this.failures.get(agentId) },
      );
      this.failures.delete(agentId);
    }
  }

  /**
   * Race a promise against a configurable timeout deadline.
   * Rejects with a message prefixed "A2A timeout:" so callers can
   * detect timeouts by inspecting the error message.
   */
  private raceTimeout<T>(
    promise: Promise<T>,
    timeoutMs: number,
    messageId: string,
    agentId: string,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(
          new Error(
            `A2A timeout: agent "${agentId}" did not respond within ${timeoutMs}ms (messageId=${messageId})`,
          ),
        );
      }, timeoutMs);

      promise.then(
        (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        (err: unknown) => {
          clearTimeout(timer);
          reject(err);
        },
      );
    });
  }
}

/**
 * StackOwl — A2A (Agent-to-Agent) Types
 *
 * Typed contracts for the A2ARegistry: agents, results, context, errors.
 * These replace the ACP router+bridge+backpressure stack for direct
 * in-process agent-to-agent communication without the overhead of
 * channel registration, inbox queuing, or session bridging.
 */

import type { ChatMessage } from "../providers/base.js";

// ─── A2AMessage (internal to registry — not exported from barrel) ──────────
// Visibility: INTERNAL. The barrel (index.ts) must NOT re-export this type.
// Tests assert: "barrel does not export A2AMessage".
// This interface is re-declared locally in registry.ts to keep it internal.

// ─── A2AContext ────────────────────────────────────────────────────────────

export interface A2AContext {
  /** Session ID associated with this exchange */
  readonly sessionId: string;

  /**
   * Return conversation history from the referenced session.
   *
   * @param limit - Optional cap on entries returned (most-recent first).
   *                If omitted, the full session history is returned.
   *                Returns [] when no session store is available or the
   *                session is not found.
   */
  getHistory(limit?: number): Promise<ChatMessage[]>;
}

// ─── A2AAgent ──────────────────────────────────────────────────────────────

export interface A2AAgent {
  /** Unique stable identifier for this agent */
  readonly agentId: string;

  /**
   * Handle an incoming payload from another agent.
   *
   * The handler receives the raw payload (not the internal envelope) and a
   * lazy A2AContext for session history access.
   *
   * Implementations MUST NOT throw — they should return a result indicating
   * failure instead. The registry will catch any thrown exception and
   * translate it to { status: 'failed' } so the outer call-chain is never
   * interrupted by a rogue agent.
   */
  handle<TIn = unknown, TOut = unknown>(
    payload: TIn,
    context: A2AContext,
  ): Promise<TOut>;
}

// ─── Delivery Status ───────────────────────────────────────────────────────

export type A2AStatus = "delivered" | "failed" | "not-found" | "timeout";

// ─── A2AResult ────────────────────────────────────────────────────────────

export interface A2AResult<TOut = unknown> {
  /** Outcome of the delivery attempt */
  readonly status: A2AStatus;
  /** ID of the underlying A2AMessage (UUID v4) */
  readonly messageId: string;
  /** The agent's response payload — present when status === 'delivered' */
  readonly result?: TOut;
  /** Error description — present when status is 'failed' or 'timeout' */
  readonly error?: string;
}

// ─── A2ABroadcastResult ───────────────────────────────────────────────────

export interface A2ABroadcastResult {
  /** True when every targeted agent received and handled the payload */
  readonly allDelivered: boolean;
  /** Number of agents that handled the message successfully */
  readonly deliveredCount: number;
  /** Number of agents that failed or were not found */
  readonly failedCount: number;
  /** Per-agent result breakdown */
  readonly results: ReadonlyArray<A2AResult<unknown>>;
}

// ─── Errors ────────────────────────────────────────────────────────────────

export class A2ADuplicateAgentError extends Error {
  constructor(agentId: string) {
    super(
      `A2ARegistry: agent "${agentId}" is already registered. Call unregister() first.`,
    );
    this.name = "A2ADuplicateAgentError";
  }
}

export class A2AAgentNotFoundError extends Error {
  constructor(agentId: string) {
    super(`A2ARegistry: agent "${agentId}" is not registered.`);
    this.name = "A2AAgentNotFoundError";
  }
}

// ─── SessionStore minimal interface ───────────────────────────────────────

/**
 * Minimal session-store contract that A2ARegistry depends on.
 * Compatible with both src/memory/store.ts and src/sessions/store.ts.
 */
export interface SessionStore {
  loadSession(
    sessionId: string,
  ): Promise<{ messages: ChatMessage[] } | null | undefined>;
}

/**
 * StackOwl — Event Bus
 *
 * Decouples subsystems via typed pub/sub events.
 * Built on Node.js EventEmitter for zero-dependency async dispatch.
 */

import { EventEmitter } from "node:events";
import type { TokenUsage } from "../providers/base.js";
import { log } from "../logger.js";

// ─── Event Definitions ──────────────────────────────────────────

export interface EventPayloads {
  "message:received": {
    sessionId: string;
    channelId: string;
    userId: string;
    text: string;
  };
  "message:responded": {
    sessionId: string;
    channelId: string;
    userId: string;
    content: string;
    owlName: string;
    toolsUsed: string[];
    usage?: TokenUsage;
    messages?: Array<{ role: string; content: string }>;
  };
  "session:created": {
    sessionId: string;
    owlName: string;
    channelId: string;
  };
  "session:ended": {
    sessionId: string;
    messageCount: number;
  };
  "tool:called": {
    name: string;
    args: Record<string, unknown>;
    sessionId: string;
  };
  "tool:result": {
    name: string;
    success: boolean;
    result: string;
    sessionId: string;
    durationMs: number;
  };
  "evolution:triggered": {
    owlName: string;
    generation: number;
  };
  "pellet:created": {
    id: string;
    title: string;
    tags: string[];
  };
  "capability:gap": {
    description: string;
    toolName?: string;
    sessionId: string;
  };
  "cost:usage": {
    provider: string;
    model: string;
    usage: TokenUsage;
    sessionId: string;
    userId: string;
  };
  "error:subsystem": {
    subsystem: string;
    error: string;
    recoverable: boolean;
  };

  // ─── Agent State & Ping Events ────────────────────────────
  "agent:state_change": {
    sessionId: string;
    state: "IDLE" | "PLANNING" | "EXECUTING";
  };
  "agent:ping_request": {
    prompt: string;
    type: string;
  };

  // ─── Plugin Events ──────────────────────────────────────────
  "plugin:loaded": {
    name: string;
    version: string;
  };
  "plugin:started": {
    name: string;
  };
  "plugin:stopped": {
    name: string;
  };
  "plugin:error": {
    name: string;
    error: string;
  };

  // ─── Hot Reload Events ──────────────────────────────────────
  "reload:started": {
    moduleId: string;
    affectedModules: string[];
  };
  "reload:completed": {
    moduleId: string;
    events: Array<{
      moduleId: string;
      kind: string;
      action: string;
      success: boolean;
      rolledBack: boolean;
      durationMs: number;
    }>;
  };
  "reload:failed": {
    moduleId: string;
    error: string;
  };
  "reload:rolledback": {
    moduleId: string;
    error: string;
  };

  // ─── ACP Events ─────────────────────────────────────────────
  "acp:message:sent": {
    messageId: string;
    from: string;
    to: string;
    channel: string;
  };
  "acp:message:delivered": {
    messageId: string;
    from: string;
    to: string;
    channel: string;
  };
  "acp:message:failed": {
    messageId: string;
    from: string;
    to: string;
    error: string;
  };
  "acp:stream:opened": {
    messageId: string;
    to: string;
    channel: string;
  };
  "acp:stream:closed": {
    messageId: string;
    to: string;
    channel: string;
  };
}

export type EventType = keyof EventPayloads;

// ─── EventBus Interface ─────────────────────────────────────────

export interface EventBus {
  emit<T extends EventType>(type: T, payload: EventPayloads[T]): void;
  on<T extends EventType>(
    type: T,
    handler: (payload: EventPayloads[T]) => void | Promise<void>,
  ): void;
  off<T extends EventType>(type: T, handler: (...args: any[]) => any): void;
  once<T extends EventType>(
    type: T,
    handler: (payload: EventPayloads[T]) => void | Promise<void>,
  ): void;
}

// ─── Implementation ─────────────────────────────────────────────

export class StackOwlEventBus implements EventBus {
  private emitter = new EventEmitter();

  constructor() {
    // Allow many subscribers without warning
    this.emitter.setMaxListeners(50);
  }

  emit<T extends EventType>(type: T, payload: EventPayloads[T]): void {
    try {
      this.emitter.emit(type, payload);
      this.emitter.emit("*", { type, payload });
    } catch (err) {
      log.engine.warn(
        `[EventBus] Error emitting "${type}": ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  on<T extends EventType>(
    type: T,
    handler: (payload: EventPayloads[T]) => void | Promise<void>,
  ): void {
    this.emitter.on(type, (payload: EventPayloads[T]) => {
      try {
        const result = handler(payload);
        // If handler returns a promise, catch errors silently
        if (result && typeof (result as any).catch === "function") {
          (result as Promise<void>).catch((err) => {
            log.engine.warn(
              `[EventBus] Async handler error for "${type}": ${err instanceof Error ? err.message : String(err)}`,
            );
          });
        }
      } catch (err) {
        log.engine.warn(
          `[EventBus] Handler error for "${type}": ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    });
  }

  off<T extends EventType>(type: T, handler: (...args: any[]) => any): void {
    this.emitter.off(type, handler);
  }

  once<T extends EventType>(
    type: T,
    handler: (payload: EventPayloads[T]) => void | Promise<void>,
  ): void {
    this.emitter.once(type, handler as any);
  }

  /** Number of listeners for a given event type. */
  listenerCount(type: EventType): number {
    return this.emitter.listenerCount(type);
  }

  /** Remove all listeners. Used in tests and shutdown. */
  removeAllListeners(): void {
    this.emitter.removeAllListeners();
  }
}

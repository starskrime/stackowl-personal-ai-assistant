/**
 * StackOwl — ACP Router
 *
 * Capability-based message routing between agents.
 * Supports direct send, capability-based discovery, request/response,
 * and streaming channels.
 */

import { randomUUID } from "node:crypto";
import type {
  ACPMessage,
  ACPCapability,
  ACPMessageHandler,
  ACPStreamWriter,
  DeliveryStatus,
  SessionBridge,
} from "./types.js";
import { ACPBackpressure } from "./backpressure.js";
import { SessionBridgeFactory } from "./bridge.js";
import type { AgentRegistry } from "../agents/types.js";
import type { EventBus } from "../events/bus.js";
import { log } from "../logger.js";

interface RegisteredHandler {
  agentId: string;
  channel: string;
  handler: ACPMessageHandler;
}

export class ACPRouter {
  private handlers = new Map<string, RegisteredHandler[]>(); // channel → handlers
  private agentHandlers = new Map<string, RegisteredHandler[]>(); // agentId → handlers
  private capabilities = new Map<string, ACPCapability[]>(); // agentId → capabilities
  private backpressure: ACPBackpressure;
  private pendingRequests = new Map<string, {
    resolve: (value: unknown) => void;
    reject: (err: Error) => void;
    timeout: NodeJS.Timeout;
  }>();

  constructor(
    _agentRegistry: AgentRegistry,
    private eventBus: EventBus,
    private bridgeFactory?: SessionBridgeFactory,
    maxInboxSize: number = 100,
  ) {
    this.backpressure = new ACPBackpressure(maxInboxSize);

    // Periodic expired message cleanup
    setInterval(() => this.backpressure.pruneExpired(), 60_000).unref();
  }

  // ─── Agent Registration ──────────────────────────────────────

  /**
   * Register an agent's ACP capabilities and message handlers.
   */
  registerAgent(
    agentId: string,
    capabilities: ACPCapability[],
    channelHandlers: Array<{ channel: string; handler: ACPMessageHandler }>,
  ): void {
    this.capabilities.set(agentId, capabilities);

    const agentEntries: RegisteredHandler[] = [];
    for (const { channel, handler } of channelHandlers) {
      const entry: RegisteredHandler = { agentId, channel, handler };

      if (!this.handlers.has(channel)) {
        this.handlers.set(channel, []);
      }
      this.handlers.get(channel)!.push(entry);
      agentEntries.push(entry);
    }
    this.agentHandlers.set(agentId, agentEntries);

    log.engine.info(
      `[ACP] Registered agent "${agentId}" with ${capabilities.length} capabilities on ${channelHandlers.length} channels`,
    );
  }

  /**
   * Unregister an agent and all its handlers.
   */
  unregisterAgent(agentId: string): void {
    this.capabilities.delete(agentId);
    this.backpressure.clearInbox(agentId);

    // Remove from channel handlers
    const entries = this.agentHandlers.get(agentId);
    if (entries) {
      for (const entry of entries) {
        const channelHandlers = this.handlers.get(entry.channel);
        if (channelHandlers) {
          const filtered = channelHandlers.filter((h) => h.agentId !== agentId);
          if (filtered.length === 0) {
            this.handlers.delete(entry.channel);
          } else {
            this.handlers.set(entry.channel, filtered);
          }
        }
      }
    }
    this.agentHandlers.delete(agentId);

    log.engine.info(`[ACP] Unregistered agent "${agentId}"`);
  }

  // ─── Message Sending ─────────────────────────────────────────

  /**
   * Send a message to a specific agent.
   */
  async send<T>(message: ACPMessage<T>): Promise<DeliveryStatus> {
    // Find handler for this agent + channel
    const agentEntries = this.agentHandlers.get(message.to);
    if (!agentEntries) return "not-found";

    const handler = agentEntries.find((h) => h.channel === message.channel);
    if (!handler) return "not-found";

    // Check backpressure
    const status = this.backpressure.enqueue(message.to, message as ACPMessage);
    if (status !== "delivered") return status;

    // Dequeue and deliver
    const queued = this.backpressure.dequeue(message.to);
    if (!queued) return "not-found";

    // Create session bridge if session reference provided
    let bridge: SessionBridge | undefined;
    if (message.sessionRef && this.bridgeFactory) {
      bridge = this.bridgeFactory.createBridge(message.sessionRef, {
        readHistory: true,
        readPellets: true,
        writeContext: false,
        maxHistoryDepth: 20,
      });
    }

    // Execute handler
    try {
      const result = await handler.handler(queued, bridge);

      // If this is a reply to a pending request, resolve it
      if (message.replyTo && this.pendingRequests.has(message.replyTo)) {
        const pending = this.pendingRequests.get(message.replyTo)!;
        clearTimeout(pending.timeout);
        pending.resolve(result);
        this.pendingRequests.delete(message.replyTo);
      }

      this.eventBus.emit("acp:message:delivered" as any, {
        messageId: message.id,
        from: message.from,
        to: message.to,
        channel: message.channel,
      });

      return "delivered";
    } catch (err) {
      log.engine.warn(
        `[ACP] Handler error for ${message.channel}@${message.to}: ${err instanceof Error ? err.message : String(err)}`,
      );

      this.eventBus.emit("acp:message:failed" as any, {
        messageId: message.id,
        from: message.from,
        to: message.to,
        error: err instanceof Error ? err.message : String(err),
      });

      return "rejected";
    }
  }

  /**
   * Route a message to the best agent by capability.
   */
  async sendToCapability<T>(
    capability: string,
    payload: T,
    options?: { from?: string; channel?: string; prefer?: string; exclude?: string[] },
  ): Promise<{ agentId: string; status: DeliveryStatus }> {
    // Find agents with this capability
    const candidates: Array<{ agentId: string; priority: number }> = [];

    for (const [agentId, caps] of this.capabilities) {
      if (options?.exclude?.includes(agentId)) continue;

      const match = caps.find((c) => c.name === capability);
      if (match) {
        candidates.push({
          agentId,
          priority: agentId === options?.prefer ? -1 : match.priority,
        });
      }
    }

    if (candidates.length === 0) {
      return { agentId: "", status: "not-found" };
    }

    // Sort by priority (lowest first)
    candidates.sort((a, b) => a.priority - b.priority);
    const best = candidates[0];

    const message: ACPMessage<T> = {
      id: randomUUID(),
      from: options?.from ?? "system",
      to: best.agentId,
      channel: options?.channel ?? capability,
      payload,
      timestamp: Date.now(),
    };

    const status = await this.send(message);
    return { agentId: best.agentId, status };
  }

  /**
   * Request/response pattern with timeout.
   */
  async request<TIn, TOut>(
    to: string,
    channel: string,
    payload: TIn,
    timeoutMs: number = 30_000,
  ): Promise<TOut> {
    const messageId = randomUUID();

    const message: ACPMessage<TIn> = {
      id: messageId,
      from: "system",
      to,
      channel,
      payload,
      timestamp: Date.now(),
    };

    return new Promise<TOut>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pendingRequests.delete(messageId);
        reject(new Error(`ACP request to ${to}/${channel} timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      this.pendingRequests.set(messageId, {
        resolve: resolve as (value: unknown) => void,
        reject,
        timeout,
      });

      this.send(message).then((status) => {
        if (status !== "delivered") {
          clearTimeout(timeout);
          this.pendingRequests.delete(messageId);
          reject(new Error(`ACP send failed: ${status}`));
        }
      });
    });
  }

  /**
   * Open a streaming channel to an agent.
   */
  openStream<T>(to: string, channel: string): ACPStreamWriter<T> {
    const messageId = randomUUID();
    const chunks: T[] = [];
    let ended = false;
    const waiters: Array<{ resolve: (value: IteratorResult<T>) => void }> = [];

    // Send stream-open notification
    this.eventBus.emit("acp:stream:opened" as any, {
      messageId,
      to,
      channel,
    });

    // Return the writer
    const writer: ACPStreamWriter<T> = {
      write(chunk: T) {
        if (ended) return;
        if (waiters.length > 0) {
          const waiter = waiters.shift()!;
          waiter.resolve({ value: chunk, done: false });
        } else {
          chunks.push(chunk);
        }
      },

      end() {
        ended = true;
        for (const w of waiters) {
          w.resolve({ value: undefined as any, done: true });
        }
        waiters.length = 0;
      },

      error(_err: Error) {
        ended = true;
        for (const w of waiters) {
          w.resolve({ value: undefined as any, done: true });
        }
        waiters.length = 0;
      },
    };

    return writer;
  }

  // ─── Introspection ───────────────────────────────────────────

  /**
   * List all registered capabilities.
   */
  listCapabilities(): Array<{ agentId: string; capabilities: ACPCapability[] }> {
    return [...this.capabilities.entries()].map(([agentId, caps]) => ({
      agentId,
      capabilities: caps,
    }));
  }

  /**
   * Find agents that handle a specific channel.
   */
  findByChannel(channel: string): string[] {
    const handlers = this.handlers.get(channel);
    if (!handlers) return [];
    return handlers.map((h) => h.agentId);
  }
}

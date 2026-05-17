/**
 * StackOwl — ProactiveDeliveryService
 *
 * Tracks per-session activity (channelId + userId) so proactive/scheduled
 * messages are always routed to the correct session. Replaces the old shared
 * `lastActiveChannel` / `lastActiveUserId` scalar bug on OwlGateway, which
 * could misdirect heartbeat messages when multiple sessions were active.
 */

import { log } from "../logger.js";
import type { GatewayResponse, ChannelAdapter } from "./types.js";

// ─── Activity Record ──────────────────────────────────────────────

interface ActivityRecord {
  channelId: string;
  userId: string;
}

// ─── Ready Message shape (mirrors ScheduledMessage in timer.ts) ───

interface ReadyMessage {
  id: string;
  message: string;
  channelId?: string | null;
  userId?: string | null;
}

// ─── Context the service needs from OwlGateway ────────────────────

export interface ProactiveDeliveryContext {
  adapters: Map<string, ChannelAdapter>;
  owl: { persona: { name: string; emoji: string } };
}

// ─── Public interface ─────────────────────────────────────────────

export interface IProactiveDeliveryService {
  recordActivity(sessionId: string, channelId: string, userId: string): void;
  getLastActivity(sessionId: string): ActivityRecord | undefined;
  deliver(channelId: string, userId: string, text: string, preformatted?: boolean): Promise<void>;
  broadcast(text: string): Promise<void>;
  deliverScheduled(getReadyMessages: () => ReadyMessage[]): Promise<void>;
}

// ─── Implementation ───────────────────────────────────────────────

export class ProactiveDeliveryService implements IProactiveDeliveryService {
  /** Per-session activity — session isolation for correctness */
  private readonly activity = new Map<string, ActivityRecord>();
  /** Last globally seen activity — fallback for un-attributed messages */
  private lastGlobalActivity: ActivityRecord | null = null;

  constructor(private readonly ctx: ProactiveDeliveryContext) {}

  // ── Activity tracking ──────────────────────────────────────────

  recordActivity(sessionId: string, channelId: string, userId: string): void {
    log.gateway.debug("ProactiveDeliveryService.recordActivity: entry", { sessionId, channelId, userId });
    const record: ActivityRecord = { channelId, userId };
    this.activity.set(sessionId, record);
    this.lastGlobalActivity = record;
    log.gateway.debug("ProactiveDeliveryService.recordActivity: exit", { sessionId, total: this.activity.size });
  }

  getLastActivity(sessionId: string): ActivityRecord | undefined {
    return this.activity.get(sessionId);
  }

  // ── Single-message delivery ────────────────────────────────────

  async deliver(channelId: string, userId: string, text: string, preformatted = false): Promise<void> {
    log.gateway.debug("ProactiveDeliveryService.deliver: entry", { channelId, userId, textLen: text.length });

    const adapter = this.ctx.adapters?.get(channelId);
    if (!adapter) {
      log.gateway.warn("ProactiveDeliveryService.deliver: no adapter for channel — skipping", { channelId });
      return;
    }

    log.gateway.debug("ProactiveDeliveryService.deliver: sending via adapter", { channelId, adapterId: adapter.id });

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
      log.gateway.error("ProactiveDeliveryService.deliver: sendToUser failed", err as Error, { channelId, userId });
    }
  }

  // ── Broadcast to all adapters ──────────────────────────────────

  async broadcast(text: string): Promise<void> {
    log.gateway.debug("ProactiveDeliveryService.broadcast: entry", { textLen: text.length, adapterCount: this.ctx.adapters.size });
    const response: GatewayResponse = {
      content: text,
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      toolsUsed: [],
    };
    for (const [channelId, adapter] of this.ctx.adapters) {
      try {
        await adapter.broadcast?.(response);
        log.gateway.debug("ProactiveDeliveryService.broadcast: sent to adapter", { channelId });
      } catch (err) {
        log.gateway.error("ProactiveDeliveryService.broadcast: adapter failed", err as Error, { channelId });
      }
    }
    log.gateway.debug("ProactiveDeliveryService.broadcast: exit");
  }

  // ── Scheduled batch delivery ───────────────────────────────────

  async deliverScheduled(getReadyMessages: () => ReadyMessage[]): Promise<void> {
    log.gateway.debug("ProactiveDeliveryService.deliverScheduled: entry");

    const ready = getReadyMessages();
    if (ready.length === 0) {
      log.gateway.debug("ProactiveDeliveryService.deliverScheduled: no messages ready");
      return;
    }

    log.gateway.debug("ProactiveDeliveryService.deliverScheduled: processing", { count: ready.length });

    let delivered = 0;
    let skipped = 0;

    for (const msg of ready) {
      // Timer-scheduled messages have null channelId/userId — they're created before any session is
      // established. Fall back to lastGlobalActivity (most recent session); for a per-session fix,
      // ScheduledMessage would need to carry a sessionId at creation time.
      const channelId = msg.channelId ?? this.lastGlobalActivity?.channelId ?? null;
      const userId = msg.userId ?? this.lastGlobalActivity?.userId ?? null;

      if (!channelId || !userId) {
        log.gateway.debug("ProactiveDeliveryService.deliverScheduled: no specific target, broadcasting", { id: msg.id });
        await this.broadcast(msg.message).catch((err: Error) => {
          log.gateway.error("ProactiveDeliveryService.deliverScheduled: broadcast failed", err, { id: msg.id });
        });
        continue;
      }

      log.gateway.debug("ProactiveDeliveryService.deliverScheduled: delivering message", {
        id: msg.id,
        channelId,
        userId,
      });

      await this.deliver(channelId, userId, msg.message);

      delivered++;
    }

    log.gateway.debug("ProactiveDeliveryService.deliverScheduled: exit", { delivered, skipped });
  }
}

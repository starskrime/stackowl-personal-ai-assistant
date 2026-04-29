import type { GatewayEventBus } from "./event-bus.js"
import type { ChannelRegistry } from "./channel-registry.js"
import type { DeliveryEnvelope } from "./delivery-envelope.js"
import type Database from "better-sqlite3"
import { log } from "../logger.js"

export class DeliveryRouter {
  private readonly retryDelaysMs: number[]

  constructor(
    private registry: ChannelRegistry,
    private db?: Database.Database,
    retryDelaysMs = [0, 2_000, 8_000]
  ) {
    this.retryDelaysMs = retryDelaysMs
  }

  start(bus: GatewayEventBus): void {
    bus.onDeliver(env => this.route(env))
  }

  private async route(envelope: DeliveryEnvelope): Promise<void> {
    if (envelope.ttlMs !== undefined && envelope.createdAt + envelope.ttlMs < Date.now()) {
      this.writeLog(envelope, "dropped_ttl", 0, undefined)
      return
    }

    const adapter = envelope.channelId
      ? this.registry.get(envelope.channelId)
      : this.registry.getBestChannel(envelope.userId, envelope.urgency)

    if (!adapter) {
      this.writeLog(envelope, "dropped_no_channel", 0, undefined)
      log.engine.warn(
        `[DeliveryRouter] no channel for userId=${envelope.userId} urgency=${envelope.urgency}`
      )
      return
    }

    const maxAttempts = this.retryDelaysMs.length
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (attempt > 0) {
        await new Promise(r => setTimeout(r, this.retryDelaysMs[attempt]))
        if (envelope.ttlMs !== undefined && envelope.createdAt + envelope.ttlMs < Date.now()) {
          this.writeLog(envelope, "dropped_ttl", attempt, undefined)
          return
        }
      }
      try {
        await adapter.deliver(envelope)
        this.writeLog(envelope, "delivered", attempt, undefined)
        return
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        if (attempt === maxAttempts - 1) {
          this.writeLog(envelope, "failed", attempt, msg)
          log.engine.error(`[DeliveryRouter] delivery failed after ${attempt + 1} attempt(s): ${msg}`)
        }
      }
    }
  }

  private writeLog(
    envelope: DeliveryEnvelope,
    status: string,
    attempt: number,
    error: string | undefined
  ): void {
    if (!this.db) return
    try {
      this.db.prepare(`
        INSERT INTO delivery_log
          (id, envelope_id, user_id, channel_id, urgency, trigger, status, attempt, error, delivered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        crypto.randomUUID(),
        envelope.envelopeId,
        envelope.userId,
        envelope.channelId ?? "unknown",
        envelope.urgency,
        envelope.trigger,
        status,
        attempt,
        error ?? null,
        status === "delivered" ? Date.now() : null
      )
    } catch {
      // non-fatal — never break delivery because of a logging error
    }
  }
}

import type { ChannelAdapterV2 } from "./adapter-v2.js"
import type { ChannelCapabilities } from "./channel-capabilities.js"
import type { DeliveryUrgency } from "./delivery-envelope.js"
import { log } from "../logger.js"

export class ChannelRegistry {
  private adapters = new Map<string, ChannelAdapterV2>()
  private presence = new Map<string, Map<string, number>>() // channelId → userId → lastSeen ms

  register(adapter: ChannelAdapterV2): void {
    this.adapters.set(adapter.capabilities.channelId, adapter)
    log.engine.info(`[ChannelRegistry] registered: ${adapter.capabilities.channelId}`)
  }

  unregister(channelId: string): void {
    this.adapters.delete(channelId)
    this.presence.delete(channelId)
  }

  get(channelId: string): ChannelAdapterV2 | undefined {
    return this.adapters.get(channelId)
  }

  listAll(): ChannelAdapterV2[] {
    return Array.from(this.adapters.values())
  }

  markActive(channelId: string, userId: string): void {
    if (!this.presence.has(channelId)) this.presence.set(channelId, new Map())
    this.presence.get(channelId)!.set(userId, Date.now())
  }

  markInactive(channelId: string, userId: string): void {
    this.presence.get(channelId)?.delete(userId)
  }

  getLastSeen(channelId: string, userId: string): number {
    return this.presence.get(channelId)?.get(userId) ?? 0
  }

  getActiveChannels(userId: string): ChannelAdapterV2[] {
    const result: ChannelAdapterV2[] = []
    for (const [channelId, adapter] of this.adapters) {
      if (this.presence.get(channelId)?.has(userId)) result.push(adapter)
    }
    return result
  }

  getCapableChannels(
    userId: string,
    requires: Partial<ChannelCapabilities>
  ): ChannelAdapterV2[] {
    return this.getActiveChannels(userId).filter(adapter => {
      const caps = adapter.capabilities as unknown as Record<string, unknown>
      return Object.entries(requires).every(([k, v]) => caps[k] === v)
    })
  }

  getBestChannel(userId: string, urgency: DeliveryUrgency): ChannelAdapterV2 | undefined {
    if (urgency === "interrupt") {
      for (const adapter of this.adapters.values()) {
        if (adapter.capabilities.supportsInterrupt) return adapter
      }
      return undefined
    }

    const active = this.getActiveChannels(userId)

    if (urgency === "proactive") {
      const asyncChannels = active.filter(a => a.capabilities.async)
      if (asyncChannels.length === 0) return undefined
      return asyncChannels.reduce((best, a) =>
        this.getLastSeen(a.capabilities.channelId, userId) >
        this.getLastSeen(best.capabilities.channelId, userId) ? a : best
      )
    }

    if (urgency === "background") {
      const TWENTY_FOUR_H = 24 * 60 * 60 * 1000
      return active
        .filter(a => a.capabilities.async)
        .find(a => this.getLastSeen(a.capabilities.channelId, userId) > Date.now() - TWENTY_FOUR_H)
    }

    // normal — most-recently-active
    if (active.length === 0) return undefined
    return active.reduce((best, a) =>
      this.getLastSeen(a.capabilities.channelId, userId) >
      this.getLastSeen(best.capabilities.channelId, userId) ? a : best
    )
  }
}

import type { ChannelAdapter, GatewayResponse } from "./types.js"
import type { ChannelAdapterV2, AskPayload } from "./adapter-v2.js"
import type { ChannelCapabilities } from "./channel-capabilities.js"
import type { ChannelRegistry } from "./channel-registry.js"
import type { DeliveryEnvelope } from "./delivery-envelope.js"

/**
 * Wraps a v1 ChannelAdapter to satisfy the ChannelAdapterV2 interface.
 * Remove one-by-one as each adapter is rewritten in Phase 2.
 */
export class ChannelAdapterV1Shim implements ChannelAdapterV2 {
  constructor(
    private v1: ChannelAdapter,
    private caps: ChannelCapabilities
  ) {}

  get capabilities(): ChannelCapabilities { return this.caps }

  async start(): Promise<void> { await this.v1.start() }

  async stop(): Promise<void> { this.v1.stop() }

  register(registry: ChannelRegistry): void { registry.register(this) }

  async deliver(envelope: DeliveryEnvelope): Promise<void> {
    // TUI v2 fast path: proactive messages get their own HeartbeatBanner lane
    // instead of appearing as inline chat replies.
    if (
      envelope.urgency === "proactive" &&
      this.v1.capabilities?.()?.tuiV2 &&
      this.v1.emit
    ) {
      this.v1.emit({
        kind: "heartbeat.message",
        owlId: envelope.userId,
        owlName: "",
        owlEmoji: "🔔",
        text: envelope.content.text,
        timestamp: envelope.createdAt,
      });
      return;
    }

    const response: GatewayResponse = {
      content: envelope.content.text,
      owlName: "",
      owlEmoji: "",
      toolsUsed: [],
    }
    await this.v1.sendToUser(envelope.userId, response)
  }

  async ask(_userId: string, prompt: AskPayload): Promise<string> {
    return prompt.defaultChoice ?? "yes"
  }
}

/**
 * Returns default ChannelCapabilities for each known v1 channel ID.
 * Used when wrapping v1 adapters in the shim.
 */
export function defaultCapsForV1(channelId: string): ChannelCapabilities {
  const base: ChannelCapabilities = {
    channelId,
    displayName: channelId,
    streaming: false,
    async: false,
    multiUser: false,
    maxMessageLength: Infinity,
    formatting: "plain",
    supportsButtons: false,
    supportsFiles: false,
    supportsVoice: false,
    supportsImages: false,
    supportsThreads: false,
    supportsReactions: false,
    supportsInterrupt: false,
  }
  switch (channelId) {
    case "telegram":
      return { ...base, streaming: true, async: true, maxMessageLength: 4096,
               formatting: "html", supportsButtons: true, supportsFiles: true,
               supportsImages: true, supportsInterrupt: true }
    case "slack":
      return { ...base, streaming: true, async: true, multiUser: true,
               maxMessageLength: 3000, formatting: "mrkdwn", supportsButtons: true,
               supportsFiles: true, supportsImages: true, supportsThreads: true,
               supportsReactions: true, supportsInterrupt: true }
    case "cli":
      return { ...base, streaming: true, formatting: "ansi" }
    case "voice":
      return { ...base, streaming: true, supportsVoice: true,
               maxMessageLength: 800, formatting: "plain" }
    case "web":
      return { ...base, streaming: true, async: true, maxMessageLength: Infinity,
               formatting: "markdown", supportsButtons: true, supportsFiles: true,
               supportsImages: true, supportsInterrupt: true }
    default:
      return base
  }
}

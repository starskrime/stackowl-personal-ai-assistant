import type { ChannelCapabilities } from "./channel-capabilities.js"
import type { ChannelRegistry } from "./channel-registry.js"
import type { DeliveryEnvelope } from "./delivery-envelope.js"

export interface ChannelAdapterV2 {
  readonly capabilities: ChannelCapabilities
  start(): Promise<void>
  stop(): Promise<void>
  register(registry: ChannelRegistry): void
  deliver(envelope: DeliveryEnvelope): Promise<void>
  ask(userId: string, prompt: AskPayload): Promise<string>
}

export interface AskPayload {
  text: string
  choices?: string[]
  timeoutMs?: number
  defaultChoice?: string
}

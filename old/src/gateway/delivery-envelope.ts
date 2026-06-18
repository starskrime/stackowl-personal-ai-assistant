import { v4 as uuidv4 } from "uuid"
import type { RichContent } from "./rich-content.js"

export type DeliveryUrgency = "background" | "normal" | "proactive" | "interrupt"

export type DeliveryTrigger =
  | "user-request"
  | "proactive"
  | "background-result"
  | "commitment"
  | "alert"
  | "parliament"

export interface DeliveryEnvelope {
  envelopeId: string
  createdAt: number
  userId: string
  channelId?: string
  content: RichContent
  urgency: DeliveryUrgency
  trigger: DeliveryTrigger
  ttlMs?: number
  sessionId?: string
  /** Set on outbound proactive envelopes — adapters use this to correlate replies */
  deliveryId?: string
  /** Job type for proactive deliveries (e.g. "morning_brief", "check_in") */
  jobType?: string
  /** Set on inbound user replies that follow a proactive delivery */
  inReplyToDeliveryId?: string
}

export function makeEnvelope(
  partial: Omit<DeliveryEnvelope, "envelopeId" | "createdAt">
): DeliveryEnvelope {
  return { ...partial, envelopeId: uuidv4(), createdAt: Date.now() }
}

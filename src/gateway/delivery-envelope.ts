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
}

export function makeEnvelope(
  partial: Omit<DeliveryEnvelope, "envelopeId" | "createdAt">
): DeliveryEnvelope {
  return { ...partial, envelopeId: uuidv4(), createdAt: Date.now() }
}

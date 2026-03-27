/**
 * StackOwl — Time Capsule Types
 *
 * Future-self messaging: date/condition/event-triggered messages delivered by heartbeat.
 */

export type TriggerType = "date" | "condition" | "event";
export type CapsuleStatus = "sealed" | "delivered" | "expired";

export interface CapsuleTrigger {
  type: TriggerType;
  /** ISO date string for date-based triggers */
  date?: string;
  /** Natural-language condition for LLM evaluation */
  condition?: string;
  /** Event name for event-based triggers (e.g. "quest_completed", "pellet_count_50") */
  event?: string;
}

export interface TimeCapsule {
  id: string;
  /** The message from past-self */
  message: string;
  /** Optional context snapshot at creation time */
  contextSnapshot?: string;
  trigger: CapsuleTrigger;
  status: CapsuleStatus;
  createdAt: string;
  deliveredAt?: string;
  /** Owl that was active when capsule was created */
  owlName?: string;
}

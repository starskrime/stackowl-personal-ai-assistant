import { EventEmitter } from "node:events"
import type { DeliveryEnvelope } from "./delivery-envelope.js"

export type GatewaySystemEvent =
  | { type: "pellet:created";    pelletId: string;  userId: string }
  | { type: "learning:complete"; summary: string;   userId: string }
  | { type: "evolution:done";    owlName: string;   changes: string[] }
  | { type: "parliament:done";   topic: string;     verdict: string; userId: string }
  | { type: "perch:event";       source: string;    detail: string;  userId: string }
  | { type: "commitment:due";    text: string;      userId: string }
  | { type: "cost:alert";        spent: number;     budget: number;  userId: string }
  | { type: "tool:start";        toolName: string; args: Record<string, unknown>; turnId: string }
  | { type: "tool:result";       toolName: string; success: boolean; durationMs: number; truncated: boolean }
  | { type: "tool:retry";        toolName: string; attempt: number; reason: string }
  | { type: "tool:fallback";     fromTool: string; toTool: string; reason: string }
  | { type: "tool:goal_advance"; toolName: string; subGoal: string; verdict: "ADVANCES" | "PARTIAL" }
  | { type: "tool:goal_blocked"; toolName: string; subGoal: string; suggestion?: string }
  | { type: "task:failed";       userId: string; taskDescription: string; toolSequence: string[]; errorSummary: string; category: string; complexityTier: string }
  | { type: "fact:extracted";    userId: string; factText: string; factId: string }
  | { type: "fact:retracted";    sessionId: string; turnIndex: number; toolName: string; reason: string }
  | { type: "session:ended";     userId: string; sessionId: string }
  | { type: "memory:written";    id: string; kind: string; goal_id: string | null; importance: number }
  | { type: "memory:invalidated";id: string; reason: string; invalidated_by: string }
  | { type: "memory:classify_failed"; turnId: string; reason: string }

const DELIVER_EVENT = "gateway:deliver"

export class GatewayEventBus {
  private emitter = new EventEmitter().setMaxListeners(0)

  publish(envelope: DeliveryEnvelope): void {
    this.emitter.emit(DELIVER_EVENT, envelope)
  }

  onDeliver(handler: (env: DeliveryEnvelope) => Promise<void>): void {
    this.emitter.on(DELIVER_EVENT, handler)
  }

  emit<T extends GatewaySystemEvent>(event: T): void {
    this.emitter.emit(`system:${event.type}`, event)
  }

  on<T extends GatewaySystemEvent["type"]>(
    type: T,
    handler: (e: Extract<GatewaySystemEvent, { type: T }>) => void
  ): void {
    this.emitter.on(`system:${type}`, handler as (e: GatewaySystemEvent) => void)
  }
}

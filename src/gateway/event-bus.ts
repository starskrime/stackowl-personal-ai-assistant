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

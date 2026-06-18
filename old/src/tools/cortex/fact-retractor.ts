/**
 * StackOwl — Element 7 T17 — FactRetractor (FPC retraction wiring)
 *
 * Listens for `fact:retracted` events and reacts by:
 *   1. Marking the FactEnvelopeStore entry as retracted (flag flip,
 *      so any downstream verifier asking via `getActive()` excludes it).
 *   2. Dropping the matching short-term layer from the ContextPipeline so
 *      the retracted fact is not rendered into the next prompt.
 *
 * Anyone holding the FactEnvelopeStore can trigger retraction — typically
 * a downstream verifier that reviews an upstream fact's source.
 */
import type { GatewayEventBus } from "../../gateway/event-bus.js";
import type { FactEnvelopeStore } from "./fact-envelope.js";
import { log } from "../../logger.js";

export interface RetractablePipeline {
  removeShortTermLayer(key: string): boolean;
}

/**
 * Convention for keying fact-derived short-term layers in ContextPipeline.
 * Producers writing facts into the pipeline must use this exact key so
 * retraction can address them later.
 */
export function factShortTermKey(sessionId: string, turnIndex: number): string {
  return `fact:${sessionId}:${turnIndex}`;
}

export class FactRetractor {
  constructor(
    bus: GatewayEventBus,
    private readonly store: FactEnvelopeStore,
    private readonly pipeline?: RetractablePipeline,
  ) {
    bus.on("fact:retracted", (e) => {
      log.tool.debug("fact-retractor: fact:retracted event received", {
        sessionId: e.sessionId,
        turnIndex: e.turnIndex,
      });

      const retracted = this.store.retract(e.sessionId, e.turnIndex);

      if (retracted) {
        log.tool.debug("fact-retractor: envelope marked retracted", {
          sessionId: e.sessionId,
          turnIndex: e.turnIndex,
          toolName: retracted.provenance.toolName,
        });
      } else {
        log.tool.debug("fact-retractor: envelope not found in store — noop", {
          sessionId: e.sessionId,
          turnIndex: e.turnIndex,
        });
      }

      const layerKey = factShortTermKey(e.sessionId, e.turnIndex);
      const removed = this.pipeline?.removeShortTermLayer(layerKey) ?? false;

      log.tool.debug("fact-retractor: context pipeline layer removal", {
        layerKey,
        removed,
        hasPipeline: !!this.pipeline,
      });
    });
  }
}

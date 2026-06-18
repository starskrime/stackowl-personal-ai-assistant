/**
 * StackOwl — Element 7 T17 — fact:retracted event + pipeline retraction
 *
 * FactRetractor subscribes to `fact:retracted` events and:
 *   1. Flips the FactEnvelopeStore entry's retracted flag.
 *   2. Drops the matching short-term layer from the ContextPipeline so the
 *      retracted fact does not get rendered into the next prompt.
 *
 * The retraction key convention is `fact:<sessionId>:<turnIndex>`.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import { FactEnvelopeStore } from "../../src/tools/cortex/fact-envelope.js";
import {
  FactRetractor,
  factShortTermKey,
} from "../../src/tools/cortex/fact-retractor.js";

class FakePipeline {
  removed: string[] = [];
  removeShortTermLayer(key: string): boolean {
    this.removed.push(key);
    return true;
  }
}

describe("FactRetractor — fact:retracted wiring", () => {
  let bus: GatewayEventBus;
  let store: FactEnvelopeStore;
  let pipeline: FakePipeline;

  beforeEach(() => {
    bus = new GatewayEventBus();
    store = new FactEnvelopeStore();
    pipeline = new FakePipeline();
    store.record("s1", 4, {
      content: "fact",
      provenance: { toolName: "web", args: {}, durationMs: 10 },
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    new FactRetractor(bus, store, pipeline as any);
  });

  it("flips the FactEnvelopeStore entry's retracted flag", () => {
    bus.emit({
      type: "fact:retracted",
      sessionId: "s1",
      turnIndex: 4,
      toolName: "web",
      reason: "downstream verifier rejected",
    });
    expect(store.get("s1", 4)?.retracted).toBe(true);
  });

  it("removes the corresponding short-term layer from the pipeline", () => {
    bus.emit({
      type: "fact:retracted",
      sessionId: "s1",
      turnIndex: 4,
      toolName: "web",
      reason: "stale",
    });
    expect(pipeline.removed).toEqual([factShortTermKey("s1", 4)]);
  });

  it("does nothing destructive when the entry does not exist", () => {
    bus.emit({
      type: "fact:retracted",
      sessionId: "missing",
      turnIndex: 99,
      toolName: "web",
      reason: "n/a",
    });
    // No envelope to flip; pipeline removal is still attempted (idempotent).
    expect(store.get("missing", 99)).toBeNull();
    expect(pipeline.removed).toEqual([factShortTermKey("missing", 99)]);
  });

  it("works without a pipeline (store-only retraction)", () => {
    const bus2 = new GatewayEventBus();
    const store2 = new FactEnvelopeStore();
    store2.record("s2", 0, {
      content: "x",
      provenance: { toolName: "memory", args: {}, durationMs: 1 },
    });
    new FactRetractor(bus2, store2);
    bus2.emit({
      type: "fact:retracted",
      sessionId: "s2",
      turnIndex: 0,
      toolName: "memory",
      reason: "user disagreed",
    });
    expect(store2.get("s2", 0)?.retracted).toBe(true);
  });
});

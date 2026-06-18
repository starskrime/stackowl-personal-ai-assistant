import { describe, it, expect, vi, beforeEach } from "vitest";
import { ProgressManager } from "../../src/progress/manager.js";
import type { ProgressNotifier } from "../../src/progress/types.js";
import type { GatewayEventBus } from "../../src/gateway/event-bus.js";

type BusHandler = (e: { type: string; [k: string]: unknown }) => void;

function makeEventBus() {
  const handlers = new Map<string, BusHandler[]>();
  return {
    on(type: string, handler: BusHandler) {
      if (!handlers.has(type)) handlers.set(type, []);
      handlers.get(type)!.push(handler);
    },
    trigger(event: { type: string; [k: string]: unknown }) {
      for (const h of handlers.get(event.type) ?? []) h(event);
    },
  } as unknown as GatewayEventBus & { trigger: (e: { type: string; [k: string]: unknown }) => void };
}

function makeNotifier(): ProgressNotifier & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async start(phrase: string, turnId: string) { calls.push(`start:${turnId}:${phrase}`); },
    async update(text: string, turnId: string) { calls.push(`update:${turnId}:${text}`); },
    async stop(turnId: string) { calls.push(`stop:${turnId}`); },
  };
}

describe("ProgressManager", () => {
  let bus: ReturnType<typeof makeEventBus>;
  let manager: ProgressManager;

  beforeEach(() => {
    bus = makeEventBus();
    manager = new ProgressManager(bus as unknown as GatewayEventBus);
  });

  it("fans out notifyStart to all registered notifiers", async () => {
    const a = makeNotifier();
    const b = makeNotifier();
    manager.register(a);
    manager.register(b);
    await manager.notifyStart("Working on it...", "turn-1");
    expect(a.calls).toEqual(["start:turn-1:Working on it..."]);
    expect(b.calls).toEqual(["start:turn-1:Working on it..."]);
  });

  it("fans out notifyStop to all registered notifiers", async () => {
    const a = makeNotifier();
    manager.register(a);
    await manager.notifyStop("turn-1");
    expect(a.calls).toEqual(["stop:turn-1"]);
  });

  it("fans out tool:start events as update() calls", async () => {
    const a = makeNotifier();
    manager.register(a);
    bus.trigger({ type: "tool:start", toolName: "shell", args: {}, turnId: "turn-1" });
    await new Promise((r) => setImmediate(r)); // flush async
    expect(a.calls[0]).toMatch(/^update:turn-1:/);
    expect(a.calls[0]).toContain("turn-1");
  });

  it("does not fan out to unregistered notifiers", async () => {
    const a = makeNotifier();
    manager.register(a);
    manager.unregister(a);
    await manager.notifyStart("phrase", "turn-1");
    expect(a.calls).toHaveLength(0);
  });

  it("tool:start events with any turnId are fanned out to all notifiers", async () => {
    const a = makeNotifier();
    manager.register(a);
    bus.trigger({ type: "tool:start", toolName: "web_fetch", args: {}, turnId: "turn-X" });
    await new Promise((r) => setImmediate(r));
    expect(a.calls.some((c) => c.includes("turn-X"))).toBe(true);
  });

  it("a throwing notifier does not prevent others from receiving events", async () => {
    const bad = makeNotifier();
    bad.start = async () => { throw new Error("boom"); };
    const good = makeNotifier();
    manager.register(bad);
    manager.register(good);
    await manager.notifyStart("phrase", "turn-1");
    expect(good.calls).toEqual(["start:turn-1:phrase"]);
  });
});

import { describe, it, expect, vi } from "vitest";
import { ProactiveDeliveryService } from "../../src/gateway/proactive-delivery-service.js";

const makeAdapter = () => ({
  id: "telegram",
  sendToUser: vi.fn().mockResolvedValue(undefined),
  broadcast: vi.fn().mockResolvedValue(undefined),
});

describe("ProactiveDeliveryService", () => {
  it("records last seen user per session", () => {
    const svc = new ProactiveDeliveryService({} as any);
    svc.recordActivity("s1", "telegram", "u1");
    expect(svc.getLastActivity("s1")).toEqual({ channelId: "telegram", userId: "u1" });
  });

  it("different sessions track independently", () => {
    const svc = new ProactiveDeliveryService({} as any);
    svc.recordActivity("s1", "telegram", "u1");
    svc.recordActivity("s2", "cli", "local");
    expect(svc.getLastActivity("s1")).toEqual({ channelId: "telegram", userId: "u1" });
    expect(svc.getLastActivity("s2")).toEqual({ channelId: "cli", userId: "local" });
  });

  it("deliver calls adapter.sendToUser with correct args", async () => {
    const adapter = makeAdapter();
    const adapters = new Map([["telegram", adapter]]);
    const svc = new ProactiveDeliveryService({ adapters, owl: { persona: { name: "owl", emoji: "🦉" } } } as any);
    await svc.deliver("telegram", "u1", "hello");
    expect(adapter.sendToUser).toHaveBeenCalledWith("u1", expect.objectContaining({ content: "hello" }));
  });

  it("deliver is no-op when adapter not found", async () => {
    const svc = new ProactiveDeliveryService({ adapters: new Map(), owl: { persona: { name: "owl", emoji: "🦉" } } } as any);
    // Must not throw
    await svc.deliver("missing-channel", "u1", "hello");
  });

  it("deliverScheduled calls deliver for each ready message", async () => {
    const adapter = makeAdapter();
    const adapters = new Map([["telegram", adapter]]);
    const svc = new ProactiveDeliveryService({ adapters, owl: { persona: { name: "owl", emoji: "🦉" } } } as any);
    svc.recordActivity("s1", "telegram", "u1");

    const getReadyMessages = vi.fn().mockReturnValue([
      { id: "t1", message: "reminder", channelId: "telegram", userId: "u1" },
    ]);
    await svc.deliverScheduled(getReadyMessages);
    expect(adapter.sendToUser).toHaveBeenCalledWith("u1", expect.objectContaining({ content: "reminder" }));
  });

  it("deliverScheduled uses last-activity fallback when message has no channelId", async () => {
    const adapter = makeAdapter();
    const adapters = new Map([["cli", adapter]]);
    const svc = new ProactiveDeliveryService({ adapters, owl: { persona: { name: "owl", emoji: "🦉" } } } as any);
    svc.recordActivity("s1", "cli", "local");

    const getReadyMessages = vi.fn().mockReturnValue([
      { id: "t2", message: "no-channel", channelId: null, userId: null },
    ]);
    await svc.deliverScheduled(getReadyMessages);
    expect(adapter.sendToUser).toHaveBeenCalledWith("local", expect.objectContaining({ content: "no-channel" }));
  });
});

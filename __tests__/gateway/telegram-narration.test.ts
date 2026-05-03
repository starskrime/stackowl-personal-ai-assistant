import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import { subscribeTelegramNarration } from "../../src/gateway/adapters/telegram.js";

describe("Telegram narration", () => {
  it("calls send on tool:start", async () => {
    const bus = new GatewayEventBus();
    const send = vi.fn();
    subscribeTelegramNarration(bus, { send, chatId: "123" });
    bus.emit({
      type: "tool:start",
      toolName: "web",
      args: { action: "search", query: "x" },
      turnId: "t1",
    });
    await new Promise((r) => setImmediate(r));
    expect(send).toHaveBeenCalled();
    expect(String(send.mock.calls[0][0])).toContain("Searching");
  });

  it("calls send on tool:goal_blocked", async () => {
    const bus = new GatewayEventBus();
    const send = vi.fn();
    subscribeTelegramNarration(bus, { send, chatId: "123" });
    bus.emit({
      type: "tool:goal_blocked",
      toolName: "web",
      subGoal: "find news",
      suggestion: "try memory_search",
    });
    await new Promise((r) => setImmediate(r));
    expect(send).toHaveBeenCalled();
  });

  it("throttles to one message per 1.5s", async () => {
    const bus = new GatewayEventBus();
    const send = vi.fn();
    subscribeTelegramNarration(bus, { send, chatId: "123" });
    bus.emit({ type: "tool:start", toolName: "web", args: { action: "search", query: "a" }, turnId: "t1" });
    bus.emit({ type: "tool:start", toolName: "web", args: { action: "search", query: "b" }, turnId: "t1" });
    bus.emit({ type: "tool:start", toolName: "web", args: { action: "search", query: "c" }, turnId: "t1" });
    await new Promise((r) => setImmediate(r));
    expect(send).toHaveBeenCalledTimes(1);
  });
});

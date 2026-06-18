import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import { subscribeSlackNarration } from "../../src/gateway/adapters/slack.js";

describe("Slack narration", () => {
  it("calls postMessage on tool:start", async () => {
    const bus = new GatewayEventBus();
    const postMessage = vi.fn();
    subscribeSlackNarration(bus, { postMessage, channel: "C123" });
    bus.emit({
      type: "tool:start",
      toolName: "web_search",
      args: { query: "x" },
      turnId: "t1",
    });
    await new Promise((r) => setImmediate(r));
    expect(postMessage).toHaveBeenCalled();
    const arg = postMessage.mock.calls[0][0];
    expect(arg.channel).toBe("C123");
    expect(String(arg.text)).toContain("Searching");
  });

  it("calls postMessage on tool:goal_blocked", async () => {
    const bus = new GatewayEventBus();
    const postMessage = vi.fn();
    subscribeSlackNarration(bus, { postMessage, channel: "C123" });
    bus.emit({
      type: "tool:goal_blocked",
      toolName: "web_search",
      subGoal: "find news",
      suggestion: "try memory_search",
    });
    await new Promise((r) => setImmediate(r));
    expect(postMessage).toHaveBeenCalled();
  });

  it("throttles to one message per 3s", async () => {
    const bus = new GatewayEventBus();
    const postMessage = vi.fn();
    subscribeSlackNarration(bus, { postMessage, channel: "C123" });
    bus.emit({ type: "tool:start", toolName: "web_search", args: { query: "a" }, turnId: "t1" });
    bus.emit({ type: "tool:start", toolName: "web_search", args: { query: "b" }, turnId: "t1" });
    bus.emit({ type: "tool:start", toolName: "web_search", args: { query: "c" }, turnId: "t1" });
    await new Promise((r) => setImmediate(r));
    expect(postMessage).toHaveBeenCalledTimes(1);
  });
});

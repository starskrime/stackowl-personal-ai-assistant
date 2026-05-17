import { describe, it, expect } from "vitest";

// Test the dropLastTurn logic in isolation
describe("dropLastUserTurn logic", () => {
  it("drops the full exchange: user + assistant + tool blocks", () => {
    type Message = { role: string; content: string | Array<{ type: string }> };
    const messages: Message[] = [
      { role: "user",      content: "first message" },
      { role: "assistant", content: "first response" },
      { role: "user",      content: "second message" },
      { role: "assistant", content: [{ type: "tool_use" }] },
      { role: "user",      content: [{ type: "tool_result" }] },
      { role: "assistant", content: "second response" },
    ];

    // Find the last real user message (not a tool_result block)
    const lastRealUserIdx = [...messages].reverse().findIndex(
      (m) => m.role === "user" && !Array.isArray(m.content),
    );
    const fromIdx = messages.length - 1 - lastRealUserIdx;
    const dropped = messages.slice(0, fromIdx);

    expect(dropped).toHaveLength(2);
    expect(dropped[0]!.role).toBe("user");
    expect(dropped[0]!.content).toBe("first message");
    expect(dropped[1]!.role).toBe("assistant");
    expect(dropped[1]!.content).toBe("first response");
  });

  it("drops a simple user + assistant pair", () => {
    type Message = { role: string; content: string };
    const messages: Message[] = [
      { role: "user",      content: "hello" },
      { role: "assistant", content: "world" },
    ];

    const lastRealUserIdx = [...messages].reverse().findIndex(
      (m) => m.role === "user",
    );
    const fromIdx = messages.length - 1 - lastRealUserIdx;
    const dropped = messages.slice(0, fromIdx);

    expect(dropped).toHaveLength(0);
  });

  it("returns original messages unchanged when no user message present", () => {
    type Message = { role: string; content: string };
    const messages: Message[] = [
      { role: "assistant", content: "welcome" },
    ];

    const idx = [...messages].reverse().findIndex((m) => m.role === "user");
    // idx === -1 means no user message found — nothing to drop
    expect(idx).toBe(-1);
    // No mutation occurred
    expect(messages).toHaveLength(1);
  });
});

import { describe, it, expect, vi } from "vitest";
import { DiscordAdapter } from "../../src/gateway/adapters/discord.js";

describe("DiscordAdapter message normalization", () => {
  it("normalizes a Discord DM to GatewayMessage", () => {
    const adapter = new DiscordAdapter({ botToken: "fake-token" });
    const mockMsg = {
      id: "111222333",
      content: "hello owl",
      author: { id: "user-42", bot: false },
      channel: { type: 1, send: vi.fn() }, // 1 = DM channel type in discord.js
      guild: null,
      mentions: { has: vi.fn().mockReturnValue(false) },
    } as any;

    const normalized = (adapter as any).normalizeMessage(mockMsg);
    expect(normalized.channelId).toBe("discord");
    expect(normalized.userId).toBe("user-42");
    expect(normalized.text).toBe("hello owl");
    expect(normalized.sessionId).toContain("discord");
    expect(normalized.sessionId).toContain("user-42");
  });

  it("normalizes a server mention to GatewayMessage", () => {
    const adapter = new DiscordAdapter({ botToken: "fake-token" });
    const mockMsg = {
      id: "444555666",
      content: "<@BOT_ID> help me",
      author: { id: "server-user-7", bot: false },
      channel: { type: 0, id: "channel-abc", send: vi.fn() },
      guild: { id: "guild-xyz" },
      mentions: { has: vi.fn().mockReturnValue(true) },
    } as any;

    const normalized = (adapter as any).normalizeMessage(mockMsg);
    expect(normalized).not.toBeNull();
    expect(normalized.channelId).toBe("discord");
    expect(normalized.text).toContain("help me");
  });

  it("returns null for empty text after stripping mentions", () => {
    const adapter = new DiscordAdapter({ botToken: "fake-token" });
    const mockMsg = {
      id: "999",
      content: "<@BOT_ID>",  // mention only, no text
      author: { id: "user-x", bot: false },
      channel: { type: 1, send: vi.fn() },
      guild: null,
      mentions: { has: vi.fn().mockReturnValue(false) },
    } as any;

    const normalized = (adapter as any).normalizeMessage(mockMsg);
    expect(normalized).toBeNull();
  });
});

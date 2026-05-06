import { describe, it, expect } from "vitest";
import { formatToolEvent } from "../../src/gateway/narration-formatter.js";

const baseEvent = (toolName: string, args: Record<string, unknown> = {}) => ({
  type: "tool:start" as const,
  toolName,
  args,
  toolCallId: "id",
  turnId: "turn",
  channel: "cli" as const,
  timestamp: Date.now(),
});

describe("narration-formatter — Element 16c", () => {
  it("recognises web_fetch", () => {
    const out = formatToolEvent(baseEvent("web_fetch", { url: "https://x" }) as any);
    expect(out).toMatch(/Fetching https:\/\/x/);
  });

  it("recognises web_search", () => {
    const out = formatToolEvent(baseEvent("web_search", { query: "q" }) as any);
    expect(out).toMatch(/Searching the web for "q"/);
  });

  it("does NOT special-case the deleted 'web' umbrella tool", () => {
    const out = formatToolEvent(baseEvent("web", { action: "fetch", url: "https://x" }) as any);
    expect(out).toBe("Using web…"); // generic fallback
  });

  it("does NOT special-case camofox (no longer LLM-visible)", () => {
    const out = formatToolEvent(baseEvent("camofox", { action: "navigate", url: "https://x" }) as any);
    expect(out).toBe("Using camofox…"); // generic fallback
  });
});

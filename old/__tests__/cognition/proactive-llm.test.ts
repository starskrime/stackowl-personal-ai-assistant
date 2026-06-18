import { describe, it, expect, vi } from "vitest";
import type { ModelProvider } from "../../src/providers/base.js";
import type { SuggestionContext } from "../../src/oscar/cognition/proactive.js";

const MOCK_CONTEXT: SuggestionContext = {
  currentApp: "Slack",
  recentActions: ["navigate"],
  timeOfDay: 14,
  dayOfWeek: 2,
};

describe("ProactiveAssistant with ModelProvider", () => {
  it("calls provider.chat and returns LLM suggestion", async () => {
    const { ProactiveAssistant } = await import("../../src/oscar/cognition/proactive.js");

    const fakeProvider: Partial<ModelProvider> = {
      chat: vi.fn().mockResolvedValue({
        content: "You have 3 unread Slack threads from this morning.",
      }),
    };

    const assistant = new ProactiveAssistant(fakeProvider as ModelProvider);
    const suggestions = await assistant.suggest(MOCK_CONTEXT);

    expect(fakeProvider.chat).toHaveBeenCalledOnce();
    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].message).toBe("You have 3 unread Slack threads from this morning.");
    expect(suggestions[0].confidence).toBeGreaterThan(0.6);
  });

  it("caches responses for 5 minutes — second call within TTL skips provider", async () => {
    vi.useFakeTimers();
    const { ProactiveAssistant } = await import("../../src/oscar/cognition/proactive.js");

    const fakeProvider: Partial<ModelProvider> = {
      chat: vi.fn().mockResolvedValue({ content: "Focus on your open PRs." }),
    };

    const assistant = new ProactiveAssistant(fakeProvider as ModelProvider);
    await assistant.suggest(MOCK_CONTEXT);
    await assistant.suggest(MOCK_CONTEXT); // same context, within TTL

    expect(fakeProvider.chat).toHaveBeenCalledOnce(); // not twice

    vi.advanceTimersByTime(6 * 60 * 1000); // advance past 5-min TTL
    await assistant.suggest(MOCK_CONTEXT);

    expect(fakeProvider.chat).toHaveBeenCalledTimes(2); // now refreshed
    vi.useRealTimers();
  });

  it("falls back to empty array when provider throws", async () => {
    const { ProactiveAssistant } = await import("../../src/oscar/cognition/proactive.js");

    const fakeProvider: Partial<ModelProvider> = {
      chat: vi.fn().mockRejectedValue(new Error("rate limited")),
    };

    const assistant = new ProactiveAssistant(fakeProvider as ModelProvider);
    const suggestions = await assistant.suggest(MOCK_CONTEXT);

    expect(suggestions).toHaveLength(0);
  });

  it("works without provider — returns empty array (no crash)", async () => {
    const { ProactiveAssistant } = await import("../../src/oscar/cognition/proactive.js");
    const assistant = new ProactiveAssistant();
    const suggestions = await assistant.suggest(MOCK_CONTEXT);
    expect(Array.isArray(suggestions)).toBe(true);
  });
});

import { describe, it, expect, vi } from "vitest";

describe("EventBasedPelletGenerator — router classification", () => {
  it("generates pellet when router classifies as decision", async () => {
    const { EventBasedPelletGenerator } = await import("../../src/pellets/event-based-generator.js");
    const mockRouter = {
      resolve: vi.fn().mockResolvedValue('{"isDecision":true,"isInsight":false,"isCorrection":false}'),
    };
    const mockStore = { save: vi.fn().mockResolvedValue({ verdict: "CREATE" }) };
    const mockGenerator = {
      generate: vi.fn().mockResolvedValue({
        id: "p1", title: "T", content: "C", tags: [], owls: [],
        source: "s", generatedAt: new Date().toISOString(), version: 1,
        successCount: 0, failureCount: 0, provenance: [],
      }),
    };
    const mockEventBus = { on: vi.fn(), off: vi.fn() };

    const gen = new (EventBasedPelletGenerator as any)(mockEventBus, mockStore, mockRouter);
    gen.generator = mockGenerator;

    await gen.handleMessageResponded({
      sessionId: "s1", channelId: "c", userId: "u",
      content: "we decided to use Postgres for the project",
      owlName: "Noctua", toolsUsed: ["web"],
    });

    expect(mockRouter.resolve).toHaveBeenCalledWith("classification", expect.stringContaining("Classify"));
    expect(mockStore.save).toHaveBeenCalled();
  });

  it("does not generate pellet when router returns all false", async () => {
    const { EventBasedPelletGenerator } = await import("../../src/pellets/event-based-generator.js");
    const mockRouter = { resolve: vi.fn().mockResolvedValue('{"isDecision":false,"isInsight":false,"isCorrection":false}') };
    const mockStore = { save: vi.fn() };
    const mockEventBus = { on: vi.fn(), off: vi.fn() };
    const gen = new (EventBasedPelletGenerator as any)(mockEventBus, mockStore, mockRouter);
    gen.generator = { generate: vi.fn().mockResolvedValue(null) };

    await gen.handleMessageResponded({
      sessionId: "s1", channelId: "c", userId: "u",
      content: "some message with no decision",
      owlName: "Noctua", toolsUsed: ["web"],
    });
    expect(mockStore.save).not.toHaveBeenCalled();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import type { EpisodicMemory } from "../../src/memory/episodic.js";

// Minimal BackgroundOrchestrator interface needed for this test
async function makeOrchestrator(episodicMemory?: Partial<EpisodicMemory>) {
  const { BackgroundOrchestrator } = await import("../../src/background/orchestrator.js");
  const fakeProvider = { chat: vi.fn().mockResolvedValue({ content: "ping" }) } as any;
  const fakeOwl = { persona: { name: "Athena" } } as any;
  return new BackgroundOrchestrator(
    fakeProvider,
    fakeOwl,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    episodicMemory as EpisodicMemory,
  );
}

describe("BackgroundOrchestrator.runMemoryConsolidation", () => {
  it("calls runDecay and logs result when episodicMemory is provided", async () => {
    const runDecay = vi.fn().mockReturnValue({ compressed: 3, archived: 1 });
    const save = vi.fn().mockResolvedValue(undefined);
    const orch = await makeOrchestrator({ runDecay, save } as any);

    // Access private method via cast
    await (orch as any).runMemoryConsolidation();

    expect(runDecay).toHaveBeenCalledOnce();
    expect(save).toHaveBeenCalledOnce();
  });

  it("is a no-op when episodicMemory is not provided", async () => {
    const orch = await makeOrchestrator(undefined);
    // Must not throw
    await expect((orch as any).runMemoryConsolidation()).resolves.toBeUndefined();
  });
});

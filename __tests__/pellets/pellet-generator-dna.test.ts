import { describe, it, expect, vi } from "vitest";
import { updatePelletGeneratorDNA } from "../../src/owls/evolution.js";

const makeOwl = (name: string, expertiseGrowth: Record<string, number> = {}) => ({
  persona: { name },
  dna: { expertiseGrowth: { ...expertiseGrowth }, evolvedTraits: {}, learnedPreferences: {} },
});

describe("updatePelletGeneratorDNA", () => {
  it("increments expertiseGrowth for the topic", async () => {
    const owl = makeOwl("Noctua", { api: 0.5 });
    const mockRegistry = {
      listOwls: vi.fn().mockReturnValue([owl]),
      saveDNA: vi.fn().mockResolvedValue(undefined),
    };
    await updatePelletGeneratorDNA(["Noctua"], "api", mockRegistry as any);
    expect(owl.dna.expertiseGrowth["api"]).toBeCloseTo(0.53, 2);
    expect(mockRegistry.saveDNA).toHaveBeenCalledWith("Noctua");
  });

  it("clamps expertiseGrowth to max 0.9", async () => {
    const owl = makeOwl("Archimedes", { api: 0.89 });
    const mockRegistry = {
      listOwls: vi.fn().mockReturnValue([owl]),
      saveDNA: vi.fn().mockResolvedValue(undefined),
    };
    await updatePelletGeneratorDNA(["Archimedes"], "api", mockRegistry as any);
    expect(owl.dna.expertiseGrowth["api"]).toBe(0.9);
  });

  it("updates multiple owls", async () => {
    const owlA = makeOwl("A");
    const owlB = makeOwl("B");
    const mockRegistry = {
      listOwls: vi.fn().mockReturnValue([owlA, owlB]),
      saveDNA: vi.fn().mockResolvedValue(undefined),
    };
    await updatePelletGeneratorDNA(["A", "B"], "security", mockRegistry as any);
    expect(owlA.dna.expertiseGrowth["security"]).toBeCloseTo(0.53, 2);
    expect(owlB.dna.expertiseGrowth["security"]).toBeCloseTo(0.53, 2);
    expect(mockRegistry.saveDNA).toHaveBeenCalledTimes(2);
  });

  it("missing owl is a graceful no-op", async () => {
    const mockRegistry = {
      listOwls: vi.fn().mockReturnValue([]),
      saveDNA: vi.fn(),
    };
    await expect(
      updatePelletGeneratorDNA(["Ghost"], "api", mockRegistry as any)
    ).resolves.not.toThrow();
    expect(mockRegistry.saveDNA).not.toHaveBeenCalled();
  });
});

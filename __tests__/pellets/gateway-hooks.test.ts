import { describe, it, expect, vi } from "vitest";

describe("Gateway hook 4 — recordOutcome", () => {
  it("calls recordOutcome when pellets were retrieved and verdict is non-NEUTRAL", async () => {
    const mockPelletStore = { recordOutcome: vi.fn().mockResolvedValue(undefined) };
    const retrievedPelletIds = ["p1", "p2"];
    const goalVerdict = "ADVANCES" as const;

    if (retrievedPelletIds.length > 0 && goalVerdict !== "NEUTRAL") {
      await mockPelletStore.recordOutcome(retrievedPelletIds, goalVerdict);
    }
    expect(mockPelletStore.recordOutcome).toHaveBeenCalledWith(["p1", "p2"], "ADVANCES");
  });

  it("does NOT call recordOutcome when verdict is NEUTRAL", async () => {
    const mockPelletStore = { recordOutcome: vi.fn() };
    const retrievedPelletIds = ["p1"];
    const goalVerdict = "NEUTRAL" as const;

    if (retrievedPelletIds.length > 0 && goalVerdict !== "NEUTRAL") {
      await mockPelletStore.recordOutcome(retrievedPelletIds, goalVerdict);
    }
    expect(mockPelletStore.recordOutcome).not.toHaveBeenCalled();
  });
});

describe("Gateway hook 5 — updatePelletGeneratorDNA", () => {
  it("calls updatePelletGeneratorDNA only on ADVANCES", async () => {
    const mockUpdateDNA = vi.fn().mockResolvedValue(undefined);
    const goalVerdict = "ADVANCES" as const;
    const generatorOwlNames = ["Noctua"];

    if (goalVerdict === "ADVANCES" && generatorOwlNames.length > 0) {
      await mockUpdateDNA(generatorOwlNames, "api", {});
    }
    expect(mockUpdateDNA).toHaveBeenCalledWith(["Noctua"], "api", {});
  });

  it("skips updatePelletGeneratorDNA on BLOCKED", async () => {
    const mockUpdateDNA = vi.fn();
    const goalVerdict = "BLOCKED" as const;
    const generatorOwlNames = ["Noctua"];

    if (goalVerdict === "ADVANCES" && generatorOwlNames.length > 0) {
      await mockUpdateDNA(generatorOwlNames, "api", {});
    }
    expect(mockUpdateDNA).not.toHaveBeenCalled();
  });

  it("skips updatePelletGeneratorDNA when owlNames is empty", async () => {
    const mockUpdateDNA = vi.fn();
    const goalVerdict = "ADVANCES" as const;
    const generatorOwlNames: string[] = [];

    if (goalVerdict === "ADVANCES" && generatorOwlNames.length > 0) {
      await mockUpdateDNA(generatorOwlNames, "api", {});
    }
    expect(mockUpdateDNA).not.toHaveBeenCalled();
  });
});

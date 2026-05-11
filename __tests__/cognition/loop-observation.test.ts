import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

describe("CognitiveLoop.captureObservation", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("uses focused app from macOS adapter when available", async () => {
    // Stub macOSAdapter before importing the module under test
    vi.doMock("../../src/oscar/platform/adapters/macos.js", () => ({
      macOSAdapter: {
        getFocusedApp: vi.fn().mockResolvedValue("Slack"),
      },
    }));

    const { CognitiveLoop } = await import("../../src/oscar/cognition/loop.js");
    const loop = new CognitiveLoop();
    const obs = await (loop as any).captureObservation();

    expect(obs.app).toBe("Slack");
    expect(obs.timestamp).toBeGreaterThan(0);
  });

  it("falls back to null app when macOS adapter fails", async () => {
    vi.doMock("../../src/oscar/platform/adapters/macos.js", () => ({
      macOSAdapter: {
        getFocusedApp: vi.fn().mockRejectedValue(new Error("osascript unavailable")),
      },
    }));

    const { CognitiveLoop } = await import("../../src/oscar/cognition/loop.js");
    const loop = new CognitiveLoop();
    const obs = await (loop as any).captureObservation();

    expect(obs.app).toBeNull();
  });
});

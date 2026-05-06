import { describe, it, expect } from "vitest";
import { MicroLearner } from "../src/learning/micro-learner.js";

describe("MicroLearner — style and temporal signal emission", () => {
  it("emits at least one style signal and one temporal signal per message", async () => {
    const learner = new MicroLearner("/tmp");
    const signals = await learner.processMessage("How do I set up TypeScript?");
    const types = signals.map((s: any) => s.type);
    expect(types).toContain("style");
    expect(types).toContain("temporal");
  });

  it("verbosity value is <= 1.0 for any message length", async () => {
    const learner = new MicroLearner("/tmp");
    // Very long message
    const longMsg = "word ".repeat(200);
    const signals = await learner.processMessage(longMsg);
    const verbosity = signals.find((s: any) => s.key === "verbosity");
    expect(verbosity).toBeDefined();
    expect(verbosity!.value).toBeLessThanOrEqual(1.0);
  });

  it("temporal signal has key 'hour' and value in [0, 1]", async () => {
    const learner = new MicroLearner("/tmp");
    const signals = await learner.processMessage("run the build");
    const temporal = signals.find((s: any) => s.type === "temporal" && s.key === "hour");
    expect(temporal).toBeDefined();
    expect(temporal!.value).toBeGreaterThanOrEqual(0);
    expect(temporal!.value).toBeLessThanOrEqual(1);
  });
});

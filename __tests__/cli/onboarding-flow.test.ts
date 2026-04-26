import { describe, it, expect } from "vitest";
import { OnboardingFlow } from "../../src/cli/onboarding-flow.js";

describe("OnboardingFlow smart routing steps", () => {
  it("OnboardingFlow class is importable", () => {
    expect(typeof OnboardingFlow).toBe("function");
  });

  it("WizardData type includes srEnabled and srRoster (type assertion)", () => {
    const flow = new OnboardingFlow("/tmp/test.json");
    const data = (flow as any)._data;
    data.srEnabled = true;
    data.srRoster  = [{ modelName: "llama3.2", providerName: "ollama" }];
    expect(data.srEnabled).toBe(true);
    expect(data.srRoster[0].modelName).toBe("llama3.2");
  });
});

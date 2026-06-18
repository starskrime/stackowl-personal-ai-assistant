// __tests__/context/predictive-layer.test.ts
import { describe, it, expect } from "vitest";
import { PredictiveContextLayer } from "../../src/context/layers/predictive.js";

function makeReq(predictiveQueue?: any) {
  return {
    session: { messages: [] },
    callbacks: {},
    continuityResult: null,
    digest: null,
    deps: { sessionStore: {} as any, config: {} as any, predictiveQueue },
  } as any;
}

const triage = {
  userMessage: "what should I do next?",
  isConversational: false,
  hasFrustration: false,
  isOpinionRequest: false,
  hasTemporalTrigger: false,
  isReturningUser: false,
  sessionDepth: 3,
  hasActiveItems: false,
  effectiveUserId: "u1",
  continuityClass: null,
} as any;

describe("PredictiveContextLayer", () => {
  it("returns empty string when deps.predictiveQueue is absent", async () => {
    const layer = new PredictiveContextLayer();
    expect(await layer.build(makeReq(undefined), triage, new Map())).toBe("");
  });

  it("returns empty string when no ready tasks", async () => {
    const layer = new PredictiveContextLayer();
    const mockQueue = { getReadyTasks: () => [] };
    expect(await layer.build(makeReq(mockQueue), triage, new Map())).toBe("");
  });

  it("returns <predicted_next> block with up to 3 tasks sorted by confidence", async () => {
    const layer = new PredictiveContextLayer();
    const mockQueue = {
      getReadyTasks: () => [
        { action: "Check calendar", confidence: 0.9, status: "ready" },
        { action: "Review PRs", confidence: 0.7, status: "ready" },
        { action: "Send standup", confidence: 0.8, status: "ready" },
        { action: "Low priority task", confidence: 0.5, status: "ready" },
      ],
    };
    const result = await layer.build(makeReq(mockQueue), triage, new Map());
    expect(result).toContain("<predicted_next>");
    expect(result).toContain("Check calendar");
    expect(result).toContain('confidence="0.9"');
    // Only top 3
    const taskCount = (result.match(/<task /g) ?? []).length;
    expect(taskCount).toBe(3);
    expect(result).not.toContain("Low priority task");
    expect(result).toContain("</predicted_next>");
  });

  it("shouldFire returns false for conversational messages", () => {
    const layer = new PredictiveContextLayer();
    expect(layer.shouldFire({ ...triage, isConversational: true })).toBe(false);
  });
});

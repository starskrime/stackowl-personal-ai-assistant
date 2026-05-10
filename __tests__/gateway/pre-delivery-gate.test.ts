import { describe, it, expect, vi } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: {
    gateway: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

import { runPreDeliveryGate } from "../../src/gateway/pre-delivery-gate.js";
import { buildDegradationMessage } from "../../src/gateway/messages/graceful-degradation.js";
import type { EngineResponse } from "../../src/engine/runtime.js";

function mockResponse(content: string, toolsUsed: string[] = []): EngineResponse {
  return {
    content,
    owlName: "Archimedes",
    owlEmoji: "🦉",
    challenged: false,
    toolsUsed,
    modelUsed: "test",
    newMessages: [],
    usage: undefined,
    pendingFiles: [],
  } as any;
}

function mockProvider() {
  return {
    chat: vi.fn().mockResolvedValue({
      content:
        '{"isDone":false,"hasEvidence":false,"evidenceTypes":[],"confidence":0.8,"concerns":"no content"}',
    }),
  } as any;
}

describe("runPreDeliveryGate", () => {
  it("returns original response unchanged when content is non-empty", async () => {
    const response = mockResponse("Hello there");
    const correctionRun = vi.fn();
    const result = await runPreDeliveryGate(response, {
      provider: mockProvider(),
      userIntent: "Tell me something",
      owlName: "Archimedes",
      owlEmoji: "🦉",
      sessionId: "sess-1",
      correctionRun,
    });

    expect(result).toBe(response);
    expect(correctionRun).not.toHaveBeenCalled();
  });

  it("returns corrected response when correction run succeeds", async () => {
    const response = mockResponse("");
    const correctedResponse = mockResponse("Here is the answer");
    const correctionRun = vi.fn().mockResolvedValue(correctedResponse);

    const result = await runPreDeliveryGate(response, {
      provider: mockProvider(),
      userIntent: "Tell me something",
      owlName: "Archimedes",
      owlEmoji: "🦉",
      sessionId: "sess-2",
      correctionRun,
    });

    expect(result).toBe(correctedResponse);
    expect(correctionRun).toHaveBeenCalledOnce();
  });

  it("returns graceful-degradation message when both original and correction are empty", async () => {
    const response = mockResponse("", ["web_search"]);
    const correctionRun = vi.fn().mockResolvedValue(mockResponse(""));

    const result = await runPreDeliveryGate(response, {
      provider: mockProvider(),
      userIntent: "Find me some news",
      owlName: "Archimedes",
      owlEmoji: "🦉",
      sessionId: "sess-3",
      correctionRun,
    });

    expect(result.content).toContain("Archimedes");
    expect(result.content).not.toBe("");
  });

  it("graceful-degradation message contains web_search tool name when in toolsUsed", async () => {
    const response = mockResponse("", ["web_search"]);
    const correctionRun = vi.fn().mockResolvedValue(mockResponse(""));

    const result = await runPreDeliveryGate(response, {
      provider: mockProvider(),
      userIntent: "Find me some news",
      owlName: "Archimedes",
      owlEmoji: "🦉",
      sessionId: "sess-4",
      correctionRun,
    });

    expect(result.content).toContain("web_search");
  });

  it("treats [DONE]-only content as empty and triggers correction", async () => {
    const response = mockResponse("[DONE]");
    const correctedResponse = mockResponse("Real answer here");
    const correctionRun = vi.fn().mockResolvedValue(correctedResponse);

    const result = await runPreDeliveryGate(response, {
      provider: mockProvider(),
      userIntent: "Do something",
      owlName: "Archimedes",
      owlEmoji: "🦉",
      sessionId: "sess-5",
      correctionRun,
    });

    expect(result).toBe(correctedResponse);
    expect(correctionRun).toHaveBeenCalledOnce();
  });
});

describe("buildDegradationMessage", () => {
  it("includes fetch suggestion hint when web_search is in failedTools but not web_fetch", () => {
    const msg = buildDegradationMessage({
      failedTools: ["web_search"],
      userIntent: "What is the weather today?",
      owlName: "Archimedes",
      owlEmoji: "🦉",
    });

    expect(msg).toContain("fetch");
  });

  it("includes browser suggestion when web_fetch is in failedTools but not live_browser", () => {
    const msg = buildDegradationMessage({
      failedTools: ["web_fetch"],
      userIntent: "What is on this page?",
      owlName: "Hestia",
      owlEmoji: "🦅",
    });

    expect(msg).toContain("browser");
  });

  it("includes the owl name in the output", () => {
    const msg = buildDegradationMessage({
      failedTools: [],
      userIntent: "Tell me a story",
      owlName: "Hestia",
      owlEmoji: "🦅",
    });

    expect(msg).toContain("Hestia");
  });

  it("includes the intent snippet in the output", () => {
    const intent = "What is the capital of France?";
    const msg = buildDegradationMessage({
      failedTools: [],
      userIntent: intent,
      owlName: "Archimedes",
    });

    expect(msg).toContain("What is the capital of France?");
  });
});

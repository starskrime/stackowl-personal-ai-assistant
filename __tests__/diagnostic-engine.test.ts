import { describe, it, expect, vi, beforeEach } from "vitest";
import { DiagnosticEngine } from "../src/engine/diagnostic-engine.js";
import type { DiagnosticInput } from "../src/engine/diagnostic-engine.js";
import type { ModelProvider } from "../src/providers/base.js";

// ─── Helpers ──────────────────────────────────────────────────────

function makeMockProvider(responseContent: string): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: responseContent,
      model: "mock-model",
      finishReason: "stop" as const,
    }),
    listModels: vi.fn().mockResolvedValue([]),
  } as unknown as ModelProvider;
}

function makeInput(overrides: Partial<DiagnosticInput> = {}): DiagnosticInput {
  return {
    toolName: "shell",
    toolArgs: { command: "curl https://example.com" },
    toolResult: "curl: command not found",
    failStreak: 1,
    failureType: "soft",
    errorClass: "NON-RETRYABLE",
    recentMessages: [
      { role: "user", content: "Fetch the homepage of example.com" },
    ],
    userIntent: "Fetch the homepage of example.com",
    ...overrides,
  };
}

const VALID_LLM_RESPONSE = JSON.stringify({
  rootCause: "curl is not installed in this environment",
  errorClass: "non-retryable",
  candidates: [
    {
      label: "Use web_crawl tool",
      reasoning:
        "web_crawl is a built-in tool that can fetch URLs without curl",
      action: "Call web_crawl with url='https://example.com'",
      likelihood: 0.9,
      feasibility: 0.95,
      risk: 0.05,
    },
    {
      label: "Use duckduckgo_search",
      reasoning: "Search for the content instead of fetching directly",
      action: "Call duckduckgo_search with query='example.com homepage'",
      likelihood: 0.6,
      feasibility: 0.9,
      risk: 0.1,
    },
    {
      label: "Tell user curl unavailable",
      reasoning:
        "Inform user that curl is not available and suggest alternatives",
      action: "Respond to user explaining the limitation",
      likelihood: 1.0,
      feasibility: 1.0,
      risk: 0.0,
    },
  ],
});

// ─── Tests ────────────────────────────────────────────────────────

describe("DiagnosticEngine", () => {
  let engine: DiagnosticEngine;

  beforeEach(() => {
    engine = new DiagnosticEngine(makeMockProvider(VALID_LLM_RESPONSE));
  });

  it("parses LLM response and returns ranked candidates", async () => {
    const result = await engine.diagnose(makeInput());

    expect(result.rootCause).toBe("curl is not installed in this environment");
    expect(result.errorClass).toBe("non-retryable");
    expect(result.candidates.length).toBe(3);
    // All candidates should have computed scores
    for (const c of result.candidates) {
      expect(c.score).toBeGreaterThan(0);
      expect(c.likelihood).toBeGreaterThanOrEqual(0);
      expect(c.likelihood).toBeLessThanOrEqual(1);
    }
  });

  it("sorts candidates by composite score descending", async () => {
    const result = await engine.diagnose(makeInput());

    for (let i = 1; i < result.candidates.length; i++) {
      expect(result.candidates[i - 1].score).toBeGreaterThanOrEqual(
        result.candidates[i].score,
      );
    }
  });

  it("sets recommended to highest-scored candidate", async () => {
    const result = await engine.diagnose(makeInput());

    expect(result.recommended).toBe(result.candidates[0]);
    expect(result.recommended.score).toBeGreaterThan(0);
  });

  it("calculates score as likelihood * feasibility * (1 - risk)", async () => {
    const result = await engine.diagnose(makeInput());

    for (const c of result.candidates) {
      const expected = c.likelihood * c.feasibility * (1 - c.risk);
      expect(c.score).toBeCloseTo(expected, 5);
    }
  });

  it("generates rejection reasons for non-recommended candidates", async () => {
    const result = await engine.diagnose(makeInput());

    expect(result.rejectionReasons.length).toBe(result.candidates.length);
    // Recommended candidate has empty rejection reason
    const recIdx = result.candidates.indexOf(result.recommended);
    expect(result.rejectionReasons[recIdx]).toBe("");
    // Others have non-empty reasons
    for (let i = 0; i < result.rejectionReasons.length; i++) {
      if (i !== recIdx) {
        expect(result.rejectionReasons[i].length).toBeGreaterThan(0);
      }
    }
  });

  it("falls back to heuristic on LLM failure", async () => {
    const brokenProvider = makeMockProvider("not json at all");
    // Make it throw
    (brokenProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("provider down"),
    );
    engine = new DiagnosticEngine(brokenProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "command not found" }),
    );

    // Should still return a result via heuristic
    expect(result.candidates.length).toBeGreaterThan(0);
    expect(result.recommended).toBeDefined();
    expect(result.rootCause).toContain("Heuristic");
  });

  it("heuristic detects 'command not found' pattern", async () => {
    const brokenProvider = makeMockProvider("");
    (brokenProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    engine = new DiagnosticEngine(brokenProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "bash: curl: command not found" }),
    );

    const labels = result.candidates.map((c) => c.label);
    expect(labels).toContain("Use different tool");
  });

  it("heuristic detects 'file not found' pattern", async () => {
    const brokenProvider = makeMockProvider("");
    (brokenProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    engine = new DiagnosticEngine(brokenProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "ENOENT: no such file or directory" }),
    );

    const labels = result.candidates.map((c) => c.label);
    expect(labels).toContain("Verify path exists");
  });

  it("heuristic boosts 'report to user' after 3 failures", async () => {
    const brokenProvider = makeMockProvider("");
    (brokenProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    engine = new DiagnosticEngine(brokenProvider);

    const result = await engine.diagnose(
      makeInput({ failStreak: 3, toolResult: "some unknown error" }),
    );

    // "Report to user" should be recommended after 3 failures
    expect(result.recommended.label).toBe("Report to user");
  });

  it("formatDirective includes all candidates with scores", async () => {
    const result = await engine.diagnose(makeInput());
    const input = makeInput();
    const directive = engine.formatDirective(result, input);

    expect(directive).toContain("DIAGNOSTIC ANALYSIS");
    expect(directive).toContain("ROOT CAUSE:");
    expect(directive).toContain("CANDIDATE FIXES");
    expect(directive).toContain("RECOMMENDED");
    expect(directive).toContain("rejected");
    expect(directive).toContain("DIRECTIVE: Execute fix");
  });

  it("formatDirective includes critical warning after 3+ failures", async () => {
    const input = makeInput({ failStreak: 3 });
    const result = await engine.diagnose(input);
    const directive = engine.formatDirective(result, input);

    expect(directive).toContain("CRITICAL");
    expect(directive).toContain("STOP and tell the user");
  });

  it("handles LLM returning JSON wrapped in markdown code blocks", async () => {
    const wrappedResponse = "```json\n" + VALID_LLM_RESPONSE + "\n```";
    engine = new DiagnosticEngine(makeMockProvider(wrappedResponse));

    const result = await engine.diagnose(makeInput());
    expect(result.candidates.length).toBe(3);
  });

  it("clamps out-of-range scores to 0-1", async () => {
    const badScores = JSON.stringify({
      rootCause: "test",
      errorClass: "unknown",
      candidates: [
        {
          label: "fix1",
          reasoning: "r",
          action: "a",
          likelihood: 1.5,
          feasibility: -0.3,
          risk: 2.0,
        },
        {
          label: "fix2",
          reasoning: "r",
          action: "a",
          likelihood: 0.5,
          feasibility: 0.5,
          risk: 0.1,
        },
      ],
    });
    engine = new DiagnosticEngine(makeMockProvider(badScores));

    const result = await engine.diagnose(makeInput());
    for (const c of result.candidates) {
      expect(c.likelihood).toBeGreaterThanOrEqual(0);
      expect(c.likelihood).toBeLessThanOrEqual(1);
      expect(c.feasibility).toBeGreaterThanOrEqual(0);
      expect(c.feasibility).toBeLessThanOrEqual(1);
      expect(c.risk).toBeGreaterThanOrEqual(0);
      expect(c.risk).toBeLessThanOrEqual(1);
    }
  });

  it("tracks diagnosis time", async () => {
    const result = await engine.diagnose(makeInput());
    expect(result.diagnosisTimeMs).toBeGreaterThanOrEqual(0);
  });
});

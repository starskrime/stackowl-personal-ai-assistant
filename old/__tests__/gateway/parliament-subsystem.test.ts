import { describe, it, expect, vi } from "vitest";
import { ParliamentSubsystem } from "../../src/gateway/parliament-subsystem.js";
import type { GatewayMessage } from "../../src/gateway/types.js";

const makeMsg = (): GatewayMessage => ({
  id: "m1", sessionId: "s1", userId: "u1", channelId: "cli", text: "hello",
});

/**
 * makeOrchestrator builds a minimal GatewayContext-like object with just the
 * parliament-related fields wired up.
 *
 * runDebate uses the real API: it mutates the passed ParliamentSession in place
 * (returns void) and writes `synthesis` directly onto the session object.
 */
const makeOrchestrator = (shouldTrigger = true) => ({
  parliamentAutoTrigger: {
    check: vi.fn().mockResolvedValue({ shouldTrigger, reason: "complex" }),
  },
  topicWorthiness: { evaluate: vi.fn().mockResolvedValue({ worthy: true, score: 0.8 }) },
  multiRoundDebate: {
    // Mutates session.synthesis in place — same contract as the real MultiRoundDebateManager
    runDebate: vi.fn().mockImplementation(async (session: { synthesis?: string; verdict?: string }) => {
      session.synthesis = "Parliament says: yes";
      session.verdict = "CONSENSUS_REACHED";
    }),
  },
  debatePelletGenerator: { generateFromSession: vi.fn().mockResolvedValue(undefined) },
});

describe("ParliamentSubsystem", () => {
  it("shouldAutoTrigger returns false when parliamentAutoTrigger is absent", async () => {
    const subsystem = new ParliamentSubsystem({} as any);
    const result = await subsystem.shouldAutoTrigger("test question");
    expect(result).toBe(false);
  });

  it("shouldAutoTrigger delegates to parliamentAutoTrigger.check", async () => {
    const deps = makeOrchestrator(true);
    const subsystem = new ParliamentSubsystem(deps as any);
    const result = await subsystem.shouldAutoTrigger("complex question");
    expect(deps.parliamentAutoTrigger.check).toHaveBeenCalledWith("complex question", undefined);
    expect(result).toBe(true);
  });

  it("run returns synthesis from debate as GatewayResponse", async () => {
    const deps = makeOrchestrator();
    const ctx = {
      owl: { persona: { name: "owl", emoji: "🦉" } },
      provider: {},
      pelletStore: {},
      ...deps,
    } as any;
    const subsystem = new ParliamentSubsystem(ctx);
    const result = await subsystem.run(makeMsg(), ctx);
    // synthesis is embedded in content along with #Parliament tag
    expect(result!.content).toContain("Parliament says: yes");
    expect(result!.content).toContain("#Parliament");
    expect(result!.owlName).toBe("owl");
    expect(deps.multiRoundDebate.runDebate).toHaveBeenCalled();
  });

  it("run returns null when dependencies are missing", async () => {
    const ctx = { owl: { persona: { name: "owl", emoji: "🦉" } } } as any;
    const subsystem = new ParliamentSubsystem(ctx);
    const result = await subsystem.run(makeMsg(), ctx);
    expect(result).toBeNull();
  });

  it("pellet generation failure does not reject run()", async () => {
    const deps = makeOrchestrator();
    deps.debatePelletGenerator.generateFromSession = vi.fn().mockRejectedValue(new Error("pellet fail"));
    const ctx = {
      owl: { persona: { name: "owl", emoji: "🦉" } },
      provider: {},
      pelletStore: {},
      ...deps,
    } as any;
    const subsystem = new ParliamentSubsystem(ctx);
    await expect(subsystem.run(makeMsg(), ctx)).resolves.not.toThrow();
  });
});

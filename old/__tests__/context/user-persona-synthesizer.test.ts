import { describe, it, expect, vi, beforeEach } from "vitest";
import { UserPersonaSynthesizer } from "../../src/context/user-persona-synthesizer.js";

function mockDb() {
  return { getUserPersonaRaw: vi.fn(() => null), setUserPersona: vi.fn() } as any;
}
function mockProvider() {
  const persona = JSON.stringify({
    communicationStyle: "technical", expertiseLevel: "expert",
    currentProjects: ["trading bot"], recurringPatterns: ["prefers code first"],
    emotionalTendencies: "direct", emotionalTrajectory: ["focused (2026-04-30)"],
    preferredApproach: "show code first", lastUpdated: new Date().toISOString(),
  });
  return { chat: vi.fn(async () => ({ content: persona })) } as any;
}

describe("UserPersonaSynthesizer", () => {
  it("returns null for new user with < 3 facts", async () => {
    const db = mockDb();
    const synth = new UserPersonaSynthesizer(mockProvider(), db);
    const result = await synth.getPersona("u1", [], [], "");
    expect(result).toBeNull();
  });

  it("calls LLM and caches when >= 3 facts and no cache", async () => {
    const db = mockDb();
    const provider = mockProvider();
    const synth = new UserPersonaSynthesizer(provider, db);
    const facts = Array.from({ length: 5 }, (_, i) => ({ fact: `fact ${i}`, confidence: 0.9 } as any));
    const result = await synth.getPersona("u1", facts, [], "");
    expect(provider.chat).toHaveBeenCalledOnce();
    expect(db.setUserPersona).toHaveBeenCalledOnce();
    expect(result?.expertiseLevel).toBe("expert");
  });

  it("returns cached persona when not expired", async () => {
    const db = mockDb();
    db.getUserPersonaRaw.mockReturnValue({
      personaJson: JSON.stringify({ communicationStyle: "casual", expertiseLevel: "novice",
        currentProjects: [], recurringPatterns: [], emotionalTendencies: "",
        emotionalTrajectory: [], preferredApproach: "", lastUpdated: "" }),
      expiresAt: Date.now() + 60_000,
    });
    const provider = mockProvider();
    const synth = new UserPersonaSynthesizer(provider, db);
    const result = await synth.getPersona("u1", [{}] as any, [], "");
    expect(provider.chat).not.toHaveBeenCalled();
    expect(result?.communicationStyle).toBe("casual");
  });

  it("returns stale persona and triggers background refresh when expired", async () => {
    const db = mockDb();
    const stale = { communicationStyle: "verbose", expertiseLevel: "intermediate",
      currentProjects: [], recurringPatterns: [], emotionalTendencies: "",
      emotionalTrajectory: [], preferredApproach: "", lastUpdated: "" };
    db.getUserPersonaRaw.mockReturnValue({ personaJson: JSON.stringify(stale), expiresAt: Date.now() - 1 });
    const provider = mockProvider();
    const synth = new UserPersonaSynthesizer(provider, db);
    const facts = Array.from({ length: 5 }, (_, i) => ({ fact: `f${i}`, confidence: 0.9 } as any));
    const result = await synth.getPersona("u1", facts, [], "");
    expect(result?.communicationStyle).toBe("verbose"); // stale returned immediately
    // Flush the setImmediate queue
    await new Promise<void>(resolve => setImmediate(resolve));
    // Background refresh should have triggered LLM
    expect(provider.chat).toHaveBeenCalledOnce();
  });
});

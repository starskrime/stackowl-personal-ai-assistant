import { describe, it, expect } from "vitest";
import { computeTriage } from "../../src/context/triage.js";
import { hash, resolveUserId } from "../../src/context/utils.js";

describe("computeTriage", () => {
  const base = {
    sessionDepth: 3,
    continuityClass: null as any,
    userId: "u1",
    sessionId: "s1",
    hasActiveItems: false,
  };

  it("marks short message as conversational", () => {
    const t = computeTriage({ ...base, userMessage: "hey there" });
    expect(t.isConversational).toBe(true);
  });

  it("marks long message as non-conversational", () => {
    const msg = "Please help me debug this issue with my trading bot that keeps crashing";
    const t = computeTriage({ ...base, userMessage: msg });
    expect(t.isConversational).toBe(false);
  });

  it("detects frustration keywords", () => {
    const t = computeTriage({ ...base, userMessage: "still not working again" });
    expect(t.hasFrustration).toBe(true);
  });

  it("detects opinion request", () => {
    const t = computeTriage({ ...base, userMessage: "what do you think about this?" });
    expect(t.isOpinionRequest).toBe(true);
  });

  it("detects temporal trigger", () => {
    const t = computeTriage({ ...base, userMessage: "remember last time we did this?" });
    expect(t.hasTemporalTrigger).toBe(true);
  });

  it("marks FRESH_START as returning user", () => {
    const t = computeTriage({ ...base, userMessage: "hi", continuityClass: "FRESH_START" });
    expect(t.isReturningUser).toBe(true);
  });

  it("uses sessionId as effectiveUserId when userId absent", () => {
    const t = computeTriage({ ...base, userMessage: "hi", userId: undefined });
    expect(t.effectiveUserId).toBe("s1");
  });
});

describe("hash", () => {
  it("returns same string for same input", () => {
    expect(hash("abc")).toBe(hash("abc"));
  });
  it("returns different strings for different inputs", () => {
    expect(hash("abc")).not.toBe(hash("xyz"));
  });
});

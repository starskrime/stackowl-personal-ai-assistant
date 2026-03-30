import { describe, it, expect } from "vitest";
import {
  computeTemporalContext,
  formatTemporalPrompt,
} from "../src/cognition/temporal-context.js";
import { classifyContinuity } from "../src/cognition/continuity-engine.js";
import type { Session } from "../src/memory/store.js";

// ─── Helpers ──────────────────────────────────────────────────────

function makeSession(
  overrides: Partial<Session> = {},
  messageAgeMs = 60_000,
): Session {
  return {
    id: "test:user1",
    messages: [
      {
        role: "user" as const,
        content: "Tell me about email setup",
        timestamp: Date.now() - messageAgeMs,
      },
      {
        role: "assistant" as const,
        content: "Sure, let me help with that...",
        timestamp: Date.now() - messageAgeMs + 5000,
      },
    ],
    metadata: {
      owlName: "noctua",
      startedAt: Date.now() - messageAgeMs - 10000,
      lastUpdatedAt: Date.now() - messageAgeMs + 5000,
    },
    ...overrides,
  };
}

function emptySession(): Session {
  return {
    id: "test:user1",
    messages: [],
    metadata: {
      owlName: "noctua",
      startedAt: Date.now(),
      lastUpdatedAt: Date.now(),
    },
  };
}

// ─── Temporal Context Tests ───────────────────────────────────────

describe("TemporalContext", () => {
  it("computes basic temporal snapshot", () => {
    const session = makeSession();
    const snapshot = computeTemporalContext(session, null, "UTC");

    expect(snapshot.timezone).toBe("UTC");
    expect(snapshot.dayOfWeek).toBeTruthy();
    expect(["morning", "afternoon", "evening", "night"]).toContain(
      snapshot.timeOfDay,
    );
    expect(snapshot.isReturningUser).toBe(false);
  });

  it("detects returning user with 5-hour gap from previous session", () => {
    const session = emptySession();
    const prevSession = makeSession(
      {
        id: "test:user1_old",
        metadata: {
          owlName: "noctua",
          startedAt: Date.now() - 6 * 60 * 60 * 1000,
          lastUpdatedAt: Date.now() - 5 * 60 * 60 * 1000,
        },
      },
      5 * 60 * 60 * 1000,
    );

    const snapshot = computeTemporalContext(session, prevSession, "UTC");
    expect(snapshot.isReturningUser).toBe(true);
    expect(snapshot.lastSessionGap).toBeTruthy();
  });

  it("does NOT flag returning user for 30-min gap", () => {
    const session = makeSession({}, 30 * 60 * 1000);
    const snapshot = computeTemporalContext(session, null, "UTC");
    expect(snapshot.isReturningUser).toBe(false);
  });

  it("formats temporal prompt with all fields", () => {
    const session = makeSession();
    const prevSession = makeSession(
      {
        id: "test:user1_old",
        metadata: {
          owlName: "noctua",
          startedAt: Date.now() - 25 * 60 * 60 * 1000,
          lastUpdatedAt: Date.now() - 24 * 60 * 60 * 1000,
        },
      },
      25 * 60 * 60 * 1000,
    );

    const snapshot = computeTemporalContext(session, prevSession, "UTC");
    const prompt = formatTemporalPrompt(snapshot);

    expect(prompt).toContain("## Temporal Context");
    expect(prompt).toContain("Current time:");
  });

  it("formats empty temporal prompt for new session", () => {
    const session = emptySession();
    const snapshot = computeTemporalContext(session, null, "UTC");
    const prompt = formatTemporalPrompt(snapshot);

    expect(prompt).toContain("## Temporal Context");
    expect(prompt).toContain("Current time:");
    // Should NOT contain session age or message gap for brand new session
    expect(prompt).not.toContain("Session started:");
  });
});

// ─── Continuity Engine Tests ──────────────────────────────────────

describe("ContinuityEngine", () => {
  it("classifies empty session as FRESH_START", async () => {
    const session = emptySession();
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity(
      "hello",
      session,
      snapshot,
    );
    expect(result.classification).toBe("FRESH_START");
    expect(result.confidence).toBeGreaterThan(0.9);
  });

  it("classifies recent message with anaphora as CONTINUATION", async () => {
    const session = makeSession({}, 60_000); // 1 min ago
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity(
      "that looks good, continue with it",
      session,
      snapshot,
    );
    expect(result.classification).toBe("CONTINUATION");
  });

  it("classifies 'also' as CONTINUATION", async () => {
    const session = makeSession({}, 120_000); // 2 min ago
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity(
      "also can you check the DNS records?",
      session,
      snapshot,
    );
    expect(result.classification).toBe("CONTINUATION");
  });

  it("classifies standalone greeting as FRESH_START", async () => {
    const session = makeSession({}, 60_000);
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity("hello", session, snapshot);
    expect(["FRESH_START", "TOPIC_SWITCH"]).toContain(
      result.classification,
    );
  });

  it("classifies 'new topic' as TOPIC_SWITCH or FRESH_START", async () => {
    const session = makeSession({}, 60_000);
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity(
      "new topic, I want to talk about AI",
      session,
      snapshot,
    );
    expect(["TOPIC_SWITCH", "FRESH_START"]).toContain(
      result.classification,
    );
  });

  it("classifies 'btw' as TOPIC_SWITCH", async () => {
    const session = makeSession({}, 60_000);
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity(
      "btw can you also check my server?",
      session,
      snapshot,
    );
    expect(result.classification).toBe("TOPIC_SWITCH");
  });

  it("classifies message after 5h gap with no markers as FRESH_START", async () => {
    const session = makeSession({}, 5 * 60 * 60 * 1000);
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity(
      "what's the weather today?",
      session,
      snapshot,
    );
    expect(result.classification).toBe("FRESH_START");
  });

  it("classifies 'as I was saying' after 5h gap as CONTINUATION (linguistic wins)", async () => {
    const session = makeSession({}, 5 * 60 * 60 * 1000);
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity(
      "as I was saying, the email setup needs fixing",
      session,
      snapshot,
    );
    // Linguistic says CONTINUATION, temporal says FRESH_START — should resolve
    // Layer 3 would be invoked, but without provider it falls back
    expect(["CONTINUATION", "FOLLOW_UP"]).toContain(
      result.classification,
    );
  });

  it("classifies recent message with no markers as CONTINUATION (temporal wins)", async () => {
    const session = makeSession({}, 30_000); // 30 sec ago
    const snapshot = computeTemporalContext(session, null, "UTC");
    const result = await classifyContinuity(
      "can you show me the config file?",
      session,
      snapshot,
    );
    expect(result.classification).toBe("CONTINUATION");
    expect(result.layerUsed).toBe(1); // Pure temporal
  });
});

/**
 * Tests for Phase 4: Conversational Ground State
 *
 * Tests GroundStateView — session-scoped view over FactStore
 * that tracks decisions, open questions, goals, and rolling summary.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { GroundStateView } from "../src/cognition/ground-state.js";

// ─── Minimal FactStore mock ─────────────────────────────────────

function createMockFactStore(facts: any[] = []) {
  const store: any[] = [...facts];
  let nextId = 1;

  return {
    getActiveForUser: vi.fn((_userId: string) => store.filter((f) => !f._expired)),
    add: vi.fn(async (entry: any) => {
      store.push({ id: `fact_${nextId++}`, ...entry });
    }),
    update: vi.fn(async (id: string, patch: any) => {
      const f = store.find((x) => x.id === id);
      if (f) Object.assign(f, patch);
    }),
    search: vi.fn(async () => []),
    _store: store,
  };
}

// ─── Minimal ModelProvider mock ─────────────────────────────────

function createMockProvider(response: string = "{}") {
  return {
    chat: vi.fn(async () => ({ content: response, toolCalls: [] })),
    name: "mock",
    healthCheck: vi.fn(async () => true),
    countTokens: vi.fn(async () => 0),
  };
}

// ─── Tests ──────────────────────────────────────────────────────

describe("GroundStateView", () => {
  let factStore: ReturnType<typeof createMockFactStore>;
  let provider: ReturnType<typeof createMockProvider>;
  let gsv: GroundStateView;

  beforeEach(() => {
    factStore = createMockFactStore();
    provider = createMockProvider();
    gsv = new GroundStateView(factStore as any, provider as any, 3);
  });

  it("returns empty context when no ground state exists", () => {
    gsv.setSession("sess1");
    const ctx = gsv.toContextString("user1");
    expect(ctx).toBe("");
  });

  it("tracks turn count and signals refresh needed", () => {
    expect(gsv.recordTurn()).toBe(false); // turn 1
    expect(gsv.recordTurn()).toBe(false); // turn 2
    expect(gsv.recordTurn()).toBe(true);  // turn 3 = refreshInterval
  });

  it("resets turn count on session change", () => {
    gsv.setSession("sess1");
    gsv.recordTurn();
    gsv.recordTurn();
    gsv.setSession("sess2"); // should reset
    expect(gsv.recordTurn()).toBe(false); // turn 1 again
  });

  it("returns ground state with decisions and goals", () => {
    factStore = createMockFactStore([
      { id: "f1", category: "decision", fact: "Use PostgreSQL", entity: "sess1", confidence: 0.8, userId: "user1" },
      { id: "f2", category: "active_goal", fact: "Set up database", entity: "sess1", confidence: 0.8, userId: "user1" },
      { id: "f3", category: "open_question", fact: "Which ORM?", entity: "sess1", confidence: 0.8, userId: "user1" },
    ]);
    gsv = new GroundStateView(factStore as any, provider as any);
    gsv.setSession("sess1");

    const state = gsv.getState("user1");
    expect(state.decisions).toHaveLength(1);
    expect(state.decisions[0].fact).toBe("Use PostgreSQL");
    expect(state.activeGoals).toHaveLength(1);
    expect(state.openQuestions).toHaveLength(1);
  });

  it("formats context string with all sections", () => {
    factStore = createMockFactStore([
      { id: "f1", category: "decision", fact: "Use REST not GraphQL", entity: "sess1", confidence: 0.8, userId: "user1" },
      { id: "f2", category: "active_goal", fact: "Build API layer", entity: "sess1", confidence: 0.8, userId: "user1" },
      { id: "f3", category: "open_question", fact: "Auth strategy?", entity: "sess1", confidence: 0.8, userId: "user1" },
    ]);
    gsv = new GroundStateView(factStore as any, provider as any);
    gsv.setSession("sess1");

    const ctx = gsv.toContextString("user1");
    expect(ctx).toContain("<conversational_ground>");
    expect(ctx).toContain("Working on: Build API layer");
    expect(ctx).toContain("Use REST not GraphQL");
    expect(ctx).toContain("Auth strategy?");
    expect(ctx).toContain("</conversational_ground>");
  });

  it("refreshes ground state from messages via LLM", async () => {
    provider = createMockProvider(JSON.stringify({
      facts: ["The project uses TypeScript"],
      decisions: ["We'll use Vitest for testing"],
      open_questions: ["Should we add E2E tests?"],
      goal: "Set up test infrastructure",
      summary: "Configuring the testing stack for the project",
    }));
    gsv = new GroundStateView(factStore as any, provider as any);
    gsv.setSession("sess1");

    await gsv.refresh(
      [
        { role: "user", content: "Let's set up testing" },
        { role: "assistant", content: "I'll configure Vitest for you" },
      ],
      "user1",
      "sess1",
    );

    // Should have called provider.chat
    expect(provider.chat).toHaveBeenCalledTimes(1);

    // Should have stored facts
    expect(factStore.add).toHaveBeenCalled();
    const addedCategories = factStore.add.mock.calls.map((c: any) => c[0].category);
    expect(addedCategories).toContain("context");
    expect(addedCategories).toContain("decision");
    expect(addedCategories).toContain("open_question");
    expect(addedCategories).toContain("active_goal");
  });

  it("skips refresh with fewer than 2 messages", async () => {
    await gsv.refresh(
      [{ role: "user", content: "hi" }],
      "user1",
      "sess1",
    );
    expect(provider.chat).not.toHaveBeenCalled();
  });

  it("handles LLM timeout gracefully", async () => {
    provider.chat = vi.fn(() => new Promise((_, reject) =>
      setTimeout(() => reject(new Error("timeout")), 100),
    ));
    gsv = new GroundStateView(factStore as any, provider as any);

    // Should not throw
    await gsv.refresh(
      [
        { role: "user", content: "test" },
        { role: "assistant", content: "reply" },
      ],
      "user1",
      "sess1",
    );
    expect(factStore.add).not.toHaveBeenCalled();
  });

  it("prevents concurrent refreshes", async () => {
    let resolveChat: (v: any) => void;
    provider.chat = vi.fn(() => new Promise((resolve) => {
      resolveChat = resolve;
    }));
    gsv = new GroundStateView(factStore as any, provider as any);

    const msgs = [
      { role: "user" as const, content: "a" },
      { role: "assistant" as const, content: "b" },
    ];

    // Start first refresh (will hang)
    const p1 = gsv.refresh(msgs, "user1", "sess1");
    // Second refresh should bail immediately
    const p2 = gsv.refresh(msgs, "user1", "sess1");

    // Resolve the first
    resolveChat!({ content: "{}", toolCalls: [] });
    await Promise.all([p1, p2]);

    // Only one LLM call should have been made
    expect(provider.chat).toHaveBeenCalledTimes(1);
  });

  it("archives open questions with short TTL", async () => {
    factStore = createMockFactStore([
      { id: "q1", category: "open_question", fact: "Which DB?", entity: "sess1", confidence: 0.8, userId: "user1" },
      { id: "q2", category: "open_question", fact: "Auth method?", entity: "sess1", confidence: 0.8, userId: "user1" },
      { id: "d1", category: "decision", fact: "Use REST", entity: "sess1", confidence: 0.8, userId: "user1" },
    ]);
    gsv = new GroundStateView(factStore as any, provider as any);
    gsv.setSession("sess1");

    await gsv.archive("user1");

    // Should have set TTL on open questions only
    expect(factStore.update).toHaveBeenCalledTimes(2);
    const updatedIds = factStore.update.mock.calls.map((c: any) => c[0]);
    expect(updatedIds).toContain("q1");
    expect(updatedIds).toContain("q2");
    // Decisions should NOT be archived
    expect(updatedIds).not.toContain("d1");
  });
});

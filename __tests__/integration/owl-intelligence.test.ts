import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { ReflexionEngine } from "../../src/intelligence/reflexion-engine.js";
import { FactInvalidator } from "../../src/intelligence/fact-invalidator.js";
import { SemanticToolGate } from "../../src/intelligence/semantic-tool-gate.js";
import { HITLEscalator } from "../../src/intelligence/hitl-escalator.js";
import { OwlStateReporter } from "../../src/intelligence/owl-state-reporter.js";

describe("intelligence module integration", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db as any);
  });

  afterEach(() => db.close());

  it("SemanticToolGate + HITLEscalator do not share state", () => {
    const gate = new SemanticToolGate();
    const esc1 = new HITLEscalator();
    const esc2 = new HITLEscalator();
    esc1.onBlocked("web", "404", "task");
    expect(esc1.shouldEscalate(2)).toBe(true);
    expect(esc2.shouldEscalate(2)).toBe(false);
    expect(gate).toBeDefined();
  });

  it("ReflexionEngine writes critique to reflexion_critiques table", async () => {
    const embedFn = async (_text: string) => [0.8, 0.2, 0.1, 0.0];
    const mockProvider = {
      chat: async () => ({ content: "Searched too broadly. Use specific terms next time.", finishReason: "stop" as const, model: "test" }),
    };

    const engine = new ReflexionEngine(db as any, mockProvider as any, embedFn);
    await engine.onTaskFailed({
      userId: "u1",
      taskDescription: "find TypeScript docs",
      toolSequence: ["web"],
      errorSummary: "404",
      category: "research",
      complexityTier: "medium",
    });

    const rows = db.prepare("SELECT critique_text FROM reflexion_critiques").all();
    expect(rows).toHaveLength(1);
    expect((rows[0] as any).critique_text).toContain("Searched too broadly");
  });

  it("FactInvalidator invalidates London fact when Tokyo extracted", async () => {
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at, embedding)
      VALUES ('f1', 'u1', 'aria', 'User lives in London', 'personal', 0.9, 'explicit', 0, datetime('now'), datetime('now'), ?)
    `).run(JSON.stringify([0.9, 0.1, 0.0, 0.0]));

    const invalidator = new FactInvalidator(db as any);
    (invalidator as any).embedFn = async () => [0.9, 0.1, 0.0, 0.0];
    await invalidator.check("User moved to Tokyo", "u1");

    const row = db.prepare("SELECT invalidated_at FROM facts WHERE id = 'f1'").get() as any;
    expect(row.invalidated_at).not.toBeNull();
  });

  it("OwlStateReporter renders correctly with mixed data", async () => {
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at)
      VALUES ('f1', 'u1', 'aria', 'prefers TypeScript', 'preference', 0.9, 'owl_inferred', 0, datetime('now'), datetime('now'))
    `).run();

    const reporter = new OwlStateReporter(db as any);
    const report = await reporter.report("u1", "aria");
    expect(report).toContain("aria");
    expect(report).toContain("1 fact");
    expect(report).toContain("prefers TypeScript");
  });
});

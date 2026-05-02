import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { CritiqueRetriever } from "../../src/intelligence/critique-retriever.js";

describe("CritiqueRetriever", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("returns empty string when no critiques exist", async () => {
    const retriever = new CritiqueRetriever(db as any);
    const result = await retriever.retrieve("research TypeScript docs", "research", "medium");
    expect(result).toBe("");
  });

  it("returns past_lessons block when matching critique exists", async () => {
    const buf = Buffer.alloc(4 * 4);
    [0.9, 0.1, 0.1, 0.1].forEach((v, i) => buf.writeFloatLE(v, i * 4));
    db.prepare(`
      INSERT INTO reflexion_critiques (id, task_category, complexity_tier, tool_sequence, critique_text, embedding, used_count, created_at)
      VALUES ('c1', 'research', 'medium', 'web', 'I searched too broadly. Next time use specific terms.', ?, 0, ?)
    `).run(buf, new Date().toISOString());

    const retriever = new CritiqueRetriever(db as any);
    (retriever as any).embedFn = async () => [0.9, 0.1, 0.1, 0.1];
    const result = await retriever.retrieve("research TypeScript docs", "research", "medium");
    expect(result).toContain("<past_lessons>");
    expect(result).toContain("I searched too broadly");
  });

  it("shouldFire returns true for non-conversational requests", () => {
    const retriever = new CritiqueRetriever(db as any);
    const layer = retriever.asContextLayer();
    expect(layer.shouldFire({ isConversational: false } as any)).toBe(true);
  });

  it("shouldFire returns false for conversational messages", () => {
    const retriever = new CritiqueRetriever(db as any);
    const layer = retriever.asContextLayer();
    expect(layer.shouldFire({ isConversational: true } as any)).toBe(false);
  });
});

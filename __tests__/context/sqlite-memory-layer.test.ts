import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { join } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SqliteTier0Layer } from "../../src/context/layers/sqlite-memory.js";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "stackowl-tier0-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("SqliteTier0Layer", () => {
  it("injects high-confidence facts as tier0_memory context", async () => {
    db.facts.add({
      userId: "default",
      owlName: "default",
      fact: "Prefers concise responses",
      category: "preference",
      confidence: 0.9,
      source: "explicit",
    });

    const layer = new SqliteTier0Layer(db);
    const result = await layer.build({} as any, {} as any, new Map());

    expect(result).toContain("<tier0_memory>");
    expect(result).toContain("Prefers concise responses");
    expect(result).toContain("</tier0_memory>");
  });

  it("returns empty string when db has no high-confidence tier-0 facts", async () => {
    db.facts.add({
      userId: "default",
      owlName: "default",
      fact: "Low confidence note",
      category: "preference",
      confidence: 0.5,
      source: "inferred",
    });

    const layer = new SqliteTier0Layer(db);
    const result = await layer.build({} as any, {} as any, new Map());

    expect(result).toBe("");
  });

  it("returns empty string when no db is provided", async () => {
    const layer = new SqliteTier0Layer();
    const result = await layer.build({} as any, {} as any, new Map());
    expect(result).toBe("");
  });

  it("always fires — shouldFire returns true unconditionally", () => {
    const layer = new SqliteTier0Layer(db);
    expect(layer.shouldFire({} as any)).toBe(true);
  });

  it("has priority 0 — highest in pipeline", () => {
    const layer = new SqliteTier0Layer(db);
    expect(layer.priority).toBe(0);
  });

  it("formats facts as bullet list grouped by category", async () => {
    db.facts.add({
      userId: "default",
      owlName: "default",
      fact: "Prefers TypeScript",
      category: "preference",
      confidence: 0.9,
      source: "explicit",
    });
    db.facts.add({
      userId: "default",
      owlName: "default",
      fact: "Goal: ship StackOwl v1",
      category: "active_goal",
      confidence: 0.85,
      source: "explicit",
    });

    const layer = new SqliteTier0Layer(db);
    const result = await layer.build({} as any, {} as any, new Map());

    expect(result).toContain("preference:");
    expect(result).toContain("active_goal:");
  });
});

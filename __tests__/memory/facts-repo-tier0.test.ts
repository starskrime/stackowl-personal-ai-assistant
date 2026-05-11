import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { join } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "stackowl-facts-tier0-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("FactsRepo.getHighConfidenceFacts", () => {
  it("returns facts with confidence >= 0.8 in tier-0 categories", () => {
    db.facts.add({ userId: "default", owlName: "default", fact: "Prefers TypeScript strict mode",
      category: "preference", confidence: 0.9, source: "explicit" });
    db.facts.add({ userId: "default", owlName: "default", fact: "Has a dog named Max",
      category: "personal", confidence: 0.85, source: "explicit" });
    db.facts.add({ userId: "default", owlName: "default", fact: "Low confidence note",
      category: "preference", confidence: 0.5, source: "inferred" });
    db.facts.add({ userId: "default", owlName: "default", fact: "Context tidbit",
      category: "context", confidence: 0.95, source: "explicit" });

    const results = db.facts.getHighConfidenceFacts();
    expect(results).toHaveLength(2);
    expect(results.map((f) => f.fact)).toContain("Prefers TypeScript strict mode");
    expect(results.map((f) => f.fact)).toContain("Has a dog named Max");
  });

  it("excludes retired facts (confidence = 0)", () => {
    db.facts.add({ userId: "default", owlName: "default", fact: "Old preference",
      category: "preference", confidence: 0.9, source: "explicit" });
    const all = db.facts.getAllForUser();
    db.facts.retire(all[0].id);

    const results = db.facts.getHighConfidenceFacts();
    expect(results).toHaveLength(0);
  });

  it("respects limit parameter", () => {
    for (let i = 0; i < 5; i++) {
      db.facts.add({ userId: "default", owlName: "default", fact: `Preference ${i}`,
        category: "preference", confidence: 0.9, source: "explicit" });
    }
    const results = db.facts.getHighConfidenceFacts(undefined, 3);
    expect(results).toHaveLength(3);
  });

  it("orders by confidence DESC", () => {
    db.facts.add({ userId: "default", owlName: "default", fact: "Medium confidence",
      category: "preference", confidence: 0.82, source: "explicit" });
    db.facts.add({ userId: "default", owlName: "default", fact: "High confidence",
      category: "preference", confidence: 0.95, source: "explicit" });

    const results = db.facts.getHighConfidenceFacts();
    expect(results[0].fact).toBe("High confidence");
  });
});

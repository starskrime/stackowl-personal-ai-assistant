import { describe, it, expect } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";

function makeTempDb(): { db: MemoryDatabase; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), "test-parliament-"));
  const db = new MemoryDatabase(dir);
  return {
    db,
    cleanup: () => {
      try {
        rmSync(dir, { recursive: true, force: true });
      } catch {}
    },
  };
}

describe("parliament_verdicts confidence_score", () => {
  it("record() stores confidence_score and topic_class", () => {
    const { db, cleanup } = makeTempDb();
    try {
      const id = db.parliamentVerdicts.record(
        "session-1", "Should we use GraphQL?", "PROCEED",
        ["Mary", "Winston"], "synthesis text",
        { confidenceScore: 0.8, topicClass: "architectural" },
      );
      const rows = (db as any).db.prepare(
        "SELECT confidence_score, topic_class FROM parliament_verdicts WHERE id = ?"
      ).all(id);
      expect(rows[0].confidence_score).toBeCloseTo(0.8);
      expect(rows[0].topic_class).toBe("architectural");
    } finally { cleanup(); }
  });

  it("updateConfidence() sets confidence_score and validator_reasoning", () => {
    const { db, cleanup } = makeTempDb();
    try {
      const id = db.parliamentVerdicts.record(
        "session-2", "Test topic", "HOLD",
        ["Mary"], "synthesis",
      );
      db.parliamentVerdicts.updateConfidence(id, 0.95, "Logic is sound");
      const rows = (db as any).db.prepare(
        "SELECT confidence_score, validator_reasoning FROM parliament_verdicts WHERE id = ?"
      ).all(id);
      expect(rows[0].confidence_score).toBeCloseTo(0.95);
      expect(rows[0].validator_reasoning).toBe("Logic is sound");
    } finally { cleanup(); }
  });

  it("findRelated() returns top-2 by confidence_score and filters expired", () => {
    const { db, cleanup } = makeTempDb();
    try {
      const now = Math.floor(Date.now() / 1000);
      const id1 = db.parliamentVerdicts.record(
        "s1", "GraphQL architecture decision", "PROCEED",
        ["Mary"], "high confidence",
        { confidenceScore: 0.9, topicClass: "architectural" },
      );
      db.parliamentVerdicts.updateConfidence(id1, 0.9, "valid");

      const id2 = db.parliamentVerdicts.record(
        "s2", "GraphQL vs REST API design", "HOLD",
        ["Winston"], "medium confidence",
        { confidenceScore: 0.6, topicClass: "architectural" },
      );
      db.parliamentVerdicts.updateConfidence(id2, 0.6, "uncertain");

      // Expired verdict — should be excluded
      const id3 = db.parliamentVerdicts.record(
        "s3", "GraphQL query optimization", "PROCEED",
        ["John"], "expired",
        { confidenceScore: 0.85, topicClass: "tactical", expiresAt: now - 1 },
      );
      db.parliamentVerdicts.updateConfidence(id3, 0.85, "expired");

      const results = db.parliamentVerdicts.findRelated("GraphQL API design", 2);
      expect(results.length).toBeLessThanOrEqual(2);
      expect(results.every(r => r.confidenceScore >= 0.5)).toBe(true);
      // Expired verdict must not appear
      expect(results.find(r => r.id === id3)).toBeUndefined();
    } finally { cleanup(); }
  });
});

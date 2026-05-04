import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { FallbackSequencer } from "../../src/tools/fallback-sequencer.js";

describe("FallbackSequencer — DB-backed", () => {
  let dir: string;
  let db: MemoryDatabase;
  let seq: FallbackSequencer;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "fbseq-db-"));
    db = new MemoryDatabase(dir);
    seq = new FallbackSequencer(db);
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("returns learned fallback when tool_edges has data", () => {
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
      )
      .run("web", "web_crawl", "web_fetch", 0.85, 10);
    expect(seq.getNextFallback("web", "web_fetch")).toBe("web_crawl");
  });

  it("returns null when no edge exists", () => {
    expect(seq.getNextFallback("web", "web_fetch")).toBeNull();
  });

  it("excludes already-tried tools", () => {
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
      )
      .run("web", "web_crawl", "web_fetch", 0.85, 10);
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
      )
      .run("web", "scrapling_fetch", "web_fetch", 0.6, 5);
    expect(seq.getNextFallback("web", "web_fetch", ["web_crawl"])).toBe(
      "scrapling_fetch",
    );
  });

  it("ignores edges with sample_count < 3 (not enough data)", () => {
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
      )
      .run("web", "weak_alt", "web_fetch", 1.0, 1);
    expect(seq.getNextFallback("web", "web_fetch")).toBeNull();
  });

  it("orders by success_rate DESC then sample_count DESC", () => {
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
      )
      .run("web", "low_success", "web_fetch", 0.5, 100);
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
      )
      .run("web", "high_success", "web_fetch", 0.95, 5);
    expect(seq.getNextFallback("web", "web_fetch")).toBe("high_success");
  });

  it("survives restart (no in-memory state)", () => {
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
      )
      .run("web", "memory", "search", 0.7, 5);
    const seq2 = new FallbackSequencer(db);
    expect(seq2.getNextFallback("web", "search")).toBe("memory");
  });
});

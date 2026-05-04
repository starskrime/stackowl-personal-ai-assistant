/**
 * StackOwl — Element 7 T9 — EdgeAccumulator
 *
 * Verifies that observations write to `tool_edges` and update running averages
 * in place (no row blow-up, sample_count increments, success_rate and
 * avg_duration_ms collapse to true running means).
 */
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import { MemoryDatabase } from "../../src/memory/db.js";
import { EdgeAccumulator } from "../../src/tools/cortex/edge-accumulator.js";

describe("EdgeAccumulator", () => {
  let db: MemoryDatabase;
  let acc: EdgeAccumulator;

  beforeEach(() => {
    const dir = mkdtempSync(join(tmpdir(), "edge-"));
    db = new MemoryDatabase(dir);
    acc = new EdgeAccumulator(db);
  });

  it("creates edge on first observation", () => {
    acc.observe({
      fromTool: "web",
      toTool: "web_crawl",
      capabilityTag: "web_fetch",
      success: true,
      durationMs: 100,
    });
    const row = db.rawDb
      .prepare(
        "SELECT * FROM tool_edges WHERE from_tool=? AND to_tool=? AND capability_tag=?",
      )
      .get("web", "web_crawl", "web_fetch") as {
      sample_count: number;
      success_rate: number;
      avg_duration_ms: number;
    };
    expect(row.sample_count).toBe(1);
    expect(row.success_rate).toBe(1);
    expect(row.avg_duration_ms).toBe(100);
  });

  it("updates running averages on subsequent observations", () => {
    acc.observe({
      fromTool: "web",
      toTool: "web_crawl",
      capabilityTag: "web_fetch",
      success: true,
      durationMs: 100,
    });
    acc.observe({
      fromTool: "web",
      toTool: "web_crawl",
      capabilityTag: "web_fetch",
      success: false,
      durationMs: 200,
    });
    acc.observe({
      fromTool: "web",
      toTool: "web_crawl",
      capabilityTag: "web_fetch",
      success: true,
      durationMs: 300,
    });
    const row = db.rawDb
      .prepare(
        "SELECT * FROM tool_edges WHERE from_tool=? AND to_tool=? AND capability_tag=?",
      )
      .get("web", "web_crawl", "web_fetch") as {
      sample_count: number;
      success_rate: number;
      avg_duration_ms: number;
    };
    expect(row.sample_count).toBe(3);
    expect(row.success_rate).toBeCloseTo(2 / 3, 3);
    expect(row.avg_duration_ms).toBe(200);
  });

  it("keeps separate rows per (from, to, capability) triple", () => {
    acc.observe({
      fromTool: "web",
      toTool: "web_crawl",
      capabilityTag: "web_fetch",
      success: true,
      durationMs: 100,
    });
    acc.observe({
      fromTool: "web",
      toTool: "document",
      capabilityTag: "web_fetch",
      success: false,
      durationMs: 500,
    });
    const rows = db.rawDb
      .prepare("SELECT to_tool FROM tool_edges WHERE from_tool=?")
      .all("web") as { to_tool: string }[];
    expect(rows.map((r) => r.to_tool).sort()).toEqual([
      "document",
      "web_crawl",
    ]);
  });
});

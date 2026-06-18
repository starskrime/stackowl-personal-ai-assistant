/**
 * StackOwl — Element 7 T8 — ToolGraph (single-hop replan)
 *
 * Verifies the Cost-Weighted Tool Graph picks the highest-success-rate
 * alternative tool for a given capability tag, respecting exclusions and the
 * min-samples noise floor.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ToolGraph } from "../../src/tools/cortex/tool-graph.js";

describe("ToolGraph — Dijkstra replan (single-hop)", () => {
  let db: MemoryDatabase;
  let graph: ToolGraph;

  beforeEach(() => {
    const dir = mkdtempSync(join(tmpdir(), "tg-"));
    db = new MemoryDatabase(dir);
    graph = new ToolGraph(db);

    const ins = db.rawDb.prepare(
      "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count) VALUES (?, ?, ?, ?, ?, ?)",
    );
    // capability "web_fetch": web -> web_crawl (high) -> document (low)
    ins.run("web", "web_crawl", "web_fetch", 0.9, 200, 50);
    ins.run("web", "document", "web_fetch", 0.5, 800, 10);
    ins.run("web_crawl", "document", "web_fetch", 0.3, 500, 5);
  });

  it("returns highest-success-rate alternative", () => {
    const next = graph.replan("web", "web_fetch", { exclude: [] });
    expect(next).toBe("web_crawl");
  });

  it("excludes the failing tool itself", () => {
    const next = graph.replan("web", "web_fetch", { exclude: ["web"] });
    expect(next).toBe("web_crawl");
  });

  it("falls back to next-best when primary excluded", () => {
    const next = graph.replan("web", "web_fetch", {
      exclude: ["web", "web_crawl"],
    });
    expect(next).toBe("document");
  });

  it("returns null when no edges match", () => {
    const next = graph.replan("nonexistent", "fake_capability");
    expect(next).toBeNull();
  });

  it("respects min sample count threshold", () => {
    const tg = new ToolGraph(db, { minSamples: 20 });
    // only web_crawl has 50 samples; others below 20
    const next = tg.replan("web", "web_fetch");
    expect(next).toBe("web_crawl");
  });
});

import { describe, it, expect } from "vitest";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { ToolGraph } from "../../src/tools/cortex/tool-graph.js";
import { MemoryDatabase } from "../../src/memory/db.js";

function makeDb() {
  const dir = mkdtempSync(join(tmpdir(), "replan-test-"));
  const db = new MemoryDatabase(dir);
  return { db, dir };
}

function teardown(db: MemoryDatabase, dir: string) {
  db.close();
  rmSync(dir, { recursive: true, force: true });
}

describe("host-aware replan", () => {
  it("returns host-specific edge before global when hostRoot matches", () => {
    const { db, dir } = makeDb();
    const raw = db.rawDb;

    // Insert a host-specific row: amazon.com → puppeteer
    raw.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run("scrapling", "puppeteer_tier", "web_fetch", "amazon.com", 0.9, 3000, 5);

    // Insert a global row: scrapling → camofox
    raw.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run("scrapling", "camofox_tier", "web_fetch", "", 0.8, 500, 10);

    const graph = new ToolGraph(db);
    const result = graph.replan("scrapling", "web_fetch", { hostRoot: "amazon.com" });
    teardown(db, dir);
    expect(result).toBe("puppeteer_tier");
  });

  it("falls back to global (host_root='') when no host-specific match", () => {
    const { db, dir } = makeDb();
    const raw = db.rawDb;

    raw.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run("scrapling", "camofox_tier", "web_fetch", "", 0.8, 500, 10);

    const graph = new ToolGraph(db);
    const result = graph.replan("scrapling", "web_fetch", { hostRoot: "example.com" });
    teardown(db, dir);
    expect(result).toBe("camofox_tier");
  });

  it("does NOT return host-specific row when no hostRoot option provided", () => {
    const { db, dir } = makeDb();
    const raw = db.rawDb;

    // Insert ONLY a host-specific row (no global row)
    raw.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run("scrapling", "puppeteer_tier", "web_fetch", "amazon.com", 0.9, 3000, 5);

    const graph = new ToolGraph(db);
    // Called without hostRoot — should NOT return the per-host row
    const result = graph.replan("scrapling", "web_fetch");
    teardown(db, dir);
    expect(result).toBeNull();
  });
});

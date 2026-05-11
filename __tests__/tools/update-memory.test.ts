import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { UpdateMemoryTool } from "../../src/tools/update-memory.js";
import { MemoryDatabase } from "../../src/memory/db.js";

let dbDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  dbDir = mkdtempSync(join(tmpdir(), "stackowl-update-mem-db-"));
  db = new MemoryDatabase(dbDir);
});

afterEach(() => {
  rmSync(dbDir, { recursive: true, force: true });
});

describe("UpdateMemoryTool", () => {
  it("adds a fact to the facts table", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers dark mode" },
      {} as any,
    );
    const facts = db.facts.getAllForUser();
    expect(facts.some((f) => f.fact === "Prefers dark mode")).toBe(true);
    expect(facts.find((f) => f.fact === "Prefers dark mode")?.category).toBe("preference");
  });

  it("maps 'Goals' section to active_goal category", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Goals", content: "Ship StackOwl v2 by Q3" },
      {} as any,
    );
    const facts = db.facts.getAllForUser();
    expect(facts.find((f) => f.fact === "Ship StackOwl v2 by Q3")?.category).toBe("active_goal");
  });

  it("remove operation retires matching facts", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers verbose logs" },
      {} as any,
    );
    await tool.execute(
      { operation: "remove", section: "Preferences", content: "verbose logs" },
      {} as any,
    );
    const remaining = db.facts.getHighConfidenceFacts();
    expect(remaining.some((f) => f.fact.includes("verbose logs"))).toBe(false);
  });

  it("update operation retires old fact and adds new one", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "About me", content: "Name: Bakir" },
      {} as any,
    );
    await tool.execute(
      { operation: "update", section: "About me", content: "Name: Bakir Talibov" },
      {} as any,
    );
    const active = db.facts.getHighConfidenceFacts();
    expect(active.some((f) => f.fact === "Name: Bakir Talibov")).toBe(true);
    expect(active.some((f) => f.fact === "Name: Bakir")).toBe(false);
  });

  it("setDb wires db after construction", async () => {
    const tool = new UpdateMemoryTool();
    tool.setDb(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers TDD" },
      {} as any,
    );
    expect(db.facts.getAllForUser().some((f) => f.fact === "Prefers TDD")).toBe(true);
  });

  it("rejects content over 200 characters", async () => {
    const tool = new UpdateMemoryTool(db);
    await expect(
      tool.execute(
        { operation: "add", section: "Preferences", content: "a".repeat(201) },
        {} as any,
      ),
    ).rejects.toThrow(/too long/i);
  });

  it("returns skip message when db not injected", async () => {
    const tool = new UpdateMemoryTool();
    const result = await tool.execute(
      { operation: "add", section: "Preferences", content: "Something" },
      {} as any,
    );
    expect(result).toContain("skipped");
  });
});

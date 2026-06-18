import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { LancePelletStore } from "../../src/pellets/lance-store.js";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("LancePelletStore — column migration", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "lance-test-"));
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it("adds successCount, failureCount, provenance columns on first init", async () => {
    const store = new LancePelletStore(dir);
    await store.init();
    const schema = await store.getColumnNames();
    expect(schema).toContain("success_count");
    expect(schema).toContain("failure_count");
    expect(schema).toContain("provenance");
  });

  it("is idempotent — second init does not throw", async () => {
    const store = new LancePelletStore(dir);
    await store.init();
    const store2 = new LancePelletStore(dir);
    await expect(store2.init()).resolves.not.toThrow();
  });

  it("migrates a pre-existing table that lacks the new columns", async () => {
    // Create a directory with an old-style table (no success_count/failure_count/provenance)
    const { connect } = await import("@lancedb/lancedb");
    const db = await connect(dir);
    const embedding = new Array(384).fill(0); // BGE-small-en-v1.5 default dim
    await db.createTable("pellets", [
      {
        id: "old-row", title: "t", generated_at: "", source: "", owls: "[]", tags: "[]",
        content: "", version: 1, supersedes: "", merged_from: "[]", last_merged_at: "",
        vector: embedding,
      },
    ]);

    // Now init the real store — should add the missing columns
    const store = new LancePelletStore(dir);
    await store.init();
    const cols = await store.getColumnNames();
    expect(cols).toContain("success_count");
    expect(cols).toContain("failure_count");
    expect(cols).toContain("provenance");
  });
});

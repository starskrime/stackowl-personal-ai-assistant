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
});

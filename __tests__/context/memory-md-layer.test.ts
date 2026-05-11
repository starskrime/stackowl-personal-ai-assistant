import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryMdLayer } from "../../src/context/layers/memory-md.js";

const TEST_DIR = join(tmpdir(), `stackowl-memory-md-test-${Date.now()}`);
const MEMORY_FILE = join(TEST_DIR, "MEMORY.md");

beforeEach(() => {
  mkdirSync(TEST_DIR, { recursive: true });
});

afterEach(() => {
  rmSync(TEST_DIR, { recursive: true, force: true });
});

function makeLayer(path: string) {
  return new MemoryMdLayer(path);
}

describe("MemoryMdLayer", () => {
  it("injects MEMORY.md content as Tier-0 context", async () => {
    writeFileSync(MEMORY_FILE, "# About me\n- Name: Bakir\n");
    const layer = makeLayer(MEMORY_FILE);
    const result = await layer.build({} as any, {} as any, new Map());
    expect(result).toContain("Name: Bakir");
    expect(result).toContain("<tier0_memory>");
  });

  it("returns empty string when MEMORY.md does not exist", async () => {
    const layer = makeLayer(join(TEST_DIR, "missing.md"));
    const result = await layer.build({} as any, {} as any, new Map());
    expect(result).toBe("");
  });

  it("always fires regardless of triage signals", () => {
    const layer = makeLayer(MEMORY_FILE);
    expect(layer.shouldFire({} as any)).toBe(true);
  });

  it("has priority 0 — highest in pipeline", () => {
    const layer = makeLayer(MEMORY_FILE);
    expect(layer.priority).toBe(0);
  });
});

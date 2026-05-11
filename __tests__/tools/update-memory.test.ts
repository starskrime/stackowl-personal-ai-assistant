import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdirSync, rmSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { UpdateMemoryTool } from "../../src/tools/update-memory.js";

const TEST_DIR = join(tmpdir(), `stackowl-update-memory-test-${Date.now()}`);
const MEMORY_FILE = join(TEST_DIR, "MEMORY.md");

beforeEach(() => {
  mkdirSync(TEST_DIR, { recursive: true });
  writeFileSync(
    MEMORY_FILE,
    "# About me\n- Name: Bakir\n\n# Preferences\n- Concise responses\n",
  );
});

afterEach(() => {
  rmSync(TEST_DIR, { recursive: true, force: true });
});

describe("UpdateMemoryTool", () => {
  it("adds a line to an existing section", async () => {
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    const result = await tool.execute(
      {
        operation: "add",
        section: "Preferences",
        content: "- TypeScript strict mode always on",
      },
      {} as any,
    );
    const content = readFileSync(MEMORY_FILE, "utf-8");
    expect(content).toContain("TypeScript strict mode always on");
    expect(content).toContain("Concise responses");
    expect(result).toContain("MEMORY.md updated");
  });

  it("creates a new section when section does not exist", async () => {
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await tool.execute(
      {
        operation: "add",
        section: "Key relationships",
        content: "- Alice: product manager, works on StackOwl",
      },
      {} as any,
    );
    const content = readFileSync(MEMORY_FILE, "utf-8");
    expect(content).toContain("# Key relationships");
    expect(content).toContain("Alice");
  });

  it("removes a matching line", async () => {
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await tool.execute(
      {
        operation: "remove",
        section: "Preferences",
        content: "Concise responses",
      },
      {} as any,
    );
    const content = readFileSync(MEMORY_FILE, "utf-8");
    expect(content).not.toContain("Concise responses");
  });

  it("rejects lines over 200 characters", async () => {
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await expect(
      tool.execute(
        { operation: "add", section: "X", content: "a".repeat(201) },
        {} as any,
      ),
    ).rejects.toThrow(/line too long/i);
  });

  it("rejects when file would exceed 150 lines", async () => {
    const big = Array.from({ length: 150 }, (_, i) => `- line ${i}`).join("\n");
    writeFileSync(MEMORY_FILE, big);
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await expect(
      tool.execute(
        { operation: "add", section: "X", content: "- one more line" },
        {} as any,
      ),
    ).rejects.toThrow(/150 lines/i);
  });

  it("updates an existing line matching a keyword", async () => {
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await tool.execute(
      {
        operation: "update",
        section: "About me",
        content: "- Name: Baker",
      },
      {} as any,
    );
    const content = readFileSync(MEMORY_FILE, "utf-8");
    expect(content).toContain("Baker");
  });
});

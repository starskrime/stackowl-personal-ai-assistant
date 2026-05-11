import { describe, it, expect, afterEach } from "vitest";
import { rm, readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SessionSaver } from "../../src/memory/session-saver.js";

const TEST_DIR = join(tmpdir(), `stackowl-session-saver-test-${Date.now()}`);

afterEach(async () => {
  await rm(TEST_DIR, { recursive: true, force: true });
});

// Use the simplest message shape that matches the actual type
const MESSAGES = [
  { role: "user" as const, content: "What is TypeScript?" },
  { role: "assistant" as const, content: "TypeScript is a typed superset of JavaScript." },
  { role: "user" as const, content: "How do I install it?" },
  { role: "assistant" as const, content: "Run `npm install -g typescript`." },
];

describe("SessionSaver", () => {
  it("writes a dated markdown file under the memory directory", async () => {
    const saver = new SessionSaver(TEST_DIR);
    const filePath = await saver.save(MESSAGES, "test-session-1");
    const files = await readdir(TEST_DIR);
    expect(files.length).toBe(1);
    expect(files[0]).toMatch(/^\d{4}-\d{2}-\d{2}-\d{4}\.md$/);
    expect(filePath).toBeTruthy();
  });

  it("writes last N messages (default 15)", async () => {
    const longMessages = Array.from({ length: 30 }, (_, i) => ({
      role: (i % 2 === 0 ? "user" : "assistant") as "user" | "assistant",
      content: `Message ${i}`,
    }));

    const saver = new SessionSaver(TEST_DIR, { messageCount: 15 });
    const filePath = await saver.save(longMessages, "long-session");
    const content = await readFile(filePath!, "utf-8");
    expect(content).toContain("Message 29");
    expect(content).not.toContain("Message 0");
  });

  it("returns null and does not throw when messages are empty", async () => {
    const saver = new SessionSaver(TEST_DIR);
    const result = await saver.save([], "empty-session");
    expect(result).toBeNull();
  });

  it("creates memory directory if it does not exist", async () => {
    const newDir = join(tmpdir(), `stackowl-session-saver-test-new-${Date.now()}`);
    try {
      const saver = new SessionSaver(newDir);
      const filePath = await saver.save(MESSAGES, "new-dir-session");
      expect(filePath).toBeTruthy();
      const files = await readdir(newDir);
      expect(files.length).toBe(1);
    } finally {
      await rm(newDir, { recursive: true, force: true });
    }
  });

  it("includes session metadata in the markdown file", async () => {
    const saver = new SessionSaver(TEST_DIR);
    const filePath = await saver.save(MESSAGES, "metadata-session");
    const content = await readFile(filePath!, "utf-8");
    expect(content).toContain("**Session ID:** metadata-session");
    expect(content).toContain("**Messages saved:** 4");
    expect(content).toContain("## Conversation");
  });

  it("formats messages with proper role labels", async () => {
    const saver = new SessionSaver(TEST_DIR);
    const filePath = await saver.save(MESSAGES, "format-session");
    const content = await readFile(filePath!, "utf-8");
    expect(content).toContain("**User**: What is TypeScript?");
    expect(content).toContain("**Owl**: TypeScript is a typed superset of JavaScript.");
  });
});

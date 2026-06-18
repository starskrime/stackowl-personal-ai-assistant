# Epic 3: Always-Loaded MEMORY.md Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Tier 0 memory — a `MEMORY.md` file loaded synchronously on every conversation turn before the existing async pipeline — plus an `UpdateMemoryTool` for the LLM to maintain it, plus a session saver that writes dated files when the session resets.

**Architecture:** `src/context/pipeline.ts` assembles context layers from `src/context/layers/`. We add one new layer — `MemoryMdLayer` (priority 0, synchronous `readFileSync`) — and prepend it before all existing layers. A new `UpdateMemoryTool` writes atomic changes (add/update/remove) to `MEMORY.md` with line-count and line-length guards. A `SessionSaverHook` fires on `/new` and writes the last N messages to a dated file under `~/.stackowl/workspace/memory/`. Existing async layers are untouched.

**Tech Stack:** TypeScript, Node 22, `node:fs` (synchronous read for Tier 0), `node:fs/promises` (session saver), `better-sqlite3` (already in use for other layers), Vitest.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/context/layers/memory-md.ts` | Create | `MemoryMdLayer` — synchronous MEMORY.md read at priority 0 |
| `src/context/pipeline.ts` | Modify | Prepend `MemoryMdLayer` to layer list |
| `src/tools/update-memory.ts` | Create | `UpdateMemoryTool` — add/update/remove MEMORY.md sections |
| `src/memory/session-saver.ts` | Create | `SessionSaver` — writes dated session files on /new |
| `__tests__/context/memory-md-layer.test.ts` | Create | Unit tests for `MemoryMdLayer` |
| `__tests__/tools/update-memory.test.ts` | Create | Unit tests for `UpdateMemoryTool` |
| `__tests__/memory/session-saver.test.ts` | Create | Unit tests for `SessionSaver` |

---

## Task 1: MemoryMdLayer — Tier 0 synchronous context injection

**Files:**
- Create: `src/context/layers/memory-md.ts`
- Create: `__tests__/context/memory-md-layer.test.ts`

- [ ] **Step 1.1: Write the failing test**

```typescript
// __tests__/context/memory-md-layer.test.ts
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
```

- [ ] **Step 1.2: Run test to confirm it fails**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant
npx vitest run __tests__/context/memory-md-layer.test.ts 2>&1 | tail -20
```

Expected: FAIL — `MemoryMdLayer` module does not exist.

- [ ] **Step 1.3: Implement MemoryMdLayer**

Check the `ContextLayer` interface shape first:

```bash
cat /ssd/projects/stackowl-personal-ai-assistant/src/context/layer.ts | head -40
```

Create `src/context/layers/memory-md.ts`:

```typescript
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { log } from "../../logger.js";
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

const DEFAULT_MEMORY_PATH = join(homedir(), ".stackowl", "workspace", "MEMORY.md");

export class MemoryMdLayer implements ContextLayer {
  name = "MemoryMdLayer";
  /** Priority 0 = runs first, before every other layer. */
  priority = 0;
  maxTokens = 800;
  produces = ["tier0_memory"];
  dependsOn: string[] = [];

  constructor(private readonly memoryPath: string = DEFAULT_MEMORY_PATH) {}

  getCacheKey(): string | null {
    // Not cached — always read fresh so updates are visible immediately.
    return null;
  }

  shouldFire(_triage: TriageSignals): boolean {
    // Always fire — this is unconditional Tier 0.
    return true;
  }

  async build(
    _req: ContextRequest,
    _triage: TriageSignals,
    _deps: LayerResults,
  ): Promise<string> {
    if (!existsSync(this.memoryPath)) {
      return "";
    }

    try {
      const content = readFileSync(this.memoryPath, "utf-8").trim();
      if (!content) return "";

      log.engine.debug("[MemoryMdLayer] Injecting MEMORY.md", {
        path: this.memoryPath,
        chars: content.length,
      });

      return `<tier0_memory>\n${content}\n</tier0_memory>`;
    } catch (err) {
      log.engine.error("[MemoryMdLayer] Failed to read MEMORY.md", err as Error, {
        path: this.memoryPath,
      });
      return "";
    }
  }
}
```

- [ ] **Step 1.4: Run test to confirm it passes**

```bash
npx vitest run __tests__/context/memory-md-layer.test.ts 2>&1 | tail -20
```

Expected: PASS — 4 tests passing.

- [ ] **Step 1.5: Commit**

```bash
git add src/context/layers/memory-md.ts __tests__/context/memory-md-layer.test.ts
git commit -m "feat(memory): MemoryMdLayer — synchronous Tier-0 MEMORY.md context injection"
```

---

## Task 2: Wire MemoryMdLayer into ContextPipeline

**Files:**
- Modify: wherever `ContextPipeline` is instantiated (find at step 2.1)

- [ ] **Step 2.1: Find where ContextPipeline is constructed**

```bash
grep -rn "new ContextPipeline\|ContextPipeline(" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|__tests__" | head -10
```

- [ ] **Step 2.2: Prepend MemoryMdLayer to the layers array**

At the construction site (typically the gateway builder or engine factory), add:

```typescript
import { MemoryMdLayer } from "./context/layers/memory-md.js";

// Before existing layers array:
const memoryMdLayer = new MemoryMdLayer();  // uses default ~/.stackowl/workspace/MEMORY.md

const layers: ContextLayer[] = [
  memoryMdLayer,    // ← prepend FIRST — Tier 0, runs synchronously
  // ... existing layers follow unchanged ...
];
```

- [ ] **Step 2.3: Create initial MEMORY.md for the user**

This step runs once — creates the file if it doesn't exist:

```bash
mkdir -p ~/.stackowl/workspace
cat > ~/.stackowl/workspace/MEMORY.md <<'EOF'
# About me
- Name: Bakir
- Timezone: UTC+4
- Primary language: English

# Current projects
- StackOwl personal AI assistant (TypeScript, Node 22)

# Preferences
- Concise responses, no filler
- TypeScript strict mode always on
- Root-cause fixes over patches

# Key relationships
(Add people and context here as needed)
EOF
```

- [ ] **Step 2.4: Smoke test — verify MEMORY.md appears in context**

```bash
npm run dev 2>&1 | head -20
```

Then in a chat session, ask: "What is my name?" — the owl should answer "Bakir" immediately without any retrieval delay.

- [ ] **Step 2.5: Commit**

```bash
git add src/  # whichever pipeline construction file changed
git commit -m "feat(memory): wire MemoryMdLayer as Tier-0 into ContextPipeline"
```

---

## Task 3: UpdateMemoryTool

**Files:**
- Create: `src/tools/update-memory.ts`
- Create: `__tests__/tools/update-memory.test.ts`
- Modify: tool registry (same file as Epic 2 Task 3.1)

- [ ] **Step 3.1: Write the failing test**

```typescript
// __tests__/tools/update-memory.test.ts
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
    await tool.execute({
      operation: "add",
      section: "Preferences",
      content: "- TypeScript strict mode always on",
    });
    const result = readFileSync(MEMORY_FILE, "utf-8");
    expect(result).toContain("TypeScript strict mode always on");
    expect(result).toContain("Concise responses");
  });

  it("creates a new section when section does not exist", async () => {
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await tool.execute({
      operation: "add",
      section: "Key relationships",
      content: "- Alice: product manager, works on StackOwl",
    });
    const result = readFileSync(MEMORY_FILE, "utf-8");
    expect(result).toContain("# Key relationships");
    expect(result).toContain("Alice");
  });

  it("removes a matching line", async () => {
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await tool.execute({
      operation: "remove",
      section: "Preferences",
      content: "Concise responses",
    });
    const result = readFileSync(MEMORY_FILE, "utf-8");
    expect(result).not.toContain("Concise responses");
  });

  it("rejects lines over 200 characters", async () => {
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await expect(
      tool.execute({ operation: "add", section: "X", content: "a".repeat(201) }),
    ).rejects.toThrow(/line too long/i);
  });

  it("rejects when file would exceed 150 lines", async () => {
    const big = Array.from({ length: 150 }, (_, i) => `- line ${i}`).join("\n");
    writeFileSync(MEMORY_FILE, big);
    const tool = new UpdateMemoryTool(MEMORY_FILE);
    await expect(
      tool.execute({ operation: "add", section: "X", content: "- one more line" }),
    ).rejects.toThrow(/150 lines/i);
  });
});
```

- [ ] **Step 3.2: Run test to confirm it fails**

```bash
npx vitest run __tests__/tools/update-memory.test.ts 2>&1 | tail -20
```

Expected: FAIL — `UpdateMemoryTool` module does not exist.

- [ ] **Step 3.3: Implement UpdateMemoryTool**

Create `src/tools/update-memory.ts`:

```typescript
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { log } from "../logger.js";

const DEFAULT_MEMORY_PATH = join(homedir(), ".stackowl", "workspace", "MEMORY.md");
const MAX_LINES = 150;
const MAX_LINE_LENGTH = 200;

export interface UpdateMemoryInput {
  operation: "add" | "update" | "remove";
  section: string;
  content: string;
}

export class UpdateMemoryTool {
  readonly name = "update_memory";
  readonly description =
    "Update MEMORY.md — the always-loaded Tier-0 memory. " +
    "Use to persist durable facts: user preferences, ongoing projects, key relationships. " +
    "Operations: add (append to section), update (replace matching line), remove (delete matching line).";

  constructor(private readonly memoryPath: string = DEFAULT_MEMORY_PATH) {}

  async execute(input: UpdateMemoryInput): Promise<string> {
    log.tool.debug("update_memory.execute: entry", { operation: input.operation, section: input.section });

    if (input.content.length > MAX_LINE_LENGTH) {
      throw new Error(`Line too long (${input.content.length} chars). Keep lines under ${MAX_LINE_LENGTH}.`);
    }

    mkdirSync(dirname(this.memoryPath), { recursive: true });

    const raw = existsSync(this.memoryPath)
      ? readFileSync(this.memoryPath, "utf-8")
      : "";

    let lines = raw.split("\n");

    if (input.operation === "add") {
      if (lines.length + 1 > MAX_LINES) {
        throw new Error(`MEMORY.md is at ${MAX_LINES} lines — remove stale entries before adding new ones.`);
      }
      const sectionHeader = `# ${input.section}`;
      const idx = lines.findIndex((l) => l.trim() === sectionHeader);
      if (idx === -1) {
        // Append new section at end
        lines = [...lines.filter((l) => l !== ""), "", sectionHeader, input.content, ""];
      } else {
        // Find end of section (next heading or EOF)
        let insertAt = idx + 1;
        while (insertAt < lines.length && !lines[insertAt].startsWith("#")) {
          insertAt++;
        }
        lines.splice(insertAt, 0, input.content);
      }
    } else if (input.operation === "remove") {
      const keyword = input.content.toLowerCase();
      lines = lines.filter((l) => !l.toLowerCase().includes(keyword));
    } else if (input.operation === "update") {
      const keyword = input.content.split(":")[0].toLowerCase();
      const replaceIdx = lines.findIndex((l) => l.toLowerCase().startsWith(keyword));
      if (replaceIdx !== -1) {
        lines[replaceIdx] = input.content;
      } else {
        // Fall back to add
        lines.push(input.content);
      }
    }

    writeFileSync(this.memoryPath, lines.join("\n"), "utf-8");
    log.tool.info("update_memory.execute: written", { operation: input.operation, lines: lines.length });
    return `MEMORY.md updated (${input.operation} in "${input.section}").`;
  }
}
```

- [ ] **Step 3.4: Run test to confirm it passes**

```bash
npx vitest run __tests__/tools/update-memory.test.ts 2>&1 | tail -20
```

Expected: PASS — 5 tests passing.

- [ ] **Step 3.5: Register UpdateMemoryTool**

In the tool registry file (identified in Epic 2, Task 3.1), add:

```typescript
import { UpdateMemoryTool } from "./update-memory.js";
// In registry setup:
registry.register(new UpdateMemoryTool());
```

- [ ] **Step 3.6: Commit**

```bash
git add src/tools/update-memory.ts __tests__/tools/update-memory.test.ts  # + registry
git commit -m "feat(memory): UpdateMemoryTool — LLM can maintain MEMORY.md with add/update/remove"
```

---

## Task 4: SessionSaver — dated session files on /new

**Files:**
- Create: `src/memory/session-saver.ts`
- Create: `__tests__/memory/session-saver.test.ts`
- Modify: wherever the `/new` or session-reset command fires (find at step 4.1)

- [ ] **Step 4.1: Find the session reset hook**

```bash
grep -rn "\/new\|onSessionReset\|reset.*command\|command.*new" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|__tests__" | head -20
```

- [ ] **Step 4.2: Write the failing test**

```typescript
// __tests__/memory/session-saver.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { rm, readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SessionSaver } from "../../src/memory/session-saver.js";
import type { ChatMessage } from "../../src/providers/base.js";

const TEST_DIR = join(tmpdir(), `stackowl-session-saver-test-${Date.now()}`);

afterEach(async () => {
  await rm(TEST_DIR, { recursive: true, force: true });
});

const MESSAGES: ChatMessage[] = [
  { role: "user", content: "What is TypeScript?" },
  { role: "assistant", content: "TypeScript is a typed superset of JavaScript." },
  { role: "user", content: "How do I install it?" },
  { role: "assistant", content: "Run `npm install -g typescript`." },
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
    const longMessages: ChatMessage[] = Array.from({ length: 30 }, (_, i) => ({
      role: i % 2 === 0 ? "user" : "assistant",
      content: `Message ${i}`,
    }));

    const saver = new SessionSaver(TEST_DIR, { messageCount: 15 });
    const filePath = await saver.save(longMessages, "long-session");
    const content = await readFile(filePath, "utf-8");
    // Last 15 messages = messages 15-29
    expect(content).toContain("Message 29");
    expect(content).not.toContain("Message 0");
  });

  it("returns null and does not throw when messages are empty", async () => {
    const saver = new SessionSaver(TEST_DIR);
    const result = await saver.save([], "empty-session");
    expect(result).toBeNull();
  });
});
```

- [ ] **Step 4.3: Run test to confirm it fails**

```bash
npx vitest run __tests__/memory/session-saver.test.ts 2>&1 | tail -20
```

Expected: FAIL — `SessionSaver` module does not exist.

- [ ] **Step 4.4: Implement SessionSaver**

Create `src/memory/session-saver.ts`:

```typescript
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import { log } from "../logger.js";
import type { ChatMessage } from "../providers/base.js";

const DEFAULT_MEMORY_DIR = join(homedir(), ".stackowl", "workspace", "memory");

function pad(n: number, len = 2): string {
  return String(n).padStart(len, "0");
}

function formatDate(d: Date): { date: string; slug: string } {
  const year = d.getFullYear();
  const month = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hour = pad(d.getHours());
  const min = pad(d.getMinutes());
  return { date: `${year}-${month}-${day}`, slug: `${hour}${min}` };
}

export interface SessionSaverOptions {
  messageCount?: number;
}

export class SessionSaver {
  private messageCount: number;

  constructor(
    private readonly memoryDir: string = DEFAULT_MEMORY_DIR,
    options: SessionSaverOptions = {},
  ) {
    this.messageCount = options.messageCount ?? 15;
  }

  async save(messages: ChatMessage[], sessionId: string): Promise<string | null> {
    if (!messages.length) return null;

    const recent = messages.slice(-this.messageCount);
    const now = new Date();
    const { date, slug } = formatDate(now);

    await mkdir(this.memoryDir, { recursive: true });

    const filename = `${date}-${slug}.md`;
    const filePath = join(this.memoryDir, filename);

    const lines: string[] = [
      `# Session: ${date} ${now.toTimeString().slice(0, 8)}`,
      ``,
      `**Session ID:** ${sessionId}`,
      `**Messages saved:** ${recent.length}`,
      ``,
      `## Conversation`,
      ``,
    ];

    for (const msg of recent) {
      const role = msg.role === "user" ? "**User**" : "**Owl**";
      lines.push(`${role}: ${typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content)}`);
      lines.push(``);
    }

    await writeFile(filePath, lines.join("\n"), "utf-8");
    log.engine.info(`[SessionSaver] Saved session to ${filePath}`);
    return filePath;
  }
}
```

- [ ] **Step 4.5: Run test to confirm it passes**

```bash
npx vitest run __tests__/memory/session-saver.test.ts 2>&1 | tail -20
```

Expected: PASS — 3 tests passing.

- [ ] **Step 4.6: Wire SessionSaver into /new handler**

At the session-reset hook location found in Step 4.1, add:

```typescript
import { SessionSaver } from "../memory/session-saver.js";

// In the /new or session-reset handler:
const saver = new SessionSaver();
await saver.save(currentSessionMessages, sessionId);
// Clear currentSessionMessages after save
```

- [ ] **Step 4.7: Run full test suite for regressions**

```bash
npx vitest run 2>&1 | tail -30
```

Expected: all previously-passing tests still pass.

- [ ] **Step 4.8: Final commit**

```bash
git add src/memory/session-saver.ts __tests__/memory/session-saver.test.ts  # + hook file
git commit -m "feat(memory): SessionSaver writes dated memory files on /new; wire into reset handler"
```

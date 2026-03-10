/**
 * StackOwl — Memory Search Tool
 *
 * Provides semantic search through memory and past conversations.
 * Uses embeddings for similarity search (with fallback to keyword).
 */

import type { ToolContext } from "../../tools/registry.js";
import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";

interface MemoryEntry {
  id: string;
  content: string;
  timestamp: number;
  source: string;
}

interface ScoredEntry extends MemoryEntry {
  relevance: number;
}

const DEFAULT_MEMORY_FILE = "memory.md";
const DEFAULT_MAX_RESULTS = 5;

export class MemorySearchTool {
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  definition = {
    name: "memory_search",
    description: `Search through your long-term memory and past conversations.

Examples:
- memory_search query="user preferences": Search memory for user preferences
- memory_search query="project details" limit=10: Get 10 relevant memories

Use this to recall facts, preferences, or context from previous sessions.`,
    parameters: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "What to search for",
        },
        limit: {
          type: "number",
          description: "Maximum results (default: 5)",
        },
      },
      required: ["query"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const query = args["query"] as string;
    const limit = (args["limit"] as number) || DEFAULT_MAX_RESULTS;

    if (!query) {
      return "ERROR: query is required";
    }

    try {
      // Load memory from various sources
      const memories = await this.loadMemories();

      // Search using keyword matching (simple approach without embeddings)
      const results = this.searchMemories(query, memories, limit);

      if (results.length === 0) {
        return JSON.stringify({
          query,
          results: [],
          message: "No relevant memories found",
        });
      }

      return JSON.stringify(
        {
          query,
          results: results.map((r) => ({
            source: r.source,
            content: r.content.slice(0, 500),
            relevance: r.relevance,
          })),
        },
        null,
        2,
      );
    } catch (error) {
      return JSON.stringify({ error: String(error) });
    }
  }

  private async loadMemories(): Promise<MemoryEntry[]> {
    const memories: MemoryEntry[] = [];

    // Load from memory.md
    const memoryPath = join(this.workspacePath, DEFAULT_MEMORY_FILE);
    if (existsSync(memoryPath)) {
      try {
        const content = await readFile(memoryPath, "utf-8");
        const lines = content.split("\n").filter((l) => l.trim());

        let currentEntry = "";
        for (const line of lines) {
          if (line.startsWith("## ") || line.startsWith("### ")) {
            if (currentEntry) {
              memories.push({
                id: `memory_${memories.length}`,
                content: currentEntry,
                timestamp: Date.now(),
                source: "memory.md",
              });
            }
            currentEntry = line;
          } else {
            currentEntry += "\n" + line;
          }
        }
        // Add last entry
        if (currentEntry) {
          memories.push({
            id: `memory_${memories.length}`,
            content: currentEntry,
            timestamp: Date.now(),
            source: "memory.md",
          });
        }
      } catch (error) {
        console.error("[MemorySearch] Failed to load memory.md:", error);
      }
    }

    // Load from daily memory files
    const memoryDir = join(this.workspacePath, "memory");
    if (existsSync(memoryDir)) {
      try {
        const files = await readdir(memoryDir);
        for (const file of files.filter((f) => f.endsWith(".md"))) {
          const content = await readFile(join(memoryDir, file), "utf-8");
          memories.push({
            id: `daily_${file}`,
            content: content.slice(0, 2000),
            timestamp: Date.now(),
            source: `memory/${file}`,
          });
        }
      } catch (error) {
        console.error("[MemorySearch] Failed to load daily memories:", error);
      }
    }

    return memories;
  }

  private searchMemories(
    query: string,
    memories: MemoryEntry[],
    limit: number,
  ): ScoredEntry[] {
    const queryLower = query.toLowerCase();
    const queryWords = queryLower.split(/\W+/).filter((w) => w.length > 2);

    const scored: ScoredEntry[] = [];

    for (const entry of memories) {
      const contentLower = entry.content.toLowerCase();
      let score = 0;

      // Exact phrase match (highest weight)
      if (contentLower.includes(queryLower)) {
        score += 10;
      }

      // Word matches
      for (const word of queryWords) {
        if (contentLower.includes(word)) {
          score += 3;
        }
      }

      // Title/header match bonus
      const lines = entry.content.split("\n");
      for (const line of lines.slice(0, 3)) {
        if (line.startsWith("#") && line.toLowerCase().includes(queryLower)) {
          score += 5;
        }
      }

      if (score > 0) {
        scored.push({ ...entry, relevance: score });
      }
    }

    // Sort by relevance and return top results
    scored.sort((a, b) => b.relevance - a.relevance);
    return scored.slice(0, limit);
  }
}

export class MemoryGetTool {
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  definition = {
    name: "memory_get",
    description: `Get specific memory file contents.

Examples:
- memory_get: Get full memory.md contents
- memory_get file="2024-01-15": Get specific daily memory`,
    parameters: {
      type: "object" as const,
      properties: {
        file: {
          type: "string",
          description: "Memory file name (without .md). Default: memory.md",
        },
      },
    },
  };

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const file = (args["file"] as string) || DEFAULT_MEMORY_FILE;
    const filePath = file.endsWith(".md")
      ? join(this.workspacePath, file)
      : join(this.workspacePath, "memory", `${file}.md`);

    if (!existsSync(filePath)) {
      return JSON.stringify({ error: `Memory file not found: ${file}` });
    }

    try {
      const content = await readFile(filePath, "utf-8");
      return JSON.stringify(
        {
          file,
          content,
          size: content.length,
        },
        null,
        2,
      );
    } catch (error) {
      return JSON.stringify({ error: String(error) });
    }
  }
}

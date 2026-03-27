/**
 * StackOwl — Memory Consolidator
 *
 * Extracts key facts from a session and appends them to workspace/memory.md.
 * This file is injected into the system prompt on startup so the owl
 * remembers important things across sessions.
 */

import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import { log } from "../logger.js";

const MEMORY_FILE = "memory.md";
const MAX_MEMORY_CHARS = 6000;

export class MemoryConsolidator {
  private provider: ModelProvider;
  private owl: OwlInstance;
  private workspacePath: string;

  constructor(
    provider: ModelProvider,
    owl: OwlInstance,
    workspacePath: string,
  ) {
    this.provider = provider;
    this.owl = owl;
    this.workspacePath = workspacePath;
  }

  /**
   * Load the current memory file contents for system prompt injection.
   */
  static async loadMemory(workspacePath: string): Promise<string> {
    const memPath = join(workspacePath, MEMORY_FILE);
    if (!existsSync(memPath)) return "";
    try {
      const content = await readFile(memPath, "utf-8");
      // Return the most recent portion if too long (newest facts are at the bottom)
      if (content.length > MAX_MEMORY_CHARS) {
        return (
          "...[older memory trimmed]\n\n" +
          content.slice(content.length - MAX_MEMORY_CHARS)
        );
      }
      return content;
    } catch {
      return "";
    }
  }

  /**
   * Extract key facts from the session and append them to memory.md.
   * Only runs if the session has at least 4 messages (2 turns).
   */
  async extractAndAppend(messages: ChatMessage[]): Promise<void> {
    const relevant = messages.filter(
      (m) => m.role === "user" || m.role === "assistant",
    );
    if (relevant.length < 4) return;

    const transcript = relevant
      .slice(-20) // Use last 20 messages to stay focused
      .map(
        (m) => `[${m.role.toUpperCase()}]: ${m.content?.slice(0, 400) ?? ""}`,
      )
      .join("\n\n");

    const prompt =
      `Analyze this conversation and extract 2-5 IMPORTANT facts worth remembering long-term.\n` +
      `Focus on: decisions made, user preferences stated, project details, important context.\n` +
      `Ignore small talk and transient details.\n\n` +
      `CONVERSATION:\n${transcript}\n\n` +
      `Return ONLY a JSON array of strings, like:\n` +
      `["User prefers Rust over Go", "Project uses PostgreSQL", "User wants concise answers"]\n` +
      `If nothing important was discussed, return an empty array: []`;

    try {
      const response = await this.provider.chat([
        {
          role: "system",
          content:
            "You are a memory extraction assistant. Output only valid JSON.",
        },
        { role: "user", content: prompt },
      ]);

      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```")) {
        jsonStr = jsonStr
          .replace(/^```json?/, "")
          .replace(/```$/, "")
          .trim();
      }

      const facts: string[] = JSON.parse(jsonStr);
      if (!Array.isArray(facts) || facts.length === 0) return;

      const date = new Date().toISOString().split("T")[0];
      const section =
        `\n## Session Facts — ${date} (${this.owl.persona.name})\n` +
        facts.map((f) => `- ${f}`).join("\n") +
        "\n";

      const memPath = join(this.workspacePath, MEMORY_FILE);
      const existing = existsSync(memPath)
        ? await readFile(memPath, "utf-8")
        : "# StackOwl Persistent Memory\n\nFacts extracted from conversations.\n";

      await writeFile(memPath, existing + section, "utf-8");
      log.memory.info(`Saved ${facts.length} fact(s) to memory.md`);
    } catch (error) {
      log.memory.warn(
        `Consolidation failed (non-fatal): ${error instanceof Error ? error.message : error}`,
      );
    }
  }
}

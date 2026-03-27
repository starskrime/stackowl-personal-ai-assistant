/**
 * StackOwl — Instincts Registry
 *
 * Loads and manages Instincts from INSTINCT.md files.
 * Instincts are reactive skills that fire automatically based on conditions.
 */

import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import matter from "gray-matter";

export type InstinctTrigger = "context" | "schedule" | "event" | "perch";
export type InstinctPriority = "low" | "medium" | "high" | "critical";

export interface Instinct {
  name: string;
  trigger: InstinctTrigger;
  conditions: string[];
  relevantOwls: string[]; // List of owl names that possess this instinct
  priority: InstinctPriority;
  /** The instructions for how to act when triggered */
  actionPrompt: string;
  sourcePath: string;
}

export class InstinctRegistry {
  private instincts: Map<string, Instinct> = new Map();
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  /**
   * Load all instincts from built-in defaults and workspace.
   */
  async loadAll(): Promise<void> {
    // Load built-in instincts from src/instincts/defaults/
    const defaultsDir = new URL("./defaults/", import.meta.url).pathname;
    if (existsSync(defaultsDir)) {
      await this.loadFromDirectory(defaultsDir);
    }

    // Load custom instincts from workspace/instincts/
    const customDir = join(this.workspacePath, "instincts");
    if (existsSync(customDir)) {
      await this.loadFromDirectory(customDir);
    }
  }

  private async loadFromDirectory(dirPath: string): Promise<void> {
    let entries: string[];
    try {
      const dirEntries = await readdir(dirPath, { withFileTypes: true });
      entries = dirEntries.filter((e) => e.isDirectory()).map((e) => e.name);
    } catch {
      return; // Directory doesn't exist or isn't readable
    }

    for (const entry of entries) {
      const mdPath = join(dirPath, entry, "INSTINCT.md");
      if (!existsSync(mdPath)) continue;

      try {
        const instinct = await this.parseInstinctMd(mdPath);
        this.instincts.set(instinct.name.toLowerCase(), instinct);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error(
          `[InstinctRegistry] Failed to load from ${mdPath}: ${msg}`,
        );
      }
    }
  }

  private async parseInstinctMd(filePath: string): Promise<Instinct> {
    const raw = await readFile(filePath, "utf-8");
    const { data, content } = matter(raw);

    if (!data.name || typeof data.name !== "string") {
      throw new Error(`INSTINCT.md missing required "name" field: ${filePath}`);
    }

    return {
      name: data.name as string,
      trigger: (data.trigger as InstinctTrigger) ?? "context",
      conditions: Array.isArray(data.conditions) ? data.conditions : [],
      relevantOwls: Array.isArray(data.relevant_owls)
        ? data.relevant_owls
        : ["*"],
      priority: (data.priority as InstinctPriority) ?? "medium",
      actionPrompt: content.trim(),
      sourcePath: filePath,
    };
  }

  get(name: string): Instinct | undefined {
    return this.instincts.get(name.toLowerCase());
  }

  listAll(): Instinct[] {
    return Array.from(this.instincts.values());
  }

  /**
   * Get instincts that trigger via 'context' for a specific owl.
   */
  getContextInstincts(owlName: string): Instinct[] {
    const title = owlName.toLowerCase();
    return this.listAll().filter(
      (inst) =>
        inst.trigger === "context" &&
        (inst.relevantOwls.includes("*") ||
          inst.relevantOwls.map((o) => o.toLowerCase()).includes(title)),
    );
  }
}

import { log } from "../logger.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ToolDefinition } from "../providers/base.js";
import type { MemoryDatabase, FactCategory } from "../memory/db.js";

export const SECTION_TO_CATEGORY: Record<string, FactCategory> = {
  preferences: "preference",
  preference: "preference",
  "about me": "personal",
  personal: "personal",
  goals: "active_goal",
  "active goals": "active_goal",
  active_goals: "active_goal",
  relationships: "relationship",
  "key relationships": "relationship",
  relationship: "relationship",
  habits: "habit",
  habit: "habit",
  decisions: "decision",
  decision: "decision",
};

const MAX_LINE_LENGTH = 200;

export interface UpdateMemoryInput {
  operation: "add" | "update" | "remove";
  section: string;
  content: string;
}

export class UpdateMemoryTool implements ToolImplementation {
  definition: ToolDefinition = {
    name: "update_memory",
    description:
      "Persist durable facts about the user: preferences, goals, relationships, decisions. " +
      "Operations: add (store new fact), update (replace matching fact), remove (retire matching fact). " +
      "Each fact is stored in the SQLite facts table and surfaces automatically in Tier-0 context.",
    parameters: {
      type: "object",
      properties: {
        operation: {
          type: "string",
          enum: ["add", "update", "remove"],
          description: "add — store new fact; update — replace matching; remove — retire matching",
        },
        section: {
          type: "string",
          description:
            'Semantic category: "Preferences", "About me", "Goals", "Relationships", "Habits", "Decisions"',
        },
        content: {
          type: "string",
          description: "The fact to store, update, or remove (max 200 chars)",
        },
      },
      required: ["operation", "section", "content"],
    },
  };

  category = "filesystem" as const;
  source = "builtin";

  private db?: MemoryDatabase;

  constructor(db?: MemoryDatabase) {
    this.db = db;
  }

  setDb(db: MemoryDatabase): void {
    this.db = db;
  }

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const input = args as unknown as UpdateMemoryInput;

    log.tool.debug("update_memory.execute: entry", {
      operation: input.operation,
      section: input.section,
    });

    if (input.content.length > MAX_LINE_LENGTH) {
      const err = new Error(
        `Content too long (${input.content.length} chars). Keep under ${MAX_LINE_LENGTH}.`,
      );
      log.tool.error("update_memory.execute: content too long", err, {
        contentLen: input.content.length,
      });
      throw err;
    }

    if (!this.db) {
      log.tool.warn("update_memory.execute: no db injected — operation dropped", {
        operation: input.operation,
      });
      return `Memory operation skipped (db not ready).`;
    }

    const category =
      SECTION_TO_CATEGORY[input.section.toLowerCase()] ?? "preference";

    if (input.operation === "add") {
      this.db.facts.add({
        userId: "default",
        owlName: "default",
        fact: input.content,
        category,
        confidence: 0.9,
        source: "explicit",
      });
      log.tool.info("update_memory.execute: fact added", { category, content: input.content.slice(0, 60) });
      return `Fact stored in "${category}".`;
    }

    if (input.operation === "remove") {
      const keyword = input.content.toLowerCase();
      const all = this.db.facts.getAllForUser();
      const matches = all.filter((f) => f.fact.toLowerCase().includes(keyword) && f.category === category);
      for (const f of matches) {
        this.db.facts.retire(f.id);
      }
      log.tool.info("update_memory.execute: facts retired", { count: matches.length, category });
      return `Retired ${matches.length} fact(s) matching "${input.content}".`;
    }

    if (input.operation === "update") {
      const keyword = input.content.split(":")[0].toLowerCase().trim();
      const all = this.db.facts.getAllForUser();
      const matches = all.filter(
        (f) => f.fact.toLowerCase().startsWith(keyword) && f.category === category,
      );
      for (const f of matches) {
        this.db.facts.retire(f.id);
      }
      this.db.facts.add({
        userId: "default",
        owlName: "default",
        fact: input.content,
        category,
        confidence: 0.9,
        source: "explicit",
      });
      log.tool.info("update_memory.execute: fact updated", { retired: matches.length, category });
      return `Updated fact in "${category}" (retired ${matches.length} old, added 1 new).`;
    }

    return "Unknown operation.";
  }
}

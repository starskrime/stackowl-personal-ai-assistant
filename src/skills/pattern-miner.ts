/**
 * StackOwl — Skill Pattern Miner
 *
 * The GROWTH engine. Discovers new skills organically from conversation history
 * rather than waiting for explicit capability gaps to be reported.
 *
 * How it works:
 *   1. Scan recent sessions for successful multi-step tool sequences
 *   2. Group similar sequences by the user request that triggered them
 *   3. If a pattern appears ≥ MIN_PATTERN_FREQUENCY times with no existing skill
 *      covering it → crystallize as a new SKILL.md
 *   4. Skip patterns already covered by an existing skill (checked by description overlap)
 *
 * This is inspired by LATS (Language Agent Tree Search) — successful paths
 * through the tool-use tree become skills for future use.
 *
 * Example mined pattern:
 *   User asks "summarize this article" 3 times →
 *   Miner sees: web_crawl → run_shell_command("echo $content | head -50") pattern →
 *   Creates skill: "summarize_article" with those exact steps
 */

import { writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ModelProvider } from "../providers/base.js";
import type { ChatMessage } from "../providers/base.js";
import type { SessionStore } from "../memory/store.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { SkillsRegistry } from "./registry.js";
import { SkillParser } from "./parser.js";
import { ConfigContextBuilder } from "./config-context.js";
import type { ToolRegistry } from "../tools/registry.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

interface ToolSequence {
  /** User message that initiated this sequence */
  userRequest: string;
  /** Ordered list of tool names called */
  tools: string[];
  /** The final assistant response (to judge success) */
  finalResponse: string;
  /** Whether the sequence produced a successful result */
  succeeded: boolean;
}

interface PatternGroup {
  /** Normalized description of the pattern */
  description: string;
  /** All matching sequences */
  sequences: ToolSequence[];
  /** Representative user request */
  representativeRequest: string;
}

interface MinedSkill {
  name: string;
  filePath: string;
  content: string;
}

// ─── Constants ───────────────────────────────────────────────────

/** Minimum times a pattern must appear before crystallizing into a skill */
const MIN_PATTERN_FREQUENCY = 2;

/** How many recent sessions to scan */
const MAX_SESSIONS_TO_SCAN = 10;

// ─── Pattern Miner ───────────────────────────────────────────────

export class PatternMiner {
  private parser: SkillParser;
  private configContext: ConfigContextBuilder;

  constructor(
    private provider: ModelProvider,
    private sessionStore: SessionStore,
    private config: StackOwlConfig,
    toolRegistry?: ToolRegistry,
    skillsRegistry?: SkillsRegistry,
  ) {
    this.parser = new SkillParser();
    this.configContext = new ConfigContextBuilder(
      config,
      toolRegistry,
      skillsRegistry,
    );
  }

  /**
   * Run a full pattern mining pass.
   * Returns the names of any new skills created.
   */
  async mine(registry: SkillsRegistry, skillsDir: string): Promise<string[]> {
    log.engine.info("[PatternMiner] Starting pattern mining pass...");

    const sessions = await this.sessionStore.listSessions();
    if (sessions.length === 0) return [];

    const recentSessions = sessions.slice(0, MAX_SESSIONS_TO_SCAN);

    // Extract all tool sequences from all sessions
    const allSequences: ToolSequence[] = [];
    for (const session of recentSessions) {
      allSequences.push(...this.extractSequences(session.messages));
    }

    if (allSequences.length === 0) {
      log.engine.info(
        "[PatternMiner] No tool sequences found in recent sessions",
      );
      return [];
    }

    // Filter to only successful sequences (failed ones teach nothing useful here)
    const successfulSequences = allSequences.filter(
      (s) => s.succeeded && s.tools.length >= 2,
    );

    if (successfulSequences.length === 0) {
      log.engine.info(
        "[PatternMiner] No successful multi-tool sequences found",
      );
      return [];
    }

    // Group similar sequences by tool pattern
    const patterns = await this.groupPatterns(successfulSequences);

    // Filter to patterns that appear enough times
    const frequentPatterns = patterns.filter(
      (p) => p.sequences.length >= MIN_PATTERN_FREQUENCY,
    );

    if (frequentPatterns.length === 0) {
      log.engine.info(
        "[PatternMiner] No frequent patterns found (need ≥ 2 occurrences)",
      );
      return [];
    }

    // Filter out patterns already covered by existing skills
    const existingSkills = registry.listEnabled();
    const uncoveredPatterns = await this.filterUncoveredPatterns(
      frequentPatterns,
      existingSkills,
    );

    if (uncoveredPatterns.length === 0) {
      log.engine.info(
        "[PatternMiner] All frequent patterns are already covered by existing skills",
      );
      return [];
    }

    log.engine.info(
      `[PatternMiner] Found ${uncoveredPatterns.length} uncovered pattern(s) — crystallizing into skills`,
    );

    // Generate new skills for uncovered patterns
    const newSkillNames: string[] = [];
    for (const pattern of uncoveredPatterns.slice(0, 3)) {
      // cap at 3 new skills per pass
      try {
        const skill = await this.crystallize(pattern, skillsDir);
        if (skill) {
          // Register the new skill immediately
          try {
            const parsed = await this.parser.parse(skill.filePath);
            registry.register(parsed);
            newSkillNames.push(skill.name);
            log.engine.info(
              `[PatternMiner] ✓ New skill crystallized: "${skill.name}"`,
            );
          } catch (parseErr) {
            log.engine.warn(
              `[PatternMiner] Failed to parse crystallized skill "${skill.name}": ${parseErr instanceof Error ? parseErr.message : String(parseErr)}`,
            );
          }
        }
      } catch (err) {
        log.engine.warn(
          `[PatternMiner] Failed to crystallize pattern: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    return newSkillNames;
  }

  /**
   * Extract tool sequences from a session's message list.
   * A sequence starts at a user message and ends at the next user message.
   */
  private extractSequences(messages: ChatMessage[]): ToolSequence[] {
    const sequences: ToolSequence[] = [];
    let i = 0;

    while (i < messages.length) {
      if (messages[i].role !== "user") {
        i++;
        continue;
      }

      const userRequest = messages[i].content ?? "";
      const tools: string[] = [];
      let finalResponse = "";
      let j = i + 1;

      // Collect everything until the next user message
      while (j < messages.length && messages[j].role !== "user") {
        const msg = messages[j];
        if (
          msg.role === "assistant" &&
          msg.toolCalls &&
          msg.toolCalls.length > 0
        ) {
          for (const tc of msg.toolCalls) {
            tools.push(tc.name);
          }
        }
        if (msg.role === "assistant" && !msg.toolCalls?.length && msg.content) {
          finalResponse = msg.content;
        }
        j++;
      }

      if (tools.length >= 2 && finalResponse) {
        // Heuristic success detection: response is substantive and doesn't contain error markers
        const succeeded =
          finalResponse.length > 50 &&
          !finalResponse.toLowerCase().includes("i couldn't") &&
          !finalResponse.toLowerCase().includes("i was unable") &&
          !finalResponse.toLowerCase().includes("failed to") &&
          !finalResponse.includes("EXHAUSTED");

        sequences.push({ userRequest, tools, finalResponse, succeeded });
      }

      i = j;
    }

    return sequences;
  }

  /**
   * Group sequences by their tool pattern using LLM clustering.
   * For small datasets uses simple tool-name-based grouping.
   */
  private async groupPatterns(
    sequences: ToolSequence[],
  ): Promise<PatternGroup[]> {
    // Simple grouping: cluster by the sorted set of tool names
    // This catches "web_crawl + run_shell_command" appearing multiple times
    const groups = new Map<string, ToolSequence[]>();

    for (const seq of sequences) {
      // Key = sorted unique tool names (order-independent grouping)
      const key = [...new Set(seq.tools)].sort().join("+");
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(seq);
    }

    // Convert to PatternGroup with LLM-generated description
    const result: PatternGroup[] = [];
    for (const [key, seqs] of groups) {
      if (seqs.length < MIN_PATTERN_FREQUENCY) continue;

      // Use the most common user request as representative
      const representative = seqs[0].userRequest;

      result.push({
        description: `Uses ${key.replace(/\+/g, ", ")} to respond to: "${representative.slice(0, 60)}"`,
        sequences: seqs,
        representativeRequest: representative,
      });
    }

    return result;
  }

  /**
   * Filter out patterns that are already covered by existing skills
   * using description overlap check.
   */
  private async filterUncoveredPatterns(
    patterns: PatternGroup[],
    existingSkills: import("./types.js").Skill[],
  ): Promise<PatternGroup[]> {
    if (existingSkills.length === 0) return patterns;

    const existingDescriptions = existingSkills.map((s) =>
      `${s.name} ${s.description}`.toLowerCase(),
    );

    return patterns.filter((pattern) => {
      const patternWords = pattern.representativeRequest
        .toLowerCase()
        .split(/\W+/)
        .filter((w) => w.length > 3);

      // If > 40% of pattern words appear in any existing skill description, skip
      for (const desc of existingDescriptions) {
        const overlap = patternWords.filter((w) => desc.includes(w)).length;
        if (overlap / patternWords.length > 0.4) return false;
      }

      return true;
    });
  }

  /**
   * Crystallize a pattern group into a new SKILL.md file.
   */
  private async crystallize(
    pattern: PatternGroup,
    skillsDir: string,
  ): Promise<MinedSkill | null> {
    const exampleRequests = pattern.sequences
      .slice(0, 3)
      .map((s) => `"${s.userRequest.slice(0, 80)}"`)
      .join("\n- ");

    const exampleToolSequences = pattern.sequences
      .slice(0, 3)
      .map((s) => `Tools used: ${s.tools.join(" → ")}`)
      .join("\n");

    const prompt =
      `You are writing a SKILL.md for an AI assistant called StackOwl.\n` +
      `This skill was MINED from repeated successful user interactions — it describes a pattern\n` +
      `the assistant already does well and should continue doing.\n\n` +
      `PATTERN OBSERVED (appeared ${pattern.sequences.length} times):\n` +
      `User requests that triggered this pattern:\n- ${exampleRequests}\n\n` +
      `Tool sequences used successfully:\n${exampleToolSequences}\n\n` +
      `Write a SKILL.md that teaches the LLM to handle this class of requests.\n` +
      `The skill name must capture what the USER wants, not which tools are used.\n\n` +
      `${this.configContext.toPromptBlock()}\n\n` +
      `Output ONLY valid SKILL.md content:\n` +
      `---\n` +
      `name: verb_noun_skill_name\n` +
      `description: When [user asks X], do [Y] by [Z]\n` +
      `openclaw:\n` +
      `  emoji: 🔧\n` +
      `---\n\n` +
      `# How to [accomplish the task]\n\n` +
      `1. [First step with exact tool call]\n` +
      `2. [Second step]\n` +
      `...\n\n` +
      `## When to use this skill\n` +
      `[List 3-5 example user phrases that should trigger this skill]\n\n` +
      `Rules:\n` +
      `- name: snake_case verb-noun, describes user's goal\n` +
      `- description: "When [trigger], [action]"\n` +
      `- Instructions: numbered, concrete, with exact tool calls\n` +
      `- Output ONLY the SKILL.md, no explanation`;

    const response = await this.provider.chat(
      [{ role: "user", content: prompt }],
      this.config.defaultModel,
      { temperature: 0.4, maxTokens: 1024 },
    );

    const content = response.content.trim();

    // Validate structure
    if (!content.includes("---") || !content.includes("name:")) {
      log.engine.warn(
        "[PatternMiner] Crystallized skill missing frontmatter, skipping",
      );
      return null;
    }

    // Extract skill name
    const nameMatch = content.match(/^name:\s*(\S+)/m);
    const skillName = nameMatch
      ? nameMatch[1].replace(/[^a-z0-9_]/gi, "_").toLowerCase()
      : `mined_skill_${Date.now()}`;

    // Don't overwrite existing skills
    const skillDir = join(skillsDir, skillName);
    if (existsSync(skillDir)) {
      log.engine.info(
        `[PatternMiner] Skill "${skillName}" already exists — skipping`,
      );
      return null;
    }

    const filePath = join(skillDir, "SKILL.md");
    await mkdir(skillDir, { recursive: true });
    await writeFile(filePath, content, "utf-8");

    return { name: skillName, filePath, content };
  }
}

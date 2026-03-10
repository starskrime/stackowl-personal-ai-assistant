/**
 * StackOwl — Skill Context Injector
 *
 * Dynamically injects relevant skills into LLM context based on user input.
 * Can also automatically search ClawHub for missing skills.
 */

import chalk from "chalk";
import { SkillsRegistry } from "./registry.js";
import { ClawHubClient, SkillSelector } from "./clawhub.js";
import type { Skill } from "./types.js";

export interface SkillContextOptions {
  /** Maximum skills to inject per message */
  maxSkills?: number;
  /** Whether to auto-search ClawHub for missing skills */
  autoSearchClawHub?: boolean;
  /** ClawHub target directory for installing new skills */
  clawHubTargetDir?: string;
}

export class SkillContextInjector {
  private registry: SkillsRegistry;
  private selector: SkillSelector;
  private clawHub: ClawHubClient | null;
  private options: Required<SkillContextOptions>;
  private recentlySearched: Set<string> = new Set();
  /** Relevance cache: message prefix → matched skill names. Cleared on refreshSelector(). */
  private relevanceCache: Map<string, string[]> = new Map();
  private static readonly CACHE_KEY_LENGTH = 100;

  constructor(registry: SkillsRegistry, options: SkillContextOptions = {}) {
    this.registry = registry;
    this.selector = new SkillSelector();
    this.clawHub = null;
    this.options = {
      maxSkills: options.maxSkills ?? 3,
      autoSearchClawHub: options.autoSearchClawHub ?? true,
      clawHubTargetDir: options.clawHubTargetDir ?? "./workspace/skills",
    };

    // Register all loaded skills with the selector
    this.refreshSelector();
  }

  /**
   * Set ClawHub client for remote skill search.
   */
  setClawHub(client: ClawHubClient): void {
    this.clawHub = client;
  }

  /**
   * Refresh the skill selector with current registry contents.
   * Also clears the relevance cache since the skill set changed.
   */
  refreshSelector(): void {
    this.relevanceCache.clear();
    this.selector.clear();
    for (const skill of this.registry.listEnabled()) {
      this.selector.register({
        name: skill.name,
        description: skill.description,
        instructions: skill.instructions,
      });
    }
  }

  /**
   * Get relevant skills for a user message.
   * Results are cached by the first 100 chars of the message to avoid
   * re-running the full keyword scoring loop on every tool call.
   */
  getRelevantSkills(userMessage: string): Skill[] {
    const cacheKey = userMessage.slice(0, SkillContextInjector.CACHE_KEY_LENGTH).toLowerCase();
    let skillNames = this.relevanceCache.get(cacheKey);

    if (!skillNames) {
      skillNames = this.selector.findRelevant(userMessage, this.options.maxSkills);
      this.relevanceCache.set(cacheKey, skillNames);
      // Bound cache size — evict oldest entry when it grows too large
      if (this.relevanceCache.size > 200) {
        const firstKey = this.relevanceCache.keys().next().value;
        if (firstKey !== undefined) this.relevanceCache.delete(firstKey);
      }
    }

    return skillNames
      .map((name) => this.registry.get(name))
      .filter((s): s is Skill => s !== undefined);
  }

  /**
   * Inject relevant skills into context.
   */
  injectIntoContext(userMessage: string): string {
    const skills = this.getRelevantSkills(userMessage);

    if (skills.length === 0) {
      return "";
    }

    const lines: string[] = ["\n<context_skills>"];

    for (const skill of skills) {
      lines.push(`<skill name="${skill.name}">`);
      lines.push(skill.instructions);
      lines.push(`</skill>`);
    }

    lines.push("</context_skills>\n");
    return lines.join("\n");
  }

  /**
   * Auto-search ClawHub for skills if local skills don't cover the request.
   */
  async ensureRelevantSkills(userMessage: string): Promise<{
    injected: string;
    newSkillsInstalled: string[];
  }> {
    const localSkills = this.getRelevantSkills(userMessage);

    // If we have relevant local skills, use them
    if (localSkills.length > 0) {
      return {
        injected: this.injectIntoContext(userMessage),
        newSkillsInstalled: [],
      };
    }

    // No local skills found - try ClawHub if enabled
    if (!this.clawHub || !this.options.autoSearchClawHub) {
      return {
        injected: "",
        newSkillsInstalled: [],
      };
    }

    // Avoid duplicate searches for the same topic
    const searchKey = userMessage.toLowerCase().slice(0, 50);
    if (this.recentlySearched.has(searchKey)) {
      return {
        injected: "",
        newSkillsInstalled: [],
      };
    }

    console.log(
      chalk.dim(`[SkillInjector] No local skills found, searching ClawHub...`),
    );

    try {
      const results = await this.clawHub.search(userMessage, 5);

      if (results.skills.length === 0) {
        this.recentlySearched.add(searchKey);
        return {
          injected: "",
          newSkillsInstalled: [],
        };
      }

      // Show available skills but don't auto-install for security
      console.log(
        chalk.cyan(
          `[SkillInjector] Found ${results.skills.length} skills on ClawHub:`,
        ),
      );
      for (const skill of results.skills.slice(0, 3)) {
        console.log(
          chalk.dim(`  - ${skill.name}: ${skill.description.slice(0, 60)}...`),
        );
      }
      console.log(
        chalk.dim(`  Run: stackowl skills --install ${results.skills[0].slug}`),
      );

      this.recentlySearched.add(searchKey);

      return {
        injected: "",
        newSkillsInstalled: results.skills.map((s) => s.slug),
      };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.log(
        chalk.yellow(`[SkillInjector] ClawHub search failed: ${msg}`),
      );
      return {
        injected: "",
        newSkillsInstalled: [],
      };
    }
  }

  /**
   * Format skills for system prompt inclusion.
   */
  formatForSystemPrompt(): string {
    const skills = this.registry.listEnabled();

    if (skills.length === 0) {
      return "";
    }

    const lines: string[] = [
      "\n## Available Skills\n",
      "You have access to the following skills that can help with user requests:\n",
    ];

    for (const skill of skills) {
      const emoji = skill.metadata.openclaw?.emoji || "•";
      lines.push(`- ${emoji} **${skill.name}**: ${skill.description}`);
    }

    lines.push(
      "\nUse relevant skills when the user request matches their description.\n",
    );

    return lines.join("\n");
  }
}

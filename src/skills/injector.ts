/**
 * StackOwl — Skill Context Injector
 *
 * Dynamically injects relevant skills into LLM context based on user input.
 * Uses IntentRouter (BM25 + usage-weighted re-ranking + LLM disambiguation)
 * instead of primitive keyword matching.
 *
 * Also handles:
 *   - Skill composition (dependency resolution via SkillComposer)
 *   - Usage tracking (selection events via SkillTracker)
 *   - ClawHub remote skill search (when no local skills match)
 */

import chalk from "chalk";
import type { ModelProvider } from "../providers/base.js";
import type { ToolRegistry } from "../tools/registry.js";
import { SkillsRegistry } from "./registry.js";
import { ClawHubClient } from "./clawhub.js";
import { IntentRouter, type IntentMatch } from "./intent-router.js";
import { SkillTracker } from "./tracker.js";
import { SkillComposer } from "./composer.js";
import { SkillExecutor } from "./executor.js";
import { SkillParamExtractor } from "./param-extractor.js";
import { isStructuredSkill } from "./types.js";
import type { Skill, SkillExecutionResult } from "./types.js";
import { log } from "../logger.js";

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
  private router: IntentRouter;
  private tracker: SkillTracker;
  private composer: SkillComposer;
  private executor: SkillExecutor | null = null;
  private paramExtractor: SkillParamExtractor | null = null;
  private clawHub: ClawHubClient | null;
  private options: Required<SkillContextOptions>;
  private recentlySearched: Set<string> = new Set();

  constructor(
    registry: SkillsRegistry,
    options: SkillContextOptions = {},
    provider?: ModelProvider,
    tracker?: SkillTracker,
    toolRegistry?: ToolRegistry,
    cwd?: string,
  ) {
    this.registry = registry;
    this.clawHub = null;
    this.options = {
      maxSkills: options.maxSkills ?? 3,
      autoSearchClawHub: options.autoSearchClawHub ?? true,
      clawHubTargetDir: options.clawHubTargetDir ?? "./workspace/skills",
    };

    // Initialize the tracker (use provided or create a no-op one)
    this.tracker = tracker ?? new SkillTracker(".");

    // Initialize the semantic router (BM25 + usage weighting + LLM disambiguation)
    this.router = new IntentRouter(registry, provider, this.tracker);

    // Initialize the composer for skill chaining
    this.composer = new SkillComposer(registry);

    // Initialize the structured skill executor (requires tool registry)
    if (provider && toolRegistry && cwd) {
      this.executor = new SkillExecutor(toolRegistry, provider, cwd);
      this.paramExtractor = new SkillParamExtractor(provider);
    }
  }

  /**
   * Set ClawHub client for remote skill search.
   */
  setClawHub(client: ClawHubClient): void {
    this.clawHub = client;
  }

  /**
   * Get the tracker instance for external usage recording.
   */
  getTracker(): SkillTracker {
    return this.tracker;
  }

  /**
   * Check if a skill should use the structured executor.
   */
  canExecuteStructured(skill: Skill): boolean {
    return isStructuredSkill(skill) && this.executor !== null;
  }

  /**
   * Execute a structured skill directly — bypasses prompt injection.
   * The engine drives execution, not the LLM.
   */
  async executeStructuredSkill(
    skill: Skill,
    userMessage: string,
    onProgress?: (msg: string) => Promise<void>,
  ): Promise<SkillExecutionResult> {
    if (!this.executor || !this.paramExtractor) {
      throw new Error(
        "Structured skill executor not initialized (missing toolRegistry or provider)",
      );
    }

    const startTime = Date.now();

    // Extract parameters from user message
    let parameters: Record<string, unknown> = {};
    if (skill.parameters && Object.keys(skill.parameters).length > 0) {
      try {
        parameters = await this.paramExtractor.extract(
          userMessage,
          skill.parameters,
        );
        log.engine.info(
          `[SkillExecutor] Extracted params for "${skill.name}": ` +
            `${JSON.stringify(parameters)}`,
        );
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : String(err);
        this.tracker.recordFailure(skill.name, Date.now() - startTime);
        return {
          skillName: skill.name,
          status: "failed",
          stepResults: [],
          finalOutput: `Failed to extract parameters: ${errorMsg}`,
          totalDurationMs: Date.now() - startTime,
          parameters: {},
        };
      }
    }

    // Map progress to gateway format
    const progressAdapter = onProgress
      ? async (stepId: string, status: string, detail?: string) => {
          const emoji =
            status === "running" ? "⏳" : status === "success" ? "✅" : "❌";
          await onProgress(`${emoji} **Step ${stepId}:** ${detail || status}`);
        }
      : undefined;

    // Execute
    const result = await this.executor.execute(
      skill,
      parameters,
      progressAdapter,
    );

    // Track
    const durationMs = Date.now() - startTime;
    if (result.status === "success") {
      this.tracker.recordSuccess(skill.name, durationMs);
    } else {
      this.tracker.recordFailure(skill.name, durationMs);
    }

    return result;
  }

  /**
   * Rebuild the BM25 index after skills change.
   * Call this after loading/unloading skills.
   */
  reindex(): void {
    this.router.reindex();
    this.router.clearCache();
  }

  /**
   * Get relevant skills for a user message.
   * Uses BM25 retrieval + usage-weighted re-ranking + optional LLM disambiguation.
   */
  async getRelevantSkills(userMessage: string): Promise<Skill[]> {
    const matches = await this.getRelevantMatches(userMessage);
    return matches.map((m) => m.skill);
  }

  /**
   * Get relevant skills with their match scores.
   * Useful for gating structured execution on confidence.
   */
  async getRelevantMatches(userMessage: string): Promise<IntentMatch[]> {
    const matches = await this.router.route(
      userMessage,
      this.options.maxSkills,
    );

    // Track selections
    for (const m of matches) {
      this.tracker.recordSelection(m.skill.name);
    }

    return matches;
  }

  /**
   * Inject relevant skills into context.
   * Resolves skill dependencies and formats as XML for LLM consumption.
   */
  async injectIntoContext(userMessage: string): Promise<string> {
    const skills = await this.getRelevantSkills(userMessage);

    if (skills.length === 0) {
      return "";
    }

    const lines: string[] = ["\n<context_skills>"];

    for (const skill of skills) {
      // Resolve composition — check if this skill has dependencies/chains
      const plan = this.composer.resolve(skill);

      if (plan.totalSkills > 1) {
        // Multi-skill composition — format as skill chain
        lines.push(this.composer.formatForContext(plan));
      } else {
        // Single skill — standard format
        lines.push(`<skill name="${skill.name}">`);
        lines.push(skill.instructions);
        lines.push(`</skill>`);
      }
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
    const localSkills = await this.getRelevantSkills(userMessage);

    // If we have relevant local skills, use them
    if (localSkills.length > 0) {
      return {
        injected: await this.injectIntoContext(userMessage),
        newSkillsInstalled: [],
      };
    }

    // No local skills found — try ClawHub if enabled
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
   * (Synchronous — lists all skills, not per-message matching)
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
      const usage = skill.usage;
      const usageHint =
        usage && usage.selectionCount > 0
          ? ` (used ${usage.selectionCount}x, ${(usage.successRate * 100).toFixed(0)}% success)`
          : "";
      lines.push(
        `- ${emoji} **${skill.name}**: ${skill.description}${usageHint}`,
      );
    }

    lines.push(
      "\nUse relevant skills when the user request matches their description.\n",
    );

    return lines.join("\n");
  }
}

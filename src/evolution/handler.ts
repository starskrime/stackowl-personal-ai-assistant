/**
 * StackOwl — Evolution Handler
 *
 * Handles capability gaps in two modes:
 *
 *   PRIMARY  — Skill synthesis: generates a SKILL.md that teaches the LLM to
 *              accomplish the task using shell commands + existing tools. Safe,
 *              auditable, zero compilation risk.
 *
 *   FALLBACK — TypeScript synthesis: code generation + dynamic import. Used only
 *              when a skills directory is not configured.
 *
 * Channels are responsible ONLY for:
 *   1. Formatting and displaying the proposal (channel-specific UI)
 *   2. Collecting y/n from the user (readline, Telegram message, HTTP, etc.)
 *
 * Everything else lives here — no duplication across channels.
 */

import { exec } from "node:child_process";
import { promisify } from "node:util";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type {
  OwlEngine,
  EngineContext,
  EngineResponse,
  PendingCapabilityGap,
} from "../engine/runtime.js";
import { GapDetector } from "./detector.js";
import {
  ToolSynthesizer,
  type ToolProposal,
  SYNTHESIZED_DIR,
} from "./synthesizer.js";
import { CapabilityNeedAssessor } from "./assessor.js";
import { CapabilityLedger } from "./ledger.js";
import { DynamicToolLoader } from "./loader.js";
import type { ApprovalCallback } from "./approval.js";
import type { Skill } from "../skills/types.js";
import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

const execAsync = promisify(exec);
const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = dirname(dirname(__dirname));

export type { ToolProposal };

export interface BuildResult {
  filePath: string;
  response: EngineResponse;
  depsToInstall: string[];
  depsInstalled: boolean;
}

export type InstallApprovalCallback = (deps: string[]) => Promise<boolean>;
export type ProgressCallback = (message: string) => Promise<void>;
export type { ApprovalCallback };

export class EvolutionHandler {
  private synthesizer: ToolSynthesizer;
  private ledger: CapabilityLedger;
  private loader: DynamicToolLoader;

  constructor(
    synthesizer: ToolSynthesizer,
    ledger: CapabilityLedger,
    loader: DynamicToolLoader,
  ) {
    this.synthesizer = synthesizer;
    this.ledger = ledger;
    this.loader = loader;
  }

  /**
   * Resolve the provider and model to use for tool/skill synthesis.
   * Prefers the configured synthesis provider (default: Anthropic Claude Sonnet 4.6)
   * over the default provider to ensure high-quality tool generation.
   */
  private resolveSynthesisProvider(context: EngineContext): {
    provider: ModelProvider;
    model: string;
  } {
    const synthesisConfig = context.config.synthesis;
    const providerName = synthesisConfig?.provider ?? "anthropic";
    const model = synthesisConfig?.model ?? "claude-sonnet-4-5-20241022";

    // Try to get the synthesis-specific provider from the registry
    if (context.providerRegistry) {
      try {
        const provider = context.providerRegistry.get(providerName);
        log.evolution.info(
          `[Synthesis] Using ${providerName}/${model} for tool generation`,
        );
        return { provider, model };
      } catch {
        log.evolution.warn(
          `[Synthesis] Provider "${providerName}" not registered. Falling back to default provider.`,
        );
      }
    }

    // Fallback to the context's default provider
    log.evolution.warn(
      `[Synthesis] No provider registry available. Using default provider with model ${model}.`,
    );
    return { provider: context.provider, model };
  }

  /**
   * Detect if we're running in a container environment
   */
  static isContainerEnvironment(): boolean {
    try {
      // Check for common container indicators
      const containerIndicators = [
        "/.dockerenv", // Docker container
        "/run/.containerenv", // Podman container
        "/.container", // Generic container marker
        "/proc/1/cgroup", // Linux cgroup (may show container info)
      ];

      for (const indicator of containerIndicators) {
        try {
          const fs = require("node:fs");
          if (fs.existsSync(indicator)) {
            return true;
          }
        } catch {
          // Continue checking
        }
      }

      // Check environment variables
      const envVars = [
        "container",
        "DOCKER_CONTAINER",
        "KUBERNETES_SERVICE_HOST",
      ];
      return envVars.some((env) => process.env[env] !== undefined);
    } catch {
      return false;
    }
  }

  /**
   * Get platform-specific advice for container execution
   */
  static getContainerAdvice(): string {
    if (this.isContainerEnvironment()) {
      return (
        `Note: This tool will execute in a container environment (likely Alpine Linux). ` +
        `Avoid platform-specific commands (like 'screencapture' on macOS) and use cross-platform Node.js libraries instead. ` +
        `If the tool needs a screenshot capability, consider using a Node.js screenshot library or implementing a fallback.`
      );
    }
    return "";
  }

  /**
   * Design a proposal from a detected gap.
   * Checks for existing tools first (dedup), then designs a new spec.
   */
  async designSpec(
    gap: PendingCapabilityGap,
    context: EngineContext,
  ): Promise<ToolProposal & { existingTool?: boolean }> {
    log.evolution.evolve(
      `Designing spec for gap: "${gap.description.slice(0, 80)}"`,
    );

    // Add container awareness to the LLM prompt
    const containerAdvice = EvolutionHandler.getContainerAdvice();

    // Dedup: does a tool for this gap already exist?
    const existing = await this.ledger.findExisting(gap.userRequest);
    if (existing) {
      log.evolution.info(
        `Found existing tool: "${existing.toolName}" — skipping design`,
      );
      return {
        toolName: existing.toolName,
        description: existing.description,
        parameters: [],
        rationale: existing.rationale,
        dependencies: existing.dependencies,
        safetyNote: existing.safetyNote,
        filePath: join(SYNTHESIZED_DIR, existing.fileName),
        owlName: existing.createdBy,
        owlEmoji: context.owl.persona.emoji,
        existingTool: true,
      };
    }

    const gapDetector = new GapDetector();
    const capabilityGap = gap.attemptedToolName
      ? gapDetector.fromMissingTool(gap.attemptedToolName, gap.userRequest)
      : {
          type: "CAPABILITY_GAP" as const,
          userRequest: gap.userRequest,
          description: gap.description,
        };

    const { provider: synthesisProvider, model: synthesisModel } =
      this.resolveSynthesisProvider(context);
    const proposal = await this.synthesizer.designSpec(
      capabilityGap,
      synthesisProvider,
      context.owl,
      context.config,
      synthesisModel,
    );
    log.evolution.evolve(
      `Spec ready: ${proposal.toolName} (deps: ${proposal.dependencies.join(", ") || "none"})`,
    );

    // Add container detection to the proposal
    if (containerAdvice) {
      proposal.rationale += ` ${containerAdvice}`;
    }
    return proposal;
  }

  /**
   * Build the approved capability and retry the original request.
   *
   * PRIMARY path: generate a SKILL.md → inject into retry context.
   * FALLBACK path: TypeScript synthesis → dynamic import (when skills dir unavailable).
   */
  async buildAndRetry(
    proposal: ToolProposal & { existingTool?: boolean },
    originalMessage: string,
    context: EngineContext,
    engine: OwlEngine,
    askInstallApproval?: InstallApprovalCallback,
    onProgress?: ProgressCallback,
    askApproval?: ApprovalCallback,
  ): Promise<BuildResult> {
    const progress = async (msg: string) => {
      log.evolution.info(msg);
      if (onProgress) await onProgress(msg);
    };

    // ─── Approval gate — ask user before synthesizing ────────────
    if (askApproval && !proposal.existingTool) {
      const decision = await askApproval({
        id: `apr_${Date.now()}`,
        type: "skill_synthesis",
        skillName: proposal.toolName,
        description: proposal.description,
        rationale: proposal.rationale,
        gap: {
          userRequest: originalMessage,
          description: proposal.rationale,
        },
        timestamp: new Date().toISOString(),
      });

      if (decision === "rejected") {
        await progress(`❌ Synthesis rejected by user.`);
        const skipResponse = await engine.run(originalMessage, {
          ...context,
          skipGapDetection: true,
        });
        return {
          filePath: "",
          response: skipResponse,
          depsToInstall: [],
          depsInstalled: false,
        };
      }

      if (decision === "deferred") {
        await progress(`⏸️ Synthesis deferred for later review.`);
        const deferResponse = await engine.run(originalMessage, {
          ...context,
          skipGapDetection: true,
        });
        return {
          filePath: "",
          response: deferResponse,
          depsToInstall: [],
          depsInstalled: false,
        };
      }

      // decision === "approved" — continue with synthesis
      await progress(`✅ Synthesis approved.`);
    }

    // ─── Re-use existing TypeScript tool ─────────────────────────
    if (proposal.existingTool) {
      await progress(`♻️ Re-using existing tool "${proposal.toolName}"`);
      if (
        context.toolRegistry &&
        !context.toolRegistry.has(proposal.toolName)
      ) {
        await progress(`🔌 Loading ${proposal.toolName} into registry...`);
        await this.loader.loadOne(proposal.filePath, context.toolRegistry!);
        await progress(`✅ ${proposal.toolName} registered.`);
      }
      return this.retryWithTool(
        proposal,
        originalMessage,
        context,
        engine,
        progress,
      );
    }

    // ─── PRIMARY: Skill synthesis ─────────────────────────────────
    const skillsDir = context.config.skills?.directories?.[0];
    if (skillsDir) {
      // ── Capability Need Assessment — gate before synthesis ────
      const { provider: synthesisProvider } =
        this.resolveSynthesisProvider(context);
      const assessor = new CapabilityNeedAssessor(synthesisProvider);
      const toolNames = context.toolRegistry
        ? context.toolRegistry.getAllDefinitions().map((d) => d.name)
        : [];
      const existingSkills: Skill[] = context.skillsRegistry
        ? context.skillsRegistry.listEnabled()
        : [];

      const assessment = await assessor.assess(
        originalMessage,
        toolNames,
        existingSkills,
        proposal.rationale, // Pass the gap description so CNA knows the engine already tried existing tools
      );
      log.evolution.info(
        `[CNA] verdict=${assessment.verdict} type=${assessment.requestType} — ${assessment.reasoning}`,
      );

      if (assessment.verdict === "SKIP") {
        // Non-operational request — don't synthesize, answer directly
        const skipResponse = await engine.run(originalMessage, {
          ...context,
          skipGapDetection: true,
        });
        return {
          filePath: "",
          response: skipResponse,
          depsToInstall: [],
          depsInstalled: false,
        };
      }

      if (assessment.verdict === "COVERED") {
        // Existing tools/skills handle it — re-run with a coverage hint
        await progress(
          assessment.suggestedExistingSkill
            ? `✅ Existing skill "${assessment.suggestedExistingSkill}" already covers this — routing.`
            : `✅ Existing tools already cover this — routing.`,
        );
        const coveredResponse = await engine.run(originalMessage, {
          ...context,
          skipGapDetection: true,
        });
        return {
          filePath: "",
          response: coveredResponse,
          depsToInstall: [],
          depsInstalled: false,
        };
      }

      if (assessment.verdict === "NEAR_DUPLICATE") {
        await progress(
          `♻️ Skill "${assessment.suggestedExistingSkill}" is very similar (${((assessment.overlapScore ?? 0) * 100).toFixed(0)}% overlap). Using existing skill.`,
        );
        const dupResponse = await engine.run(originalMessage, {
          ...context,
          skipGapDetection: true,
        });
        return {
          filePath: "",
          response: dupResponse,
          depsToInstall: [],
          depsInstalled: false,
        };
      }

      // assessment.verdict === 'SYNTHESIZE' — genuine gap confirmed
      return this.buildWithSkill(
        proposal,
        originalMessage,
        context,
        engine,
        progress,
        skillsDir,
      );
    }

    // ─── FALLBACK: TypeScript synthesis ───────────────────────────
    log.evolution.warn(
      "No skills directory configured — falling back to TypeScript synthesis",
    );
    return this.buildWithTypeScript(
      proposal,
      originalMessage,
      context,
      engine,
      progress,
      askInstallApproval,
    );
  }

  // ─── Primary: SKILL.md synthesis ─────────────────────────────────

  private async buildWithSkill(
    proposal: ToolProposal,
    originalMessage: string,
    context: EngineContext,
    engine: OwlEngine,
    progress: ProgressCallback,
    skillsDir: string,
  ): Promise<BuildResult> {
    await progress(
      `🧠 Synthesizing skill for: "${originalMessage.slice(0, 60)}..."`,
    );

    const gap = {
      type: "CAPABILITY_GAP" as const,
      userRequest: originalMessage,
      description: proposal.rationale,
    };

    // Pass full tool descriptions so the skill knows all available tools
    const toolDescriptions = context.toolRegistry
      ? context.toolRegistry
          .getAllDefinitions()
          .map((d) => `${d.name}: ${d.description?.slice(0, 100) ?? ""}`)
      : undefined;

    const { provider: synthesisProvider, model: synthesisModel } =
      this.resolveSynthesisProvider(context);
    const skill = await this.synthesizer.generateSkillMd(
      gap,
      synthesisProvider,
      context.owl,
      context.config,
      skillsDir,
      toolDescriptions,
      synthesisModel,
    );

    await progress(
      `✅ Skill "${skill.skillName}" written to ${skill.filePath}`,
    );
    await progress(
      `📚 Skill will be available for future sessions automatically.`,
    );
    await progress(`🔄 Retrying your request with the new skill...`);

    // Inject the skill instructions directly into the retry context
    const skillDirective =
      `[NEW SKILL SYNTHESIZED: ${skill.skillName}]\n` +
      `You now know how to accomplish this task. Follow the skill instructions below exactly.\n\n` +
      `<skill name="${skill.skillName}">\n${skill.content}\n</skill>`;

    const retryContext: EngineContext = {
      ...context,
      sessionHistory: [{ role: "system", content: skillDirective }],
      skipGapDetection: true,
    };

    const response = await engine.run(originalMessage, retryContext);
    response.pendingCapabilityGap = undefined;

    return {
      filePath: skill.filePath,
      response,
      depsToInstall: [],
      depsInstalled: false,
    };
  }

  // ─── Fallback: TypeScript synthesis ──────────────────────────────

  private async buildWithTypeScript(
    proposal: ToolProposal,
    originalMessage: string,
    context: EngineContext,
    engine: OwlEngine,
    progress: ProgressCallback,
    askInstallApproval?: InstallApprovalCallback,
  ): Promise<BuildResult> {
    if (!context.toolRegistry) {
      throw new Error(
        "ToolRegistry is required for TypeScript tool synthesis.",
      );
    }

    const { provider: synthesisProvider, model: synthesisModel } =
      this.resolveSynthesisProvider(context);
    const MAX_RETRIES = 3;
    let attempt = 1;
    let lastError: string | undefined;
    let filePath = "";

    while (attempt <= MAX_RETRIES) {
      try {
        await progress(
          `✍️ Writing ${proposal.toolName}.ts (Attempt ${attempt}/${MAX_RETRIES})...`,
        );
        filePath = await this.synthesizer.implement(
          proposal,
          synthesisProvider,
          context.owl,
          context.config,
          lastError,
          synthesisModel,
        );
        await progress(`✅ ${proposal.toolName}.ts written`);

        // Add platform compatibility checking
        await progress(`🔍 Checking platform compatibility...`);
        const containerAdvice = EvolutionHandler.getContainerAdvice();
        if (containerAdvice) {
          await progress(
            `⚠️ Container environment detected - checking tool compatibility`,
          );
        }

        if (proposal.dependencies.length > 0 && askInstallApproval) {
          const approved = await askInstallApproval(proposal.dependencies);
          if (approved) {
            await progress(
              `📦 Running: npm install ${proposal.dependencies.join(" ")}...`,
            );
            try {
              const { stdout, stderr } = await execAsync(
                `npm install ${proposal.dependencies.join(" ")}`,
                { cwd: PROJECT_ROOT },
              );
              if (stdout) log.evolution.debug(`npm stdout: ${stdout.trim()}`);
              if (stderr) log.evolution.warn(`npm stderr: ${stderr.trim()}`);
              await progress(`✅ npm install complete.`);
            } catch (err) {
              await progress(
                `⚠️ npm install failed: ${err instanceof Error ? err.message : err}`,
              );
              // Log but continue execution - dependencies may not be essential
            }
          } else {
            await progress(`⏭️ Skipped npm install.`);
          }
        }

        await progress(`🔌 Loading ${proposal.toolName} into registry...`);
        await this.loader.loadOne(filePath, context.toolRegistry);
        await progress(`✅ ${proposal.toolName} registered.`);
        await this.ledger.record(proposal);
        break;
      } catch (err) {
        lastError = err instanceof Error ? err.message : String(err);
        await progress(`❌ Build attempt ${attempt} failed: ${lastError}`);

        // Add enhanced error detection and recovery for platform issues
        if (
          lastError.toLowerCase().includes("command not found") ||
          lastError.toLowerCase().includes("not found") ||
          lastError.toLowerCase().includes("permission denied")
        ) {
          await progress(
            `💡 Platform error detected - considering fallback strategy`,
          );
          // Suggest improving the implementation with better platform handling
          if (attempt === 1) {
            await progress(
              `🔄 Attempting to regenerate with better cross-platform handling...`,
            );
            // If this is a platform-specific error on first attempt, suggest improving the approach
            lastError = `Platform compatibility issue - tool may require cross-platform fallbacks. Please consider updating the tool implementation.`;
          }
        }

        if (attempt === MAX_RETRIES) {
          throw new Error(
            `Tool synthesis failed after ${MAX_RETRIES} attempts. Last error: ${lastError}`,
          );
        }
        await progress(`🔄 Self-correcting...`);
        attempt++;
      }
    }

    return this.retryWithTool(
      proposal,
      originalMessage,
      context,
      engine,
      progress,
      filePath,
    );
  }

  // ─── Shared retry helper ──────────────────────────────────────────

  private async retryWithTool(
    proposal: ToolProposal,
    originalMessage: string,
    context: EngineContext,
    engine: OwlEngine,
    progress: ProgressCallback,
    filePath?: string,
  ): Promise<BuildResult> {
    await progress(`🔄 Retrying your request with the new tool...`);

    const systemInstruction =
      `You have just acquired a new tool: "${proposal.toolName}".\n` +
      `Description: ${proposal.description}\n\n` +
      `You MUST solve the user's next request by calling this tool. Do NOT apologize or refuse.`;

    const retryContext: EngineContext = {
      ...context,
      sessionHistory: [{ role: "system", content: systemInstruction }],
      skipGapDetection: true,
    };

    const response = await engine.run(originalMessage, retryContext);
    response.pendingCapabilityGap = undefined;

    return {
      filePath: filePath ?? proposal.filePath,
      response,
      depsToInstall: proposal.dependencies,
      depsInstalled: true,
    };
  }

  async listAll() {
    await this.ledger.load();
    return this.ledger.listAll();
  }
}

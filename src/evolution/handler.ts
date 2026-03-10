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

import { exec } from 'node:child_process';
import { promisify } from 'node:util';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { OwlEngine, EngineContext, EngineResponse, PendingCapabilityGap } from '../engine/runtime.js';
import { GapDetector } from './detector.js';
import { ToolSynthesizer, type ToolProposal, SYNTHESIZED_DIR } from './synthesizer.js';
import { CapabilityLedger } from './ledger.js';
import { DynamicToolLoader } from './loader.js';
import { log } from '../logger.js';

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

export class EvolutionHandler {
    private synthesizer: ToolSynthesizer;
    private ledger: CapabilityLedger;
    private loader: DynamicToolLoader;

    constructor(synthesizer: ToolSynthesizer, ledger: CapabilityLedger, loader: DynamicToolLoader) {
        this.synthesizer = synthesizer;
        this.ledger = ledger;
        this.loader = loader;
    }

    /**
     * Design a proposal from a detected gap.
     * Checks for existing tools first (dedup), then designs a new spec.
     */
    async designSpec(gap: PendingCapabilityGap, context: EngineContext): Promise<ToolProposal & { existingTool?: boolean }> {
        log.evolution.evolve(`Designing spec for gap: "${gap.description.slice(0, 80)}"`);

        // Dedup: does a tool for this gap already exist?
        const existing = await this.ledger.findExisting(gap.userRequest);
        if (existing) {
            log.evolution.info(`Found existing tool: "${existing.toolName}" — skipping design`);
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
            : { type: 'CAPABILITY_GAP' as const, userRequest: gap.userRequest, description: gap.description };

        const proposal = await this.synthesizer.designSpec(capabilityGap, context.provider, context.owl, context.config);
        log.evolution.evolve(`Spec ready: ${proposal.toolName} (deps: ${proposal.dependencies.join(', ') || 'none'})`);
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
    ): Promise<BuildResult> {
        const progress = async (msg: string) => {
            log.evolution.info(msg);
            if (onProgress) await onProgress(msg);
        };

        // ─── Re-use existing TypeScript tool ─────────────────────────
        if (proposal.existingTool) {
            await progress(`♻️ Re-using existing tool "${proposal.toolName}"`);
            if (context.toolRegistry && !context.toolRegistry.has(proposal.toolName)) {
                await progress(`🔌 Loading ${proposal.toolName} into registry...`);
                await this.loader.loadOne(proposal.filePath, context.toolRegistry!);
                await progress(`✅ ${proposal.toolName} registered.`);
            }
            return this.retryWithTool(proposal, originalMessage, context, engine, progress);
        }

        // ─── PRIMARY: Skill synthesis ─────────────────────────────────
        const skillsDir = context.config.skills?.directories?.[0];
        if (skillsDir) {
            return this.buildWithSkill(proposal, originalMessage, context, engine, progress, skillsDir);
        }

        // ─── FALLBACK: TypeScript synthesis ───────────────────────────
        log.evolution.warn('No skills directory configured — falling back to TypeScript synthesis');
        return this.buildWithTypeScript(proposal, originalMessage, context, engine, progress, askInstallApproval);
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
        await progress(`🧠 Synthesizing skill for: "${originalMessage.slice(0, 60)}..."`);

        const gap = {
            type: 'CAPABILITY_GAP' as const,
            userRequest: originalMessage,
            description: proposal.rationale,
        };

        const skill = await this.synthesizer.generateSkillMd(
            gap,
            context.provider,
            context.owl,
            context.config,
            skillsDir,
        );

        await progress(`✅ Skill "${skill.skillName}" written to ${skill.filePath}`);
        await progress(`📚 Skill will be available for future sessions automatically.`);
        await progress(`🔄 Retrying your request with the new skill...`);

        // Inject the skill instructions directly into the retry context
        const skillDirective =
            `[NEW SKILL SYNTHESIZED: ${skill.skillName}]\n` +
            `You now know how to accomplish this task. Follow the skill instructions below exactly.\n\n` +
            `<skill name="${skill.skillName}">\n${skill.content}\n</skill>`;

        const retryContext: EngineContext = {
            ...context,
            sessionHistory: [{ role: 'system', content: skillDirective }],
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
            throw new Error('ToolRegistry is required for TypeScript tool synthesis.');
        }

        const MAX_RETRIES = 3;
        let attempt = 1;
        let lastError: string | undefined;
        let filePath = '';

        while (attempt <= MAX_RETRIES) {
            try {
                await progress(`✍️ Writing ${proposal.toolName}.ts (Attempt ${attempt}/${MAX_RETRIES})...`);
                filePath = await this.synthesizer.implement(proposal, context.provider, context.owl, context.config, lastError);
                await progress(`✅ ${proposal.toolName}.ts written`);

                if (proposal.dependencies.length > 0 && askInstallApproval) {
                    const approved = await askInstallApproval(proposal.dependencies);
                    if (approved) {
                        await progress(`📦 Running: npm install ${proposal.dependencies.join(' ')}...`);
                        try {
                            const { stdout, stderr } = await execAsync(
                                `npm install ${proposal.dependencies.join(' ')}`,
                                { cwd: PROJECT_ROOT }
                            );
                            if (stdout) log.evolution.debug(`npm stdout: ${stdout.trim()}`);
                            if (stderr) log.evolution.warn(`npm stderr: ${stderr.trim()}`);
                            await progress(`✅ npm install complete.`);
                        } catch (err) {
                            await progress(`⚠️ npm install failed: ${err instanceof Error ? err.message : err}`);
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
                if (attempt === MAX_RETRIES) {
                    throw new Error(`Tool synthesis failed after ${MAX_RETRIES} attempts. Last error: ${lastError}`);
                }
                await progress(`🔄 Self-correcting...`);
                attempt++;
            }
        }

        return this.retryWithTool(proposal, originalMessage, context, engine, progress, filePath);
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
            sessionHistory: [{ role: 'system', content: systemInstruction }],
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

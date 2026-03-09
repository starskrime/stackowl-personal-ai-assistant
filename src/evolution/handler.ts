/**
 * StackOwl — Evolution Handler
 *
 * Shared logic for ALL channels: design a tool spec when a capability gap is
 * detected, then build + load + retry after the user approves.
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
const PROJECT_ROOT = dirname(dirname(__dirname)); // src/evolution/../../ = project root

export type { ToolProposal };

export interface BuildResult {
    filePath: string;
    response: EngineResponse;
    /** Deps that were requested to install — empty if all were installed or none needed */
    depsToInstall: string[];
    depsInstalled: boolean;
}

/**
 * Called by the channel to ask the user if it's OK to run npm install.
 * Returns true if user approves, false to skip (tool still loads, just may fail at runtime).
 */
export type InstallApprovalCallback = (deps: string[]) => Promise<boolean>;

/**
 * Called throughout buildAndRetry to report progress to the user.
 * Channels implement this differently (console.log vs Telegram message).
 */
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
     * Design a tool spec from a detected gap.
     * Returns the proposal for the channel to display and ask approval for.
     * No code is written yet.
     *
     * If an existing tool in the ledger already covers this gap, returns
     * a proposal pointing to that tool instead of designing a new one.
     */
    async designSpec(gap: PendingCapabilityGap, context: EngineContext): Promise<ToolProposal & { existingTool?: boolean }> {
        log.evolution.evolve(`Designing tool spec for gap: "${gap.description.slice(0, 80)}"`);

        // ─── Dedup check: does a tool for this gap already exist? ────
        // Only use the user's request — the gap description is LLM refusal text
        // full of noise words ("sorry", "boss", "have") that dilute matching.
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
        // ─────────────────────────────────────────────────────────────

        const gapDetector = new GapDetector();
        const capabilityGap = gap.attemptedToolName
            ? gapDetector.fromMissingTool(gap.attemptedToolName, gap.userRequest)
            : { type: 'CAPABILITY_GAP' as const, userRequest: gap.userRequest, description: gap.description };

        const proposal = await this.synthesizer.designSpec(capabilityGap, context.provider, context.owl, context.config);
        log.evolution.evolve(`Spec ready: ${proposal.toolName} (deps: ${proposal.dependencies.join(', ') || 'none'})`);
        return proposal;
    }

    /**
     * Build the approved tool, install deps (with user approval), hot-load it,
     * record it, and retry the original request.
     * Called by the channel after the user approves the tool proposal.
     *
     * @param askInstallApproval - Channel-provided callback to ask user about npm install.
     *                             If not provided, deps are NOT auto-installed.
     */
    async buildAndRetry(
        proposal: ToolProposal & { existingTool?: boolean },
        originalMessage: string,
        context: EngineContext,
        engine: OwlEngine,
        askInstallApproval?: InstallApprovalCallback,
        onProgress?: ProgressCallback,
    ): Promise<BuildResult> {
        if (!context.toolRegistry) {
            throw new Error('ToolRegistry is required in context for tool synthesis.');
        }

        const progress = async (msg: string) => {
            log.evolution.info(msg);
            if (onProgress) await onProgress(msg);
        };

        let filePath: string = '';

        if (proposal.existingTool) {
            // ─── Re-use existing tool (no synthesis needed) ──────────
            filePath = proposal.filePath;
            await progress(`♻️ Re-using existing tool "${proposal.toolName}" — no synthesis needed.`);

            // Ensure it's loaded into the registry (may not be if it was retired or startup failed)
            if (!context.toolRegistry.has(proposal.toolName)) {
                await progress(`🔌 Loading ${proposal.toolName} into registry...`);
                try {
                    await this.loader.loadOne(filePath, context.toolRegistry);
                    await progress(`✅ ${proposal.toolName} registered and ready.`);
                } catch (loadErr) {
                    throw new Error(`Existing tool ${filePath} could not be loaded: ${loadErr instanceof Error ? loadErr.message : String(loadErr)}. Check the file manually.`);
                }
            }
        } else {
            // ─── Full synthesis path with Self-Correction Loop ───────
            const MAX_RETRIES = 3;
            let attempt = 1;
            let lastError: string | undefined;

            while (attempt <= MAX_RETRIES) {
                try {
                    // Step 1 — Generate TypeScript implementation
                    await progress(`✍️ Writing ${proposal.toolName}.ts (Attempt ${attempt}/${MAX_RETRIES})...`);
                    filePath = await this.synthesizer.implement(proposal, context.provider, context.owl, context.config, lastError);
                    await progress(`✅ ${proposal.toolName}.ts written to src/tools/synthesized/`);

                    // Step 2 — Install npm dependencies if needed and approved
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
                                const msg = err instanceof Error ? err.message : String(err);
                                log.evolution.error(`npm install failed: ${msg}`);
                                await progress(`⚠️ npm install failed: ${msg}`);
                            }
                        } else {
                            await progress(`⏭️ Skipped npm install — tool may fail at runtime if deps are missing.`);
                        }
                    }

                    // Step 3 — Hot-load the tool
                    await progress(`🔌 Loading ${proposal.toolName} into registry...`);
                    await this.loader.loadOne(filePath, context.toolRegistry);
                    await progress(`✅ ${proposal.toolName} registered and ready.`);

                    // Step 4 — Persist to ledger
                    await this.ledger.record(proposal);

                    // Break out of the loop if everything succeeded
                    break;
                } catch (err) {
                    lastError = err instanceof Error ? err.message : String(err);
                    await progress(`❌ Build attempt ${attempt} failed: ${lastError}`);

                    if (attempt === MAX_RETRIES) {
                        throw new Error(`Tool synthesis failed after ${MAX_RETRIES} attempts. Last error: ${lastError}`);
                    }

                    await progress(`🔄 Self-correcting: Asking AI to fix the compilation error...`);
                    attempt++;
                }
            }
        }

        // ─── Retry with CLEAN context ────────────────────────────────
        // Critical: do NOT pass the old sessionHistory that contains the LLM's
        // refusal ("I can't do this"). The LLM will see that and repeat it.
        // Instead, pass a fresh history with a strong system instruction.
        await progress(`🔄 Retrying your request with the new tool...`);

        const systemInstruction =
            `You have just acquired a new tool: "${proposal.toolName}".\n` +
            `Description: ${proposal.description}\n\n` +
            `You MUST solve the user's next request by calling this tool. Do NOT apologize or refuse.`;

        const retryContext: EngineContext = {
            ...context,
            sessionHistory: [{ role: 'system', content: systemInstruction }], // Fresh start + strong instruction
            skipGapDetection: true, // Don't re-trigger evolution on retry
        };

        const response = await engine.run(originalMessage, retryContext);

        // Never re-trigger evolution after a retry — belt-and-suspenders safety
        response.pendingCapabilityGap = undefined;

        return {
            filePath,
            response,
            depsToInstall: proposal.dependencies,
            depsInstalled: !proposal.existingTool,
        };
    }

    /**
     * List all synthesized tools from the ledger.
     */
    async listAll() {
        await this.ledger.load();
        return this.ledger.listAll();
    }
}

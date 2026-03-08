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
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { OwlEngine, EngineContext, EngineResponse, PendingCapabilityGap } from '../engine/runtime.js';
import { GapDetector } from './detector.js';
import { ToolSynthesizer, type ToolProposal } from './synthesizer.js';
import { CapabilityLedger } from './ledger.js';
import { DynamicToolLoader } from './loader.js';

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
     */
    async designSpec(gap: PendingCapabilityGap, context: EngineContext): Promise<ToolProposal> {
        console.log(`[Evolution] Designing tool spec for gap: "${gap.description.slice(0, 80)}..."`);
        const gapDetector = new GapDetector();
        const capabilityGap = gap.attemptedToolName
            ? gapDetector.fromMissingTool(gap.attemptedToolName, gap.userRequest)
            : { type: 'CAPABILITY_GAP' as const, userRequest: gap.userRequest, description: gap.description };

        const proposal = await this.synthesizer.designSpec(capabilityGap, context.provider, context.owl, context.config);
        console.log(`[Evolution] Spec ready: ${proposal.toolName} (deps: ${proposal.dependencies.join(', ') || 'none'})`);
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
        proposal: ToolProposal,
        originalMessage: string,
        context: EngineContext,
        engine: OwlEngine,
        askInstallApproval?: InstallApprovalCallback,
        onProgress?: ProgressCallback,
    ): Promise<BuildResult> {
        if (!context.toolRegistry) {
            throw new Error('ToolRegistry is required in context for tool synthesis.');
        }

        const log = async (msg: string) => {
            console.log(`[Evolution] ${msg}`);
            if (onProgress) await onProgress(msg);
        };

        // Step 1 — Generate TypeScript implementation
        await log(`✍️ Writing ${proposal.toolName}.ts...`);
        const filePath = await this.synthesizer.implement(proposal, context.provider, context.owl, context.config);
        await log(`✅ ${proposal.toolName}.ts written to src/tools/synthesized/`);

        // Step 2 — Install npm dependencies if needed and approved
        let depsInstalled = false;
        if (proposal.dependencies.length > 0 && askInstallApproval) {
            const approved = await askInstallApproval(proposal.dependencies);
            if (approved) {
                await log(`📦 Running: npm install ${proposal.dependencies.join(' ')}...`);
                try {
                    const { stdout, stderr } = await execAsync(
                        `npm install ${proposal.dependencies.join(' ')}`,
                        { cwd: PROJECT_ROOT }
                    );
                    if (stdout) console.log(`[npm] ${stdout.trim()}`);
                    if (stderr) console.log(`[npm stderr] ${stderr.trim()}`);
                    depsInstalled = true;
                    await log(`✅ npm install complete.`);
                } catch (err) {
                    const msg = err instanceof Error ? err.message : String(err);
                    console.error(`[Evolution] npm install failed: ${msg}`);
                    await log(`⚠️ npm install failed: ${msg}`);
                }
            } else {
                await log(`⏭️ Skipped npm install — tool may fail at runtime if deps are missing.`);
            }
        }

        // Step 3 — Hot-load the tool
        await log(`🔌 Loading ${proposal.toolName} into registry...`);
        const loaded = await this.loader.loadOne(filePath, context.toolRegistry);
        if (!loaded) {
            throw new Error(`Tool was written to ${filePath} but failed to load. Check the file manually.`);
        }
        await log(`✅ ${proposal.toolName} registered and ready.`);

        // Step 4 — Persist to ledger
        await this.ledger.record(proposal);

        // Step 5 — Retry original request
        await log(`🔄 Retrying your request with the new tool...`);
        const response = await engine.run(originalMessage, context);

        return {
            filePath,
            response,
            depsToInstall: depsInstalled ? [] : proposal.dependencies,
            depsInstalled,
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

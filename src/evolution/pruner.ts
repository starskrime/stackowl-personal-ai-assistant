/**
 * StackOwl — Autonomous Tool Pruner
 *
 * Runs in the background (e.g., during the heartbeat loop) to scan the
 * CapabilityLedger. If a synthesized tool fails repeatedly or goes unused
 * for a long time, the Pruner autonomously attempts to fix or archive it.
 */

import { join } from 'node:path';
import { readFile, writeFile, unlink } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import type { ModelProvider } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import { OwlEngine } from '../engine/runtime.js';
import { CapabilityLedger } from './ledger.js';
import type { CapabilitySnapshot } from './ledger.js';

export class ToolPruner {
    private provider: ModelProvider;
    private owl: OwlInstance;
    private engine: OwlEngine;
    private ledger: CapabilityLedger;
    private toolsPath: string;

    // Thresholds for pruning
    private readonly FAIL_THRESHOLD = 3;

    // Used to prevent pruning the same tool back-to-back
    private recentlyPruned: Set<string> = new Set();

    constructor(provider: ModelProvider, owl: OwlInstance, workspacePath: string, ledger: CapabilityLedger) {
        this.provider = provider;
        this.owl = owl;
        this.engine = new OwlEngine();
        this.ledger = ledger;
        this.toolsPath = join(workspacePath, 'src', 'tools', 'synthesized');
    }

    /**
     * Scan the ledger for tools that need maintenance.
     */
    async scanAndPrune(): Promise<void> {
        console.log(`[ToolPruner] 🧹 Scanning Capability Ledger for failing tools...`);
        const stats = await this.ledger.getStats();

        for (const [toolName, info] of Object.entries(stats)) {
            if (this.recentlyPruned.has(toolName)) continue;

            // Only prune *synthesized* tools that are currently failing
            if (!info.isSynthesized) continue;

            // Evaluate failure streaks
            if (info.consecutiveFailures >= this.FAIL_THRESHOLD) {
                console.log(`[ToolPruner] ⚠️ Tool "${toolName}" has failed ${info.consecutiveFailures} times consecutively. Initiating automated repair...`);
                await this.attemptRepair(toolName, info);
            }
        }
    }

    /**
     * Asks the LLM to review the failing code and either rewrite it or recommend deletion.
     */
    private async attemptRepair(toolName: string, info: CapabilitySnapshot): Promise<void> {
        this.recentlyPruned.add(toolName);
        const sourcePath = join(this.toolsPath, `${toolName}.ts`);

        if (!existsSync(sourcePath)) {
            console.log(`[ToolPruner] Source for ${toolName} not found. Skipping.`);
            return;
        }

        let sourceCode = '';
        try {
            sourceCode = await readFile(sourcePath, 'utf-8');
        } catch (e) {
            console.error(`[ToolPruner] Error reading source for ${toolName}:`, e);
            return;
        }

        const prompt =
            `You are the StackOwl Autonomous System Maintainer.\n` +
            `The synthesized tool "${toolName}" has failed ${info.consecutiveFailures} times recently.\n\n` +
            `Raw Source Code:\n` +
            `\`\`\`typescript\n${sourceCode}\n\`\`\`\n\n` +
            `Analyze the code and look for obvious flaws (e.g., bad paths, missing dependencies, incorrect API calls).\n` +
            `Options:\n` +
            `1. If the tool is fundamentally broken or useless, reply exactly with: ACTION: ARCHIVE\n` +
            `2. If you see how to fix it, reply with exactly: ACTION: REWRITE\n` +
            `Followed immediately by a complete, corrected TypeScript code block.\n\n` +
            `Example Rewrite Response:\n` +
            `ACTION: REWRITE\n` +
            `\`\`\`typescript\n// Fixed code here...\n\`\`\``;

        try {
            const response = await this.engine.run(prompt, {
                provider: this.provider,
                owl: this.owl,
                sessionHistory: [],
                config: {} as any,
                skipGapDetection: true,
            });

            const content = response.content;

            if (content.includes('ACTION: ARCHIVE')) {
                console.log(`[ToolPruner] 🗑️ LLM elected to archive failing tool: ${toolName}`);
                await this.archiveTool(toolName, sourcePath);
            } else if (content.includes('ACTION: REWRITE')) {
                const match = content.match(/```(?:typescript|ts)([\s\S]*?)```/);
                if (match && match[1]) {
                    const newCode = match[1].trim();
                    console.log(`[ToolPruner] 🩹 LLM rewrote tool: ${toolName}. Saving...`);
                    await writeFile(sourcePath, newCode, 'utf-8');
                    // Reset the failure ledger so it gets a fresh chance
                    await this.ledger.recordUsage(toolName, true); // Mock a success to clear the failure streak
                } else {
                    console.error(`[ToolPruner] LLM chose REWRITE but failed to provide a valid code block for ${toolName}.`);
                }
            } else {
                console.log(`[ToolPruner] 🤷 LLM chose no action for ${toolName}.`);
            }

        } catch (e) {
            console.error(`[ToolPruner] LLM evaluation failed for ${toolName}:`, e);
        }
    }

    private async archiveTool(toolName: string, sourcePath: string): Promise<void> {
        // Move to an archive directory rather than permanently deleting
        const archiveDir = join(this.toolsPath, '_archive');
        if (!existsSync(archiveDir)) {
            const { mkdir } = await import('node:fs/promises');
            await mkdir(archiveDir, { recursive: true });
        }

        const archivePath = join(archiveDir, `${toolName}.ts`);
        try {
            await writeFile(archivePath, await readFile(sourcePath, 'utf-8'), 'utf-8');
            await unlink(sourcePath);
            console.log(`[ToolPruner] ✓ Moved ${toolName} to _archive`);
        } catch (e) {
            console.error(`[ToolPruner] Failed to archive ${toolName}:`, e);
        }
    }
}

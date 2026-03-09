/**
 * StackOwl — Capability Ledger
 *
 * Tracks all AI-synthesized tools: what was built, why, by whom,
 * and how well it's performing. Persists to src/tools/synthesized/_manifest.json.
 */

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { SYNTHESIZED_DIR } from './synthesizer.js';
import type { ToolProposal } from './synthesizer.js';

const MANIFEST_PATH = join(SYNTHESIZED_DIR, '_manifest.json');

// ─── Types ───────────────────────────────────────────────────────

export interface ToolRecord {
    toolName: string;
    fileName: string;
    description: string;
    createdAt: string;
    createdBy: string;
    rationale: string;
    dependencies: string[];
    safetyNote: string;
    timesUsed: number;
    lastUsedAt?: string;
    status: 'active' | 'failed' | 'retired';
    consecutiveFailures: number;
}

interface Manifest {
    version: number;
    tools: ToolRecord[];
}

export interface CapabilitySnapshot {
    isSynthesized: boolean;
    consecutiveFailures: number;
    totalUses: number;
    lastUsedAt?: string;
    lastError?: string;
}

// ─── Ledger ──────────────────────────────────────────────────────

export class CapabilityLedger {
    private manifest: Manifest = { version: 1, tools: [] };
    private loaded = false;

    async load(): Promise<void> {
        if (!existsSync(MANIFEST_PATH)) {
            this.manifest = { version: 1, tools: [] };
            this.loaded = true;
            return;
        }
        try {
            const raw = await readFile(MANIFEST_PATH, 'utf-8');
            this.manifest = JSON.parse(raw);
        } catch {
            this.manifest = { version: 1, tools: [] };
        }
        this.loaded = true;
    }

    async record(proposal: ToolProposal): Promise<void> {
        await this.ensureLoaded();

        const record: ToolRecord = {
            toolName: proposal.toolName,
            fileName: `${proposal.toolName}.ts`,
            description: proposal.description,
            createdAt: new Date().toISOString(),
            createdBy: proposal.owlName,
            rationale: proposal.rationale,
            dependencies: proposal.dependencies,
            safetyNote: proposal.safetyNote,
            timesUsed: 0,
            status: 'active',
            consecutiveFailures: 0,
        };

        const idx = this.manifest.tools.findIndex(t => t.toolName === proposal.toolName);
        if (idx >= 0) {
            this.manifest.tools[idx] = record;
        } else {
            this.manifest.tools.push(record);
        }

        await this.save();
    }

    async recordUsage(toolName: string, success: boolean): Promise<void> {
        await this.ensureLoaded();
        const record = this.manifest.tools.find(t => t.toolName === toolName);
        if (!record) return;

        record.timesUsed++;
        record.lastUsedAt = new Date().toISOString();
        if (success) {
            record.consecutiveFailures = 0;
            // Restore to active if it was marked failed but is working again
            if (record.status === 'failed') record.status = 'active';
        } else {
            record.consecutiveFailures = (record.consecutiveFailures ?? 0) + 1;
            if (record.consecutiveFailures >= 3) {
                record.status = 'failed';
            }
        }

        await this.save();
    }

    async retire(toolName: string): Promise<boolean> {
        await this.ensureLoaded();
        const record = this.manifest.tools.find(t => t.toolName === toolName);
        if (!record) return false;

        record.status = 'retired';
        await this.save();
        return true;
    }

    listActive(): ToolRecord[] {
        return this.manifest.tools.filter(t => t.status === 'active');
    }

    listAll(): ToolRecord[] {
        return [...this.manifest.tools];
    }

    /**
     * Find an existing active tool whose name or description matches the user's request.
     * Used to prevent duplicate tool creation for the same capability.
     *
     * Two-tier search:
     *   1. Direct name match — do any of the user's words appear in a tool name?
     *   2. Keyword overlap — bidirectional scoring between request and tool metadata.
     */
    async findExisting(userRequest: string): Promise<ToolRecord | undefined> {
        await this.ensureLoaded();
        const active = this.listActive();
        if (active.length === 0) return undefined;

        // Extract meaningful words from the user request (min 5 chars, lowercased)
        // 5-char minimum prevents common verbs ('send','take','make') from false-matching tool names
        const requestWords = userRequest
            .toLowerCase()
            .replace(/[^a-z0-9\s]/g, ' ')
            .split(/\s+/)
            .filter(w => w.length >= 5);

        if (requestWords.length === 0) return undefined;

        // ── Tier 1: Direct tool-name match ───────────────────────────
        // If ANY significant request word appears in the tool name itself, it's a strong signal.
        // e.g. "screenshot" from request matches "request_user_screenshot"
        for (const tool of active) {
            const nameWords = tool.toolName.toLowerCase().split('_');
            const nameMatchCount = requestWords.filter(rw =>
                nameWords.some(nw => nw.includes(rw) || rw.includes(nw))
            ).length;

            // At least one strong word match in the tool name → high confidence
            if (nameMatchCount >= 1) {
                console.log(`[Ledger] Tier-1 name match: "${tool.toolName}" (matched ${nameMatchCount} word(s))`);
                return tool;
            }
        }

        // ── Tier 2: Bidirectional keyword scoring ────────────────────
        let bestMatch: ToolRecord | undefined;
        let bestScore = 0;

        for (const tool of active) {
            const haystack = `${tool.toolName.replace(/_/g, ' ')} ${tool.description}`.toLowerCase();
            const matchCount = requestWords.filter(kw => haystack.includes(kw)).length;
            const score = matchCount / requestWords.length;

            if (score > bestScore && score >= 0.25) {
                bestScore = score;
                bestMatch = tool;
            }
        }

        if (bestMatch) {
            console.log(`[Ledger] Tier-2 keyword match: "${bestMatch.toolName}" (score=${bestScore.toFixed(2)})`);
        }

        return bestMatch;
    }

    /**
     * Get a statistical snapshot of all tools to support autonomous pruning.
     */
    async getStats(): Promise<Record<string, CapabilitySnapshot>> {
        await this.ensureLoaded();
        const stats: Record<string, CapabilitySnapshot> = {};

        for (const tool of this.manifest.tools) {
            // In a real system you'd want a more robust way to track consecutive
            // failures (e.g., an array of recent run results). For this MVP,
            // we'll infer it: if status is 'failed', it's failing.
            stats[tool.toolName] = {
                isSynthesized: tool.createdBy !== 'system',
                consecutiveFailures: tool.consecutiveFailures ?? 0,
                totalUses: tool.timesUsed,
                lastUsedAt: tool.lastUsedAt,
            };
        }

        return stats;
    }

    private async ensureLoaded(): Promise<void> {
        if (!this.loaded) await this.load();
    }

    private async save(): Promise<void> {
        await mkdir(SYNTHESIZED_DIR, { recursive: true });
        await writeFile(MANIFEST_PATH, JSON.stringify(this.manifest, null, 2), 'utf-8');
    }
}

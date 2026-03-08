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
}

interface Manifest {
    version: number;
    tools: ToolRecord[];
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
        if (!success) record.status = 'failed';

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

    private async ensureLoaded(): Promise<void> {
        if (!this.loaded) await this.load();
    }

    private async save(): Promise<void> {
        await mkdir(SYNTHESIZED_DIR, { recursive: true });
        await writeFile(MANIFEST_PATH, JSON.stringify(this.manifest, null, 2), 'utf-8');
    }
}

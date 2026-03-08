/**
 * StackOwl — Dynamic Tool Loader
 *
 * Loads AI-synthesized tools from src/tools/synthesized/ into the ToolRegistry.
 * Runs at startup (load all active) and on-demand after user approves a new tool.
 */

import { existsSync } from 'node:fs';
import { join } from 'node:path';
import type { ToolRegistry } from '../tools/registry.js';
import type { CapabilityLedger } from './ledger.js';
import { SYNTHESIZED_DIR } from './synthesizer.js';

export class DynamicToolLoader {
    constructor(private ledger: CapabilityLedger) {}

    /**
     * Load all active synthesized tools at startup.
     * Returns the count of successfully loaded tools.
     */
    async loadAll(registry: ToolRegistry): Promise<number> {
        await this.ledger.load();
        const active = this.ledger.listActive();
        let loaded = 0;

        for (const record of active) {
            const tsPath = join(SYNTHESIZED_DIR, record.fileName);

            if (!existsSync(tsPath)) {
                console.warn(`[Evolution] Synthesized tool file missing: ${record.fileName}`);
                continue;
            }

            const ok = await this.importAndRegister(tsPath, registry);
            if (ok) loaded++;
        }

        return loaded;
    }

    /**
     * Hot-load a single newly synthesized tool without restarting.
     * Uses a cache-busting query string so Node doesn't serve a stale cached import.
     */
    async loadOne(filePath: string, registry: ToolRegistry): Promise<boolean> {
        return this.importAndRegister(filePath, registry);
    }

    private async importAndRegister(filePath: string, registry: ToolRegistry): Promise<boolean> {
        try {
            // Cache-bust so a re-synthesized tool with the same name is always freshly loaded
            const url = `${filePath}?t=${Date.now()}`;
            const mod = await import(url);
            const tool = mod.default;

            if (!tool || !tool.definition || typeof tool.execute !== 'function') {
                console.error(`[Evolution] Invalid tool structure in: ${filePath}`);
                return false;
            }

            registry.register(tool);
            return true;
        } catch (err) {
            console.error(`[Evolution] Failed to load synthesized tool from ${filePath}:`, err);
            return false;
        }
    }
}

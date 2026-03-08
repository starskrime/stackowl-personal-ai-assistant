/**
 * StackOwl — File System Perch
 *
 * A Perch Point that passively watches the workspace for file changes.
 * Used for detecting structural changes or edits.
 */

import { watch, existsSync } from 'node:fs';
import { join } from 'node:path';
import type { PerchPoint, PerchEvent } from './manager.js';

export class FilePerch implements PerchPoint {
    name = 'FileSystem Watcher';
    private rootPath: string;
    private watcher: ReturnType<typeof watch> | null = null;
    private emitFn: ((event: PerchEvent) => void) | null = null;
    private debounceTimer: NodeJS.Timeout | null = null;
    private lastEventTime = 0;

    constructor(rootPath: string) {
        this.rootPath = rootPath;
    }

    async start(emit: (event: PerchEvent) => void): Promise<void> {
        this.emitFn = emit;

        try {
            // Prefer watching src/ to avoid noise from node_modules/builds.
            // Fall back to rootPath itself if src/ doesn't exist yet.
            const srcDir = join(this.rootPath, 'src');
            const targetDir = existsSync(srcDir) ? srcDir : this.rootPath;

            if (targetDir === this.rootPath) {
                console.log(`[FilePerch] workspace/src not found — watching workspace root instead.`);
            }

            this.watcher = watch(targetDir, { recursive: true }, (eventType, filename) => {
                if (filename && this.shouldProcess(filename)) {
                    this.handleFileChange(eventType, filename);
                }
            });
        } catch (error) {
            // Non-fatal — perch watching is a best-effort passive feature
            console.warn(`[FilePerch] Could not start watcher: ${error instanceof Error ? error.message : String(error)}`);
        }
    }

    stop(): void {
        if (this.watcher) {
            this.watcher.close();
            this.watcher = null;
        }
        if (this.debounceTimer) {
            clearTimeout(this.debounceTimer);
        }
    }

    private shouldProcess(filename: string): boolean {
        // Ignore dotfiles, temp files, and binary files
        if (filename.startsWith('.') || filename.endsWith('~') || filename.endsWith('.tmp')) return false;
        if (!filename.endsWith('.ts') && !filename.endsWith('.json') && !filename.endsWith('.md')) return false;

        // Ignore session files (Telegram and CLI sessions generate constant writes)
        if (filename.includes('sessions/')) return false;
        if (filename.includes('sessions\\')) return false;

        // Ignore pellet store and ledger writes — internal system files, not user-authored changes
        if (filename.includes('pellets/')) return false;
        if (filename.includes('synthesized/_manifest')) return false;

        return true;
    }

    private handleFileChange(eventType: string, filename: string) {
        // Debounce heavily. We don't want an alert every time the user hits Ctrl+S
        const now = Date.now();
        if (now - this.lastEventTime < 30000) {
            // Only allow one perch event every 30 seconds max to avoid spamming the LLM
            return;
        }

        if (this.debounceTimer) clearTimeout(this.debounceTimer);

        this.debounceTimer = setTimeout(() => {
            this.lastEventTime = Date.now();
            if (this.emitFn) {
                this.emitFn({
                    type: 'file_change',
                    source: `src/${filename}`,
                    details: `The user ${eventType === 'rename' ? 'created/deleted/renamed' : 'modified'} a source file.`,
                });
            }
        }, 5000); // Wait 5s after activity stops before firing
    }
}

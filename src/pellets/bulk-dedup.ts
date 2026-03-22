/**
 * StackOwl — Bulk Pellet Deduplicator
 *
 * One-time cleanup of pre-existing duplicate pellets.
 * Runs every pair through the dedup engine and merges/removes duplicates.
 */

import type { PelletStore } from './store.js';
import type { PelletDeduplicator } from './dedup.js';
import { log } from '../logger.js';

export interface BulkDedupStats {
  total: number;
  checked: number;
  skipped: number;
  merged: number;
  superseded: number;
  kept: number;
  errors: number;
}

/**
 * Run bulk deduplication across all existing pellets.
 *
 * Strategy: iterate pellets oldest-first. For each pellet, evaluate it
 * against all pellets that came before it. If a SKIP/MERGE/SUPERSEDE
 * verdict is returned, apply it immediately.
 */
export async function bulkDedup(
  store: PelletStore,
  deduplicator: PelletDeduplicator,
  opts?: { dryRun?: boolean },
): Promise<BulkDedupStats> {
  const dryRun = opts?.dryRun ?? false;
  const allPellets = await store.listAll();

  // Sort oldest-first so we keep the most recent version
  const sorted = [...allPellets].sort(
    (a, b) => new Date(a.generatedAt).getTime() - new Date(b.generatedAt).getTime(),
  );

  const stats: BulkDedupStats = {
    total: sorted.length,
    checked: 0,
    skipped: 0,
    merged: 0,
    superseded: 0,
    kept: 0,
    errors: 0,
  };

  const removed = new Set<string>();

  log.pellet.info(
    `[BulkDedup] Starting ${dryRun ? 'DRY RUN' : 'live'} dedup of ${sorted.length} pellets...`,
  );

  for (const pellet of sorted) {
    if (removed.has(pellet.id)) continue;

    stats.checked++;

    try {
      const result = await deduplicator.evaluate(pellet);

      switch (result.verdict) {
        case 'SKIP': {
          stats.skipped++;
          log.pellet.info(
            `[BulkDedup] SKIP: "${pellet.title.slice(0, 50)}" — ${result.reasoning}`,
          );
          if (!dryRun && result.targetPelletId) {
            await store.delete(pellet.id);
            removed.add(pellet.id);
          }
          break;
        }

        case 'MERGE': {
          stats.merged++;
          log.pellet.info(
            `[BulkDedup] MERGE: "${pellet.title.slice(0, 50)}" → "${result.targetPelletId}"`,
          );
          if (!dryRun && result.targetPelletId) {
            const target = await store.get(result.targetPelletId);
            if (target) {
              target.content = result.mergedContent || target.content;
              target.title = result.mergedTitle || target.title;
              target.tags = result.mergedTags || [
                ...new Set([...target.tags, ...pellet.tags]),
              ];
              target.owls = [...new Set([...target.owls, ...pellet.owls])];
              target.version = (target.version || 1) + 1;
              target.mergedFrom = [...(target.mergedFrom || []), pellet.id];
              target.lastMergedAt = new Date().toISOString();
              await store.save(target, { skipDedup: true });
              await store.delete(pellet.id);
              removed.add(pellet.id);
            }
          }
          break;
        }

        case 'SUPERSEDE': {
          stats.superseded++;
          log.pellet.info(
            `[BulkDedup] SUPERSEDE: "${pellet.title.slice(0, 50)}" replaces "${result.targetPelletId}"`,
          );
          if (!dryRun && result.targetPelletId) {
            pellet.supersedes = result.targetPelletId;
            await store.save(pellet, { skipDedup: true });
            await store.delete(result.targetPelletId);
            removed.add(result.targetPelletId);
          }
          break;
        }

        case 'CREATE':
        default:
          stats.kept++;
          break;
      }
    } catch (err) {
      stats.errors++;
      log.pellet.warn(
        `[BulkDedup] Error processing "${pellet.id}": ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    // Progress update every 25 pellets
    if (stats.checked % 25 === 0) {
      log.pellet.info(
        `[BulkDedup] Progress: ${stats.checked}/${sorted.length} ` +
        `(${stats.skipped} skipped, ${stats.merged} merged, ${stats.superseded} superseded)`,
      );
    }
  }

  log.pellet.info(
    `[BulkDedup] ${dryRun ? 'DRY RUN' : 'Done'}: ` +
    `${stats.total} total → ${stats.kept} kept, ` +
    `${stats.skipped} skipped, ${stats.merged} merged, ` +
    `${stats.superseded} superseded, ${stats.errors} errors`,
  );

  return stats;
}

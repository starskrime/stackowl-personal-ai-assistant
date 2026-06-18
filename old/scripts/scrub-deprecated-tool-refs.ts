#!/usr/bin/env tsx
/**
 * scripts/scrub-deprecated-tool-refs.ts
 *
 * One-shot rewriter run after Element 16c ships, to scrub stored data
 * that captured the old tool names while they were still LLM-visible:
 *
 *   - Pellet markdown bodies (under ~/.stackowl/pellets/)
 *   - attempt_log SQLite rows (memory.db) — note + suggestion columns
 *   - outcome-index.json (~/.stackowl/outcome-index.json)
 *
 * Replaces literal occurrences of `web_crawl` and `duckduckgo_search`
 * with `web_fetch` and `web_search`. Removes `scrapling_fetch` and
 * `camofox` from suggestions arrays (they are no longer LLM-visible).
 *
 * Usage: npx tsx scripts/scrub-deprecated-tool-refs.ts [--dry-run]
 */

import { readdirSync, readFileSync, writeFileSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import Database from "better-sqlite3";

export interface RewriteRule {
  from: RegExp;
  to: string;
}

const DEFAULT_RULES: RewriteRule[] = [
  { from: /\bweb_crawl\b/g, to: "web_fetch" },
  { from: /\bduckduckgo_search\b/g, to: "web_search" },
];

const DROP_TOKENS = new Set(["scrapling_fetch", "camofox"]);

export function rewriteText(input: string, rules: RewriteRule[] = DEFAULT_RULES): string {
  let out = input;
  for (const r of rules) out = out.replace(r.from, r.to);
  return out;
}

function scrubPellets(root: string, dry: boolean): number {
  let count = 0;
  const walk = (dir: string): void => {
    if (!existsSync(dir)) return;
    for (const entry of readdirSync(dir)) {
      const p = join(dir, entry);
      const s = statSync(p);
      if (s.isDirectory()) walk(p);
      else if (entry.endsWith(".md")) {
        const before = readFileSync(p, "utf-8");
        const after = rewriteText(before);
        if (before !== after) {
          if (!dry) writeFileSync(p, after);
          count++;
        }
      }
    }
  };
  walk(root);
  return count;
}

function scrubAttemptLog(dbPath: string, dry: boolean): number {
  if (!existsSync(dbPath)) return 0;
  const db = new Database(dbPath);
  try {
    const rows = db.prepare(
      `SELECT id, note, suggestion FROM attempt_log WHERE note LIKE '%web_crawl%' OR note LIKE '%duckduckgo_search%' OR suggestion LIKE '%web_crawl%' OR suggestion LIKE '%duckduckgo_search%'`
    ).all() as Array<{ id: number; note: string | null; suggestion: string | null }>;
    if (!dry) {
      const upd = db.prepare(`UPDATE attempt_log SET note = ?, suggestion = ? WHERE id = ?`);
      for (const r of rows) {
        upd.run(
          r.note ? rewriteText(r.note) : r.note,
          r.suggestion ? rewriteText(r.suggestion) : r.suggestion,
          r.id,
        );
      }
    }
    return rows.length;
  } finally {
    db.close();
  }
}

function scrubOutcomeIndex(path: string, dry: boolean): number {
  if (!existsSync(path)) return 0;
  const before = readFileSync(path, "utf-8");
  let parsed: any;
  try { parsed = JSON.parse(before); } catch { return 0; }
  const filterArrays = (obj: any): any => {
    if (Array.isArray(obj)) return obj.filter((s) => typeof s !== "string" || !DROP_TOKENS.has(s)).map(filterArrays);
    if (obj && typeof obj === "object") return Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, filterArrays(v)]));
    if (typeof obj === "string") return rewriteText(obj);
    return obj;
  };
  const cleaned = filterArrays(parsed);
  const after = JSON.stringify(cleaned, null, 2);
  if (after === before) return 0;
  if (!dry) writeFileSync(path, after);
  return 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const dry = process.argv.includes("--dry-run");
  const home = homedir();
  const pelletsRoot = join(home, ".stackowl", "pellets");
  const dbPath = join(home, ".stackowl", "memory.db");
  const outcomeIndex = join(home, ".stackowl", "outcome-index.json");

  const a = scrubPellets(pelletsRoot, dry);
  const b = scrubAttemptLog(dbPath, dry);
  const c = scrubOutcomeIndex(outcomeIndex, dry);
  console.log(`[scrubber] pellets rewritten: ${a}; attempt_log rows: ${b}; outcome-index: ${c}${dry ? " (dry-run)" : ""}`);
}

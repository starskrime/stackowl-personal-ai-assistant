/**
 * StackOwl Observability — JSONL log reader
 *
 * Streams records from on-disk JSONL files with filter support.
 * Used by:
 *   - read_logs tool (src/tools/read-logs.ts)
 *   - CognitiveLoop log-analysis tick
 */

import { createReadStream, readdirSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline";
import type { LogLevel, LogRecord } from "./schema.js";

export interface LogQuery {
  traceId?:     string;
  sessionId?:   string;
  userId?:      string;
  module?:      string | string[];
  level?:       LogLevel;          // minimum level — records below this are excluded
  errorOnly?:   boolean;           // convenience: same as level="error"
  since?:       Date | number;     // ms epoch or Date — inclusive lower bound on ts
  until?:       Date | number;     // ms epoch or Date — inclusive upper bound on ts
  contains?:    string;            // substring match on msg (case-insensitive)
  limit?:       number;            // max records to return (default 200, hard cap 1000)
}

const HARD_CAP = 1000;
const LEVEL_ORDER: LogLevel[] = ["debug", "info", "warn", "error", "fatal"];

function levelIndex(l: LogLevel): number {
  return LEVEL_ORDER.indexOf(l);
}

function moduleMatch(record: LogRecord, modules: string | string[]): boolean {
  const list = Array.isArray(modules) ? modules : [modules];
  return list.some((m) => record.module === m || record.module.startsWith(m + "."));
}

function toMs(v: Date | number): number {
  return typeof v === "number" ? v : v.getTime();
}

function matches(record: LogRecord, q: LogQuery): boolean {
  if (q.traceId   && record.traceId   !== q.traceId)   return false;
  if (q.sessionId && record.sessionId !== q.sessionId)  return false;
  if (q.userId    && record.userId    !== q.userId)     return false;
  if (q.module    && !moduleMatch(record, q.module))    return false;

  const minLevel = q.errorOnly ? "error" : (q.level ?? "debug");
  if (levelIndex(record.level) < levelIndex(minLevel)) return false;

  if (q.since) {
    const recMs = new Date(record.ts).getTime();
    if (recMs < toMs(q.since)) return false;
  }
  if (q.until) {
    const recMs = new Date(record.ts).getTime();
    if (recMs > toMs(q.until)) return false;
  }

  if (q.contains && !record.msg.toLowerCase().includes(q.contains.toLowerCase())) {
    return false;
  }

  return true;
}

/** Determine which log files could contain records in the query window. */
function candidateFiles(logsDir: string, q: LogQuery): string[] {
  let names: string[];
  try {
    names = readdirSync(logsDir).filter(
      (n) => n.startsWith("stackowl-") && n.endsWith(".log"),
    );
  } catch {
    return [];
  }

  names.sort(); // ascending date order

  if (!q.since && !q.until) {
    // Return last 2 days by default
    return names.slice(-2).map((n) => join(logsDir, n));
  }

  const sinceMs = q.since ? toMs(q.since) : 0;
  const untilMs = q.until ? toMs(q.until) : Infinity;

  return names
    .filter((n) => {
      const dateStr = n.replace("stackowl-", "").replace(".log", ""); // YYYY-MM-DD
      const dayStart = new Date(dateStr + "T00:00:00Z").getTime();
      const dayEnd   = dayStart + 86_400_000;
      return dayEnd >= sinceMs && dayStart <= untilMs;
    })
    .map((n) => join(logsDir, n));
}

/** Read lines from a single JSONL file, newest first (reverse). */
async function* _readFileReverse(filePath: string): AsyncIterable<LogRecord> {
  // For simplicity, read the whole file into memory and reverse.
  // Log files are bounded by daily rotation, so this is fine.
  const lines: string[] = [];
  const rl = createInterface({ input: createReadStream(filePath), crlfDelay: Infinity });
  for await (const line of rl) {
    if (line.trim()) lines.push(line);
  }
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      yield JSON.parse(lines[i]) as LogRecord;
    } catch {
      // Malformed line — skip
    }
  }
}

/**
 * Stream log records matching the query, newest first.
 * Stops after `limit` records.
 */
export async function* readLogs(logsDir: string, q: LogQuery): AsyncIterable<LogRecord> {
  const limit = Math.min(q.limit ?? 200, HARD_CAP);
  const files = candidateFiles(logsDir, q).reverse(); // most recent first

  let count = 0;
  for (const file of files) {
    for await (const record of _readFileReverse(file)) {
      if (matches(record, q)) {
        yield record;
        count++;
        if (count >= limit) return;
      }
    }
  }
}

/**
 * Collect all matching records into an array (newest first, up to limit).
 * Convenience wrapper around readLogs.
 */
export async function readLogsArray(logsDir: string, q: LogQuery): Promise<LogRecord[]> {
  const results: LogRecord[] = [];
  for await (const record of readLogs(logsDir, q)) {
    results.push(record);
  }
  return results;
}

/**
 * StackOwl Observability — JSONL file sink
 *
 * Daily-rotated file: logs/stackowl-YYYY-MM-DD.log
 * Each line is a valid JSON object (LogRecord).
 * Rotation is checked on every write — no background timer needed.
 * Sync appendFileSync is preserved for crash durability (matches legacy behaviour).
 * Log files older than retentionDays (default 7) are pruned on init.
 */

import {
  appendFileSync,
  mkdirSync,
  readdirSync,
  statSync,
  unlinkSync,
} from "node:fs";
import { join } from "node:path";
import type { LogRecord } from "../schema.js";
import type { RedactTarget } from "../redact.js";
import { redactRecord } from "../redact.js";

// ── State ─────────────────────────────────────────────────────────

let _logsDir: string | null = null;
let _logFilePath: string | null = null;
let _currentDate: string | null = null; // YYYY-MM-DD of the open file
let _retentionDays = 7;
let _redactTargets: RedactTarget[] = ["tokens", "emails"];

/** Call once at startup. Path: <workspacePath>/logs/ */
export function initFileLog(workspacePath: string, opts?: {
  retentionDays?: number;
  redact?: RedactTarget[];
}): void {
  _logsDir = join(workspacePath, "logs");
  _retentionDays = opts?.retentionDays ?? 7;
  _redactTargets = opts?.redact ?? ["tokens", "emails"];

  mkdirSync(_logsDir, { recursive: true });
  _rotateIfNeeded();
  _pruneOldLogs();
}

// ── Internal helpers ──────────────────────────────────────────────

function _today(): string {
  return new Date().toISOString().slice(0, 10); // YYYY-MM-DD
}

function _rotateIfNeeded(): void {
  const today = _today();
  if (_currentDate === today) return;
  _currentDate = today;
  _logFilePath = join(_logsDir!, `stackowl-${today}.log`);
}

function _pruneOldLogs(): void {
  if (!_logsDir) return;
  const cutoff = Date.now() - _retentionDays * 24 * 60 * 60 * 1000;
  try {
    for (const name of readdirSync(_logsDir)) {
      if (!name.startsWith("stackowl-") || !name.endsWith(".log")) continue;
      const fullPath = join(_logsDir, name);
      try {
        if (statSync(fullPath).mtimeMs < cutoff) unlinkSync(fullPath);
      } catch {
        // Individual file stat/unlink failures are non-fatal
      }
    }
  } catch {
    // Non-fatal: logsDir may be unavailable temporarily
  }
}

// ── Write ─────────────────────────────────────────────────────────

/** Write a single log record to the current day's JSONL file. */
export function writeRecord(record: LogRecord): void {
  if (!_logsDir) return;
  _rotateIfNeeded();

  // Clone before mutation so the in-memory ring buffer holds the original
  const r = { ...record } as Record<string, unknown>;
  redactRecord(r, _redactTargets);

  try {
    appendFileSync(_logFilePath!, JSON.stringify(r) + "\n");
  } catch {
    // Non-fatal: disk full, permissions, etc.
  }
}

/** Exposed for tests to verify the current log file path. */
export function currentLogFilePath(): string | null {
  return _logFilePath;
}

/** Exposed for tests to force a date rotation. */
export function _forceDate(date: string): void {
  _currentDate = date;
  _logFilePath = _logsDir ? join(_logsDir, `stackowl-${date}.log`) : null;
}

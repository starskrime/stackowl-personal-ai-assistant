/**
 * StackOwl Observability — Pretty console sink
 *
 * Active ONLY when STACKOWL_TUI_MOUNTED is NOT "1".
 * Writes to process.stderr (never stdout) to avoid Ink buffer corruption.
 * One-line, color-coded format: HH:mm:ss.SSS LEVEL [module] msg [traceId]
 */

import type { LogRecord } from "../schema.js";

// ── ANSI colours (no external deps) ──────────────────────────────

const RESET = "\x1b[0m";
const GREY  = "\x1b[90m";
const CYAN  = "\x1b[36m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED   = "\x1b[31m";
const BOLD_RED = "\x1b[1;31m";

function levelColor(level: string): string {
  switch (level) {
    case "debug": return GREY;
    case "info":  return CYAN;
    case "warn":  return YELLOW;
    case "error": return RED;
    case "fatal": return BOLD_RED;
    default:      return RESET;
  }
}

function shortTime(ts: string): string {
  // "2026-05-10T10:00:00.123Z" → "10:00:00.123"
  return ts.slice(11, 23);
}

let _enabled = false;

/** Call once at startup when TUI is NOT mounted. */
export function initPrettyConsole(): void {
  _enabled = process.env.STACKOWL_TUI_MOUNTED !== "1";
}

export function writePretty(record: LogRecord): void {
  if (!_enabled) return;
  const lc = levelColor(record.level);
  const lvl = record.level.toUpperCase().padEnd(5);
  const trace = record.traceId ? ` ${GREY}${record.traceId.slice(0, 8)}…${RESET}` : "";
  const errSuffix = record.err ? ` ${RED}${record.err.name}: ${record.err.message}${RESET}` : "";
  const line =
    `${GREY}${shortTime(record.ts)}${RESET} ` +
    `${lc}${lvl}${RESET} ` +
    `${GREEN}[${record.module}]${RESET} ` +
    `${record.msg}${errSuffix}${trace}\n`;
  process.stderr.write(line);
}

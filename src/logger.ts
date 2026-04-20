/**
 * StackOwl — Logger
 *
 * All output goes to logs/stackowl-YYYY-MM-DD.log (daily rotation).
 * Log files older than 7 days are pruned on startup.
 * Nothing is written to stdout/stderr — the terminal UI owns the screen.
 */

import {
  appendFileSync,
  mkdirSync,
  readdirSync,
  statSync,
  unlinkSync,
} from "node:fs";
import { join } from "node:path";
import type { ChatMessage } from "./providers/base.js";

// ─── File state ───────────────────────────────────────────────────

let _logsDir: string | null = null;
let _logFilePath: string | null = null;

/** Call once at startup to enable file logging and prune old files. */
export function initFileLog(workspacePath: string): void {
  _logsDir = join(workspacePath, "logs");
  mkdirSync(_logsDir, { recursive: true });

  const date = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
  _logFilePath = join(_logsDir, `stackowl-${date}.log`);

  // Append session header (file may already exist from earlier today)
  appendFileSync(
    _logFilePath,
    `\n=== StackOwl Session ===  started: ${new Date().toISOString()}\n${"=".repeat(60)}\n\n`,
  );

  _pruneOldLogs();
}

/** Delete log files older than 7 days. */
function _pruneOldLogs(): void {
  if (!_logsDir) return;
  const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
  try {
    for (const name of readdirSync(_logsDir)) {
      if (!name.startsWith("stackowl-") || !name.endsWith(".log")) continue;
      const fullPath = join(_logsDir!, name);
      const mtime = statSync(fullPath).mtimeMs;
      if (mtime < cutoff) {
        unlinkSync(fullPath);
      }
    }
  } catch {
    // Non-fatal
  }
}

// ─── Write helpers ────────────────────────────────────────────────

function _ts(): string {
  return new Date().toISOString().slice(11, 23); // HH:mm:ss.SSS
}

function _write(line: string): void {
  if (!_logFilePath) return;
  try {
    appendFileSync(_logFilePath, line + "\n");
  } catch {
    // Non-fatal
  }
}

function _writeLine(mod: string, level: string, msg: string): void {
  _write(`[${_ts()}] [${mod}] [${level}] ${msg}`);
}

function _writeBlock(header: string, content: string): void {
  const bar = "─".repeat(60);
  _write(`\n[${_ts()}] ${bar}`);
  _write(`  ${header}`);
  _write(bar);
  for (const line of content.split("\n")) {
    _write(`  ${line}`);
  }
  _write(bar + "\n");
}

// ─── Logger class ─────────────────────────────────────────────────

export type LogLevel = "debug" | "info" | "warn" | "error";

export class Logger {
  private module: string;

  constructor(module: string) {
    this.module = module.toUpperCase();
  }

  debug(msg: string, ..._extra: unknown[]): void {
    _writeLine(this.module, "DBG", msg);
  }

  info(msg: string, ..._extra: unknown[]): void {
    _writeLine(this.module, "INF", msg);
  }

  warn(msg: string, ..._extra: unknown[]): void {
    _writeLine(this.module, "WRN", msg);
  }

  error(msg: string, ..._extra: unknown[]): void {
    _writeLine(this.module, "ERR", msg);
  }

  // ─── Channel I/O ──────────────────────────────────────────────

  incoming(from: string, text: string): void {
    _writeBlock(`← USER [${from}]`, text);
  }

  outgoing(to: string, text: string): void {
    _writeBlock(`→ OWL [${to}]`, text);
  }

  // ─── LLM I/O ─────────────────────────────────────────────────

  llmRequest(model: string, messages: ChatMessage[]): void {
    const lines: string[] = [`Model: ${model}  |  Messages: ${messages.length}`];
    for (const msg of messages) {
      const role = (msg.role as string).toUpperCase();
      const toolName = (msg as any).name ? `:${(msg as any).name}` : "";
      let body = (msg.content || "").slice(0, 500);
      if (msg.toolCalls && msg.toolCalls.length > 0) {
        const calls = msg.toolCalls
          .map((t) => `  call: ${t.name} ${JSON.stringify(t.arguments)}`)
          .join("\n");
        body = body ? `${body}\n${calls}` : calls;
      }
      lines.push(`[${role}${toolName}] ${body || "(empty)"}`);
    }
    _writeBlock("→ LLM REQUEST", lines.join("\n"));
  }

  llmResponse(
    model: string,
    content: string,
    toolCalls?: Array<{ name: string; arguments: Record<string, unknown> }>,
    usage?: { promptTokens: number; completionTokens: number },
  ): void {
    const tokenStr = usage
      ? ` [${usage.promptTokens}→${usage.completionTokens} tokens]`
      : "";
    const parts: string[] = [`Model: ${model}${tokenStr}`];
    if (content) parts.push(`[CONTENT] ${content.slice(0, 1000)}`);
    if (toolCalls && toolCalls.length > 0) {
      parts.push("[TOOL CALLS]");
      for (const tc of toolCalls) {
        parts.push(`  ${tc.name} ${JSON.stringify(tc.arguments)}`);
      }
    }
    _writeBlock("← LLM RESPONSE", parts.join("\n"));
  }

  // ─── Tool I/O ─────────────────────────────────────────────────

  toolCall(name: string, args?: Record<string, unknown>): void {
    _writeLine(this.module, "TOOL", `CALL  ${name} ${args ? JSON.stringify(args) : ""}`);
  }

  toolResult(name: string, result: string, success: boolean): void {
    _writeLine(this.module, "TOOL", `RESULT ${name} ${success ? "✓" : "✗"} ${result.slice(0, 500)}`);
  }

  // ─── Misc ─────────────────────────────────────────────────────

  model(selected: string, reason?: string): void {
    const r = reason ? ` (${reason})` : "";
    _writeLine(this.module, "INF", `model → ${selected}${r}`);
  }

  evolve(msg: string): void {
    _writeLine(this.module, "EVOLVE", msg);
  }

  separator(): void {
    _write("  " + "─".repeat(60));
  }
}

// ─── Singletons ───────────────────────────────────────────────────

export const log = {
  telegram: new Logger("TELEGRAM"),
  slack:    new Logger("SLACK"),
  cli:      new Logger("CLI"),
  engine:   new Logger("ENGINE"),
  tool:     new Logger("TOOL"),
  evolution: new Logger("EVOLUTION"),
  memory:   new Logger("MEMORY"),
  heartbeat: new Logger("HEARTBEAT"),
  pellet:   new Logger("PELLET"),
};

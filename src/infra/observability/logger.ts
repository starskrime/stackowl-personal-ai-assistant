/**
 * StackOwl Observability — Core logger
 *
 * getLogger(module) returns a Logger bound to a named module.
 * Every Logger method enriches a LogRecord from the AsyncLocalStorage context
 * (traceId, spanId, sessionId, owl, model, …) before writing to sinks.
 *
 * Sinks (write in order):
 *   1. RingBuffer (always — needed for TUI overlay + tests)
 *   2. JSONL file  (when initFileLog() has been called)
 *   3. Pretty console (when not TUI-mounted)
 *
 * Back-compat methods preserved verbatim for the legacy log.* API:
 *   incoming, outgoing, llmRequest, llmResponse, toolCall, toolResult,
 *   model, evolve, behavioral, separator
 */

import type { ChatMessage } from "../../providers/base.js";
import { currentTrace } from "./context.js";
import type { LogLevel, LogRecord } from "./schema.js";
import { writeRecord } from "./sinks/jsonl-file.js";
import { getRingBuffer } from "./sinks/ring-buffer.js";
import { writePretty } from "./sinks/pretty-console.js";

// ── Minimum level (configurable after init) ───────────────────────

let _minLevel: LogLevel = "info";
const LEVELS: LogLevel[] = ["debug", "info", "warn", "error", "fatal"];

export function setMinLevel(level: LogLevel): void {
  _minLevel = level;
}

function _shouldEmit(level: LogLevel): boolean {
  return LEVELS.indexOf(level) >= LEVELS.indexOf(_minLevel);
}

// ── Record builder & dispatch ─────────────────────────────────────

function _emit(module: string, level: LogLevel, msg: string, opts?: {
  err?: unknown;
  fields?: Record<string, unknown>;
  spanName?: string;
  durationMs?: number;
}): void {
  if (!_shouldEmit(level)) return;

  const ctx = currentTrace();
  const record: LogRecord = {
    ts: new Date().toISOString(),
    level,
    module,
    msg,
    schemaVersion: 1,
    ...(ctx?.traceId      && { traceId:      ctx.traceId }),
    ...(ctx?.spanId       && { spanId:       ctx.spanId }),
    ...(ctx?.parentSpanId && { parentSpanId: ctx.parentSpanId }),
    ...(ctx?.spanName     && { spanName:     ctx.spanName }),
    ...(ctx?.sessionId    && { sessionId:    ctx.sessionId }),
    ...(ctx?.userId       && { userId:       ctx.userId }),
    ...(ctx?.channelId    && { channelId:    ctx.channelId }),
    ...(ctx?.messageId    && { messageId:    ctx.messageId }),
    ...(ctx?.owl          && { owl:          ctx.owl }),
    ...(ctx?.model        && { model:        ctx.model }),
    ...(opts?.spanName    && { spanName:     opts.spanName }),
    ...(opts?.durationMs !== undefined && { durationMs: opts.durationMs }),
    ...(opts?.fields      && { fields:       opts.fields }),
  };

  if (opts?.err != null) {
    const e = opts.err;
    if (e instanceof Error) {
      record.err = {
        name:    e.name,
        message: e.message,
        stack:   e.stack,
        ...(e.cause != null ? { cause: String(e.cause) } : {}),
      };
    } else {
      record.err = { name: "UnknownError", message: String(e) };
    }
  }

  // Write to all active sinks
  getRingBuffer().push(record);
  writeRecord(record);
  writePretty(record);
}

// ── Span lifecycle helpers (called from context.ts withSpan) ──────

/** @internal — called by context.withSpan */
export function _emitSpanStart(spanName: string, fields?: Record<string, unknown>): void {
  _emit(spanName, "debug", `span.start: ${spanName}`, { fields });
}

/** @internal — called by context.withSpan */
export function _emitSpanEnd(
  spanName: string,
  durationMs: number,
  threw: boolean,
  fields?: Record<string, unknown>,
): void {
  const level: LogLevel = threw ? "warn" : "debug";
  _emit(spanName, level, `span.end: ${spanName}`, { durationMs, fields });
}

// ── Logger class ──────────────────────────────────────────────────

export class Logger {
  private readonly _module: string;
  private readonly _baseFields?: Record<string, unknown>;

  constructor(module: string, baseFields?: Record<string, unknown>) {
    this._module = module;
    this._baseFields = baseFields;
  }

  private _fields(extra?: Record<string, unknown>): Record<string, unknown> | undefined {
    if (!this._baseFields && !extra) return undefined;
    return { ...this._baseFields, ...extra };
  }

  debug(msg: string, fields?: Record<string, unknown>): void {
    _emit(this._module, "debug", msg, { fields: this._fields(fields) });
  }

  info(msg: string, fields?: Record<string, unknown>): void {
    _emit(this._module, "info", msg, { fields: this._fields(fields) });
  }

  warn(msg: string, err?: unknown, fields?: Record<string, unknown>): void {
    _emit(this._module, "warn", msg, { err, fields: this._fields(fields) });
  }

  error(msg: string, err?: unknown, fields?: Record<string, unknown>): void {
    _emit(this._module, "error", msg, { err, fields: this._fields(fields) });
  }

  fatal(msg: string, err?: unknown, fields?: Record<string, unknown>): void {
    _emit(this._module, "fatal", msg, { err, fields: this._fields(fields) });
  }

  /** Create a child logger that inherits this module name with a sub-suffix. */
  child(submodule: string, fields?: Record<string, unknown>): Logger {
    return new Logger(`${this._module}.${submodule}`, { ...this._baseFields, ...fields });
  }

  // ── Back-compat helpers (kept verbatim from legacy Logger) ────────

  incoming(from: string, text: string): void {
    _emit(this._module, "info", `← USER [${from}]`, {
      fields: this._fields({ direction: "in", from, text: text.slice(0, 1000) }),
    });
  }

  outgoing(to: string, text: string): void {
    _emit(this._module, "info", `→ OWL [${to}]`, {
      fields: this._fields({ direction: "out", to, text: text.slice(0, 1000) }),
    });
  }

  llmRequest(model: string, messages: ChatMessage[]): void {
    _emit(this._module, "debug", `→ LLM REQUEST`, {
      fields: this._fields({
        model,
        messageCount: messages.length,
        messages: messages.map((m) => ({
          role: m.role,
          content: (m.content || "").slice(0, 300),
          toolCalls: m.toolCalls?.map((t) => t.name),
        })),
      }),
    });
  }

  llmResponse(
    model: string,
    content: string,
    toolCalls?: Array<{ name: string; arguments: Record<string, unknown> }>,
    usage?: { promptTokens: number; completionTokens: number },
  ): void {
    _emit(this._module, "debug", `← LLM RESPONSE`, {
      fields: this._fields({
        model,
        content: content.slice(0, 500),
        toolCalls: toolCalls?.map((t) => t.name),
        promptTokens:     usage?.promptTokens,
        completionTokens: usage?.completionTokens,
      }),
    });
  }

  toolCall(name: string, args?: Record<string, unknown>): void {
    _emit(this._module, "debug", `tool.call: ${name}`, {
      fields: this._fields({ tool: name, args }),
    });
  }

  toolResult(name: string, result: string, success: boolean): void {
    _emit(this._module, "debug", `tool.result: ${name}`, {
      fields: this._fields({ tool: name, success, result: result.slice(0, 500) }),
    });
  }

  model(selected: string, reason?: string): void {
    _emit(this._module, "info", `model → ${selected}`, {
      fields: this._fields({ model: selected, reason }),
    });
  }

  evolve(msg: string): void {
    _emit(this._module, "info", `evolve: ${msg}`, { fields: this._baseFields });
  }

  behavioral(event: string, data?: Record<string, unknown>): void {
    _emit(this._module, "info", `behavioral: ${event}`, {
      fields: this._fields(data),
    });
  }

  separator(): void {
    _emit(this._module, "debug", "────────────────────────────────────────────────────────────");
  }
}

// ── Factory ───────────────────────────────────────────────────────

const _cache = new Map<string, Logger>();

/** Get (or create) a module-scoped logger. Results are cached. */
export function getLogger(module: string, baseFields?: Record<string, unknown>): Logger {
  const key = baseFields ? `${module}::${JSON.stringify(baseFields)}` : module;
  let logger = _cache.get(key);
  if (!logger) {
    logger = new Logger(module, baseFields);
    _cache.set(key, logger);
  }
  return logger;
}

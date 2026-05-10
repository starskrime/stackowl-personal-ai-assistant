/**
 * StackOwl Observability — public barrel export
 *
 * Prefer importing directly from sub-modules to keep tree-shaking effective.
 * This barrel is for external code that wants a single entry point.
 */

export type { LogLevel, LogRecord } from "./schema.js";
export type { TraceContext } from "./context.js";
export type { LogQuery } from "./reader.js";
export type { LogSummary } from "./analyzer.js";
export type { RedactTarget } from "./redact.js";

export { randomTraceId, randomSpanId, w3cTraceparent, parseTraceparent } from "./ids.js";
export { currentTrace, runWithContext, withSpan, attachToContext } from "./context.js";
export { getLogger, setMinLevel, Logger } from "./logger.js";
export { initFileLog, writeRecord, currentLogFilePath } from "./sinks/jsonl-file.js";
export { getRingBuffer, resetRingBuffer, RingBuffer } from "./sinks/ring-buffer.js";
export { initPrettyConsole } from "./sinks/pretty-console.js";
export { readLogs, readLogsArray } from "./reader.js";
export { summarize } from "./analyzer.js";

// Back-compat re-exports (legacy consumers that import from src/logger.ts
// will continue to work since that file re-exports from compat.ts which
// re-exports the same symbols under the same names)
export { log, initFileLog as initFileLogCompat } from "./compat.js";

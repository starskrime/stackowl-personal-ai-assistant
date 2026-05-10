/**
 * StackOwl Observability — Log record schema
 *
 * W3C Trace Context-aligned (OTel-compatible) wire format.
 * traceId is 32-hex (16 bytes), spanId is 16-hex (8 bytes).
 * Every JSONL log line deserialises into LogRecord.
 */

export type LogLevel = "debug" | "info" | "warn" | "error" | "fatal";

export interface LogRecord {
  // ── W3C Trace Context (OTel-compatible) ──────────────────────────
  ts: string;               // ISO-8601 with ms: 2026-05-10T10:00:00.123Z
  level: LogLevel;
  module: string;           // e.g. "engine", "gateway.cli", "tool.read_logs"
  msg: string;              // human-readable; never double-JSON-encoded

  traceId?: string;         // 32-hex — one per originating request / background tick
  spanId?: string;          // 16-hex — one per operation
  parentSpanId?: string;    // 16-hex — links child → parent
  spanName?: string;        // operation name (set inside withSpan)
  durationMs?: number;      // populated on span END records only

  // ── Correlation baggage ───────────────────────────────────────────
  sessionId?: string;       // "<channelId>:<userId>"
  userId?: string;
  channelId?: string;       // "cli" | "telegram" | "slack" | "voice" | "cognition" | "heartbeat"
  messageId?: string;       // per-message unique ID (was GatewayMessage.id)
  owl?: string;             // active owl name after routing
  model?: string;           // active model after model-router resolution

  // ── Error ─────────────────────────────────────────────────────────
  err?: {
    name: string;
    message: string;
    stack?: string;
    cause?: string;
  };

  // ── Free-form payload ─────────────────────────────────────────────
  fields?: Record<string, unknown>;

  // ── Schema versioning (for reader/analyzer forward-compat) ────────
  schemaVersion: 1;
}

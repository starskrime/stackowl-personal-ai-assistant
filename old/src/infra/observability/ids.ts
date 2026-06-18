/**
 * StackOwl Observability — Trace ID generators
 *
 * W3C Trace Context-compatible format.
 * No external deps — uses node:crypto.
 */

import { randomBytes } from "node:crypto";

/** 32 lowercase hex characters (16 bytes) — matches W3C traceId. */
export function randomTraceId(): string {
  return randomBytes(16).toString("hex");
}

/** 16 lowercase hex characters (8 bytes) — matches W3C spanId. */
export function randomSpanId(): string {
  return randomBytes(8).toString("hex");
}

/**
 * W3C traceparent header value.
 * Format: 00-<traceId>-<spanId>-<flags>
 * flags=01 means sampled.
 * No OTel SDK needed — this is just a string encoding for queue propagation.
 */
export function w3cTraceparent(traceId: string, spanId: string, flags = "01"): string {
  return `00-${traceId}-${spanId}-${flags}`;
}

/**
 * Parse a W3C traceparent string back into its parts.
 * Returns null if malformed.
 */
export function parseTraceparent(header: string): {
  traceId: string;
  spanId: string;
  flags: string;
} | null {
  const parts = header.split("-");
  if (parts.length !== 4 || parts[0] !== "00") return null;
  const [, traceId, spanId, flags] = parts;
  if (traceId.length !== 32 || spanId.length !== 16) return null;
  return { traceId, spanId, flags };
}

/**
 * StackOwl Observability — Vitest test sink
 *
 * Captures log records in memory so tests can make assertions without touching
 * the filesystem.  Import and wire it before the code under test runs:
 *
 *   import { installTestSink, capturedLogs, clearTestSink } from ".../test-sink.js";
 *   beforeAll(() => installTestSink());
 *   afterEach(() => clearTestSink());
 */

import type { LogRecord } from "../schema.js";
import { getRingBuffer, resetRingBuffer } from "./ring-buffer.js";

const _captured: LogRecord[] = [];
let _unsubscribe: (() => void) | null = null;

/** Install the test sink (subscribe to the ring buffer). Idempotent. */
export function installTestSink(capacity = 1000): void {
  resetRingBuffer(capacity);
  if (_unsubscribe) _unsubscribe();
  _unsubscribe = getRingBuffer().subscribe((r) => _captured.push(r));
}

/** All records captured since the last clearTestSink() call. */
export function capturedLogs(): ReadonlyArray<LogRecord> {
  return _captured;
}

/** Clear captured records without unsubscribing. */
export function clearTestSink(): void {
  _captured.length = 0;
  getRingBuffer().clear();
}

/** Unsubscribe and clear. */
export function uninstallTestSink(): void {
  if (_unsubscribe) {
    _unsubscribe();
    _unsubscribe = null;
  }
  clearTestSink();
}

/** Find the first record matching a predicate. */
export function findLog(
  pred: (r: LogRecord) => boolean,
): LogRecord | undefined {
  return _captured.find(pred);
}

/** Assert at least one captured record satisfies a predicate. Throws otherwise. */
export function assertLog(pred: (r: LogRecord) => boolean, message?: string): void {
  if (!_captured.find(pred)) {
    throw new Error(message ?? "No matching log record found");
  }
}

/** Return all records for a given traceId, in insertion order. */
export function logsForTrace(traceId: string): LogRecord[] {
  return _captured.filter((r) => r.traceId === traceId);
}

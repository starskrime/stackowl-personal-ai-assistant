/**
 * StackOwl Observability — In-memory ring buffer sink
 *
 * Bounded FIFO; oldest record is dropped when capacity is exceeded.
 * Powers the optional TUI v2 debug overlay and vitest assertions.
 */

import type { LogRecord } from "../schema.js";

export type RingListener = (record: LogRecord) => void;

export class RingBuffer {
  private readonly _buf: LogRecord[];
  private _head = 0;
  private _size = 0;
  private readonly _cap: number;
  private readonly _listeners: Set<RingListener> = new Set();

  constructor(capacity = 5000) {
    this._cap = capacity;
    this._buf = new Array<LogRecord>(capacity);
  }

  push(record: LogRecord): void {
    this._buf[this._head] = record;
    this._head = (this._head + 1) % this._cap;
    if (this._size < this._cap) this._size++;
    for (const fn of this._listeners) {
      try { fn(record); } catch { /* listener errors must not crash logging */ }
    }
  }

  /** Subscribe to new records (live tail). Returns an unsubscribe function. */
  subscribe(fn: RingListener): () => void {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  /** Return the stored records in insertion order (oldest first). */
  toArray(): LogRecord[] {
    if (this._size < this._cap) {
      return this._buf.slice(0, this._size);
    }
    // Buffer wrapped — pivot around head
    return [
      ...this._buf.slice(this._head),
      ...this._buf.slice(0, this._head),
    ];
  }

  /** Clear all stored records (useful in tests). */
  clear(): void {
    this._head = 0;
    this._size = 0;
  }

  get length(): number {
    return this._size;
  }
}

// Module-level singleton, lazily created.
let _instance: RingBuffer | null = null;

export function getRingBuffer(capacity?: number): RingBuffer {
  if (!_instance) _instance = new RingBuffer(capacity);
  return _instance;
}

export function resetRingBuffer(capacity = 5000): void {
  _instance = new RingBuffer(capacity);
}

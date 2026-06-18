import { describe, it, expect, beforeEach } from "vitest";
import { RingBuffer, getRingBuffer, resetRingBuffer } from "../../src/infra/observability/sinks/ring-buffer.js";
import type { LogRecord } from "../../src/infra/observability/schema.js";

function makeRecord(msg: string): LogRecord {
  return {
    ts: new Date().toISOString(),
    level: "info",
    module: "test",
    msg,
    schemaVersion: 1,
  };
}

describe("RingBuffer", () => {
  beforeEach(() => {
    resetRingBuffer();
  });

  describe("push and toArray", () => {
    it("preserves insertion order", () => {
      const buf = new RingBuffer(10);
      buf.push(makeRecord("first"));
      buf.push(makeRecord("second"));
      buf.push(makeRecord("third"));
      const arr = buf.toArray();
      expect(arr.map((r) => r.msg)).toEqual(["first", "second", "third"]);
    });

    it("length reflects number of entries", () => {
      const buf = new RingBuffer(10);
      expect(buf.length).toBe(0);
      buf.push(makeRecord("a"));
      buf.push(makeRecord("b"));
      expect(buf.length).toBe(2);
    });
  });

  describe("capacity eviction", () => {
    it("evicts the oldest entry when capacity is exceeded", () => {
      const buf = new RingBuffer(3);
      buf.push(makeRecord("a"));
      buf.push(makeRecord("b"));
      buf.push(makeRecord("c"));
      buf.push(makeRecord("d")); // evicts "a"
      const arr = buf.toArray();
      expect(arr).toHaveLength(3);
      expect(arr.map((r) => r.msg)).toEqual(["b", "c", "d"]);
    });

    it("maintains length at capacity after overflow", () => {
      const buf = new RingBuffer(3);
      for (let i = 0; i < 10; i++) buf.push(makeRecord(`item-${i}`));
      expect(buf.length).toBe(3);
    });

    it("oldest-first order is correct after multiple wraps", () => {
      const buf = new RingBuffer(3);
      for (let i = 0; i < 7; i++) buf.push(makeRecord(`item-${i}`));
      const arr = buf.toArray();
      expect(arr.map((r) => r.msg)).toEqual(["item-4", "item-5", "item-6"]);
    });
  });

  describe("clear", () => {
    it("empties the buffer", () => {
      const buf = new RingBuffer(10);
      buf.push(makeRecord("x"));
      buf.push(makeRecord("y"));
      buf.clear();
      expect(buf.length).toBe(0);
      expect(buf.toArray()).toHaveLength(0);
    });

    it("allows pushing after clear", () => {
      const buf = new RingBuffer(10);
      buf.push(makeRecord("before"));
      buf.clear();
      buf.push(makeRecord("after"));
      expect(buf.toArray().map((r) => r.msg)).toEqual(["after"]);
    });
  });

  describe("subscribe", () => {
    it("callback fires for each pushed record", () => {
      const buf = new RingBuffer(10);
      const received: string[] = [];
      buf.subscribe((r) => received.push(r.msg));
      buf.push(makeRecord("one"));
      buf.push(makeRecord("two"));
      expect(received).toEqual(["one", "two"]);
    });

    it("returns an unsubscribe function that stops callbacks", () => {
      const buf = new RingBuffer(10);
      const received: string[] = [];
      const unsub = buf.subscribe((r) => received.push(r.msg));
      buf.push(makeRecord("before-unsub"));
      unsub();
      buf.push(makeRecord("after-unsub"));
      expect(received).toEqual(["before-unsub"]);
    });

    it("multiple subscribers each receive records", () => {
      const buf = new RingBuffer(10);
      const a: string[] = [];
      const b: string[] = [];
      buf.subscribe((r) => a.push(r.msg));
      buf.subscribe((r) => b.push(r.msg));
      buf.push(makeRecord("hello"));
      expect(a).toEqual(["hello"]);
      expect(b).toEqual(["hello"]);
    });

    it("listener errors do not crash push", () => {
      const buf = new RingBuffer(10);
      buf.subscribe(() => { throw new Error("listener error"); });
      expect(() => buf.push(makeRecord("safe"))).not.toThrow();
    });
  });
});

describe("getRingBuffer / resetRingBuffer", () => {
  it("getRingBuffer returns the same singleton", () => {
    resetRingBuffer();
    const a = getRingBuffer();
    const b = getRingBuffer();
    expect(a).toBe(b);
  });

  it("resetRingBuffer replaces the singleton", () => {
    resetRingBuffer();
    const before = getRingBuffer();
    before.push(makeRecord("old"));
    resetRingBuffer();
    const after = getRingBuffer();
    expect(after).not.toBe(before);
    expect(after.length).toBe(0);
  });
});

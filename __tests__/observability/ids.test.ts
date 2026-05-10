import { describe, it, expect } from "vitest";
import {
  randomTraceId,
  randomSpanId,
  w3cTraceparent,
  parseTraceparent,
} from "../../src/infra/observability/ids.js";

describe("randomTraceId", () => {
  it("returns a 32-char lowercase hex string", () => {
    const id = randomTraceId();
    expect(id).toHaveLength(32);
    expect(id).toMatch(/^[0-9a-f]{32}$/);
  });

  it("two calls produce distinct values", () => {
    expect(randomTraceId()).not.toBe(randomTraceId());
  });
});

describe("randomSpanId", () => {
  it("returns a 16-char lowercase hex string", () => {
    const id = randomSpanId();
    expect(id).toHaveLength(16);
    expect(id).toMatch(/^[0-9a-f]{16}$/);
  });

  it("two calls produce distinct values", () => {
    expect(randomSpanId()).not.toBe(randomSpanId());
  });
});

describe("w3cTraceparent", () => {
  it("formats as 00-<traceId>-<spanId>-01", () => {
    const traceId = randomTraceId();
    const spanId = randomSpanId();
    const header = w3cTraceparent(traceId, spanId);
    expect(header).toBe(`00-${traceId}-${spanId}-01`);
  });

  it("allows custom flags", () => {
    const traceId = randomTraceId();
    const spanId = randomSpanId();
    expect(w3cTraceparent(traceId, spanId, "00")).toBe(`00-${traceId}-${spanId}-00`);
  });
});

describe("parseTraceparent", () => {
  it("round-trips a generated traceparent", () => {
    const traceId = randomTraceId();
    const spanId = randomSpanId();
    const header = w3cTraceparent(traceId, spanId);
    const parsed = parseTraceparent(header);
    expect(parsed).not.toBeNull();
    expect(parsed!.traceId).toBe(traceId);
    expect(parsed!.spanId).toBe(spanId);
    expect(parsed!.flags).toBe("01");
  });

  it("returns null for malformed input", () => {
    expect(parseTraceparent("garbage")).toBeNull();
    expect(parseTraceparent("00-short-short-01")).toBeNull();
    expect(parseTraceparent("01-" + "a".repeat(32) + "-" + "b".repeat(16) + "-01")).toBeNull();
  });
});

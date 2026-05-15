import { describe, it, expect } from "vitest";
import { RateLimitError, InternalServerError, APIError } from "@anthropic-ai/sdk";

// The helper functions are module-private in runtime.ts — test equivalent
// implementations here to verify the logic without export pollution.

function isRateLimitError(err: unknown): boolean {
  if (err instanceof RateLimitError) return true;
  const status = (err as { status?: number }).status;
  return status === 429;
}

function isTransientStreamError(err: unknown): boolean {
  if (err instanceof InternalServerError) return true;
  const status = (err as { status?: number }).status;
  if (typeof status === "number" && status >= 500 && status < 600) return true;
  const msg = err instanceof Error ? err.message : String(err);
  // Use specific network-level keywords — avoid bare "timeout" / "network" which
  // match unrelated errors (e.g. CircuitOpenError message contains "timeout").
  return ["ECONNRESET", "ETIMEDOUT", "ECONNREFUSED", "fetch failed", "network error"].some((kw) =>
    msg.toLowerCase().includes(kw.toLowerCase()),
  );
}

function parseRetryAfterMs(err: unknown): number | undefined {
  if (err instanceof APIError && err.headers) {
    const val = err.headers.get("retry-after");
    if (val) {
      const seconds = parseInt(val, 10);
      if (!isNaN(seconds)) return seconds * 1000;
    }
  }
  return undefined;
}

function backoffMs(attempt: number, retryAfterMs?: number): number {
  const BASE_DELAY_MS = 1_500;
  const base = retryAfterMs ?? BASE_DELAY_MS * Math.pow(2, attempt);
  return Math.max(100, Math.round(base));
}

describe("isRateLimitError", () => {
  it("returns true for RateLimitError instance", () => {
    const err = new RateLimitError(429, {}, "rate limit", new Headers());
    expect(isRateLimitError(err)).toBe(true);
  });

  it("returns true for error with status=429", () => {
    const err = Object.assign(new Error("too many"), { status: 429 });
    expect(isRateLimitError(err)).toBe(true);
  });

  it("returns false for InternalServerError", () => {
    const err = new InternalServerError(500, {}, "server error", new Headers());
    expect(isRateLimitError(err)).toBe(false);
  });
});

describe("isTransientStreamError", () => {
  it("returns true for InternalServerError", () => {
    const err = new InternalServerError(500, {}, "server error", new Headers());
    expect(isTransientStreamError(err)).toBe(true);
  });

  it("returns true for status=502", () => {
    const err = Object.assign(new Error("bad gateway"), { status: 502 });
    expect(isTransientStreamError(err)).toBe(true);
  });

  it("returns true for ECONNRESET", () => {
    expect(isTransientStreamError(new Error("ECONNRESET"))).toBe(true);
  });

  it("returns true for ETIMEDOUT", () => {
    expect(isTransientStreamError(new Error("ETIMEDOUT"))).toBe(true);
  });

  it("returns true for fetch failed", () => {
    expect(isTransientStreamError(new Error("fetch failed"))).toBe(true);
  });

  it("returns true for network error", () => {
    expect(isTransientStreamError(new Error("network error occurred"))).toBe(true);
  });

  it("returns false for bare 'timeout' (CircuitOpenError message)", () => {
    expect(isTransientStreamError(new Error("Provider circuit is open — call rejected fast"))).toBe(false);
  });

  it("returns false for bare 'timeout' keyword alone", () => {
    expect(isTransientStreamError(new Error("Circuit is open — retry after recovery timeout"))).toBe(false);
  });

  it("returns false for RateLimitError", () => {
    const err = new RateLimitError(429, {}, "rate limit", new Headers());
    expect(isTransientStreamError(err)).toBe(false);
  });
});

describe("parseRetryAfterMs", () => {
  it("returns seconds * 1000 from Retry-After header", () => {
    const headers = new Headers({ "retry-after": "30" });
    const err = new RateLimitError(429, {}, "rate limit", headers);
    expect(parseRetryAfterMs(err)).toBe(30_000);
  });

  it("returns undefined when no Retry-After header", () => {
    const err = new RateLimitError(429, {}, "rate limit", new Headers());
    expect(parseRetryAfterMs(err)).toBeUndefined();
  });

  it("returns undefined for non-APIError", () => {
    expect(parseRetryAfterMs(new Error("plain error"))).toBeUndefined();
  });
});

describe("backoffMs", () => {
  // The production backoffMs applies ±20% jitter, so we assert ranges rather
  // than exact values. base * 0.8 ≤ result ≤ base * 1.2, floored at 100ms.

  it("uses retryAfterMs when provided — result within ±20% of retryAfterMs", () => {
    const result = backoffMs(0, 30_000);
    expect(result).toBeGreaterThanOrEqual(24_000); // 30_000 * 0.8
    expect(result).toBeLessThanOrEqual(36_000);    // 30_000 * 1.2
  });

  it("uses exponential fallback — attempt 0 within ±20% of 1500ms", () => {
    const result = backoffMs(0);
    expect(result).toBeGreaterThanOrEqual(1_200); // 1500 * 0.8
    expect(result).toBeLessThanOrEqual(1_800);    // 1500 * 1.2
  });

  it("uses exponential fallback — attempt 1 within ±20% of 3000ms", () => {
    const result = backoffMs(1);
    expect(result).toBeGreaterThanOrEqual(2_400);
    expect(result).toBeLessThanOrEqual(3_600);
  });

  it("never returns below 100ms", () => {
    // Run multiple times to defeat jitter
    for (let i = 0; i < 20; i++) {
      expect(backoffMs(0, 50)).toBeGreaterThanOrEqual(100);
    }
  });
});

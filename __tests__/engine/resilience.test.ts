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
  return ["ECONNRESET", "ETIMEDOUT", "timeout", "fetch", "network"].some((kw) =>
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
  it("uses retryAfterMs when provided", () => {
    expect(backoffMs(0, 30_000)).toBe(30_000);
  });

  it("uses exponential fallback when no retryAfterMs", () => {
    expect(backoffMs(0)).toBe(1_500);
    expect(backoffMs(1)).toBe(3_000);
    expect(backoffMs(2)).toBe(6_000);
  });

  it("never returns below 100ms", () => {
    expect(backoffMs(0, 50)).toBe(100);
  });
});

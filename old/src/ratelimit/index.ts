import { ConcurrencyGate } from "./concurrency-gate.js";
import { RateLimiter } from "./limiter.js";

export { RateLimiter } from "./limiter.js";
export { RateLimitedProvider } from "./provider-limiter.js";
export { ConcurrencyGate, ConcurrencyTimeoutError, CircuitOpenError } from "./concurrency-gate.js";
export type { RateLimitRule, RateLimitResult, RateLimitStats } from "./limiter.js";

/**
 * Shared semaphore — at most 2 provider calls in-flight across all subsystems.
 * maxConcurrent=2 allows one foreground + one background call simultaneously.
 * queueTimeoutMs=30_000 prevents stuck callers piling up indefinitely.
 */
export const concurrencyGate = new ConcurrencyGate({
  maxConcurrent: 2,
  queueTimeoutMs: 30_000,
});

/**
 * Shared count-based rate limiter — 100 calls/minute per provider name.
 * Secondary protection; the concurrencyGate does the primary in-flight control.
 */
export const providerRateLimiter = new RateLimiter([
  { name: "provider-minute", maxRequests: 100, windowMs: 60_000 },
]);

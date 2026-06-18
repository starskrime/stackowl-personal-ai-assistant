import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SessionStore } from "../../../src/gateway/adapters/telegram/session-store.js";

interface TestState { value: string; }

describe("SessionStore", () => {
  let store: SessionStore<TestState>;

  beforeEach(() => {
    vi.useFakeTimers();
    store = new SessionStore<TestState>({ ttlMs: 1000, cleanupIntervalMs: 500 });
  });

  afterEach(() => {
    store.destroy();
    vi.useRealTimers();
  });

  it("stores and retrieves a value", () => {
    store.set(1, { value: "hello" });
    expect(store.get(1)).toEqual({ value: "hello" });
  });

  it("returns undefined for missing key", () => {
    expect(store.get(999)).toBeUndefined();
  });

  it("touch-on-read: get() updates lastSeen", () => {
    store.set(1, { value: "a" });
    vi.advanceTimersByTime(800);
    store.get(1); // touch
    vi.advanceTimersByTime(800); // total: 1600ms since set, but 800ms since touch
    // should NOT be evicted because touch reset the clock
    expect(store.get(1)).toEqual({ value: "a" });
  });

  it("evicts entries older than TTL", () => {
    store.set(1, { value: "gone" });
    vi.advanceTimersByTime(1600); // past TTL + cleanup fires
    expect(store.get(1)).toBeUndefined();
  });

  it("has() returns correct presence", () => {
    store.set(2, { value: "x" });
    expect(store.has(2)).toBe(true);
    expect(store.has(99)).toBe(false);
  });

  it("delete() removes the entry", () => {
    store.set(3, { value: "y" });
    store.delete(3);
    expect(store.has(3)).toBe(false);
  });

  it("destroy() clears the cleanup interval", () => {
    const clearSpy = vi.spyOn(global, "clearInterval");
    store.destroy();
    expect(clearSpy).toHaveBeenCalled();
  });
});

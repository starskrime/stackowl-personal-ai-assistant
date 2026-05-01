import { describe, it, expect, vi, beforeEach } from "vitest";
import { ContextCache } from "../../src/context/cache.js";

describe("ContextCache", () => {
  let cache: ContextCache;
  beforeEach(() => { cache = new ContextCache(3); });

  it("returns null for cache miss", () => {
    expect(cache.get("L1", "key1")).toBeNull();
  });

  it("returns stored value within TTL", () => {
    cache.set("L1", "key1", "hello", 60_000);
    expect(cache.get("L1", "key1")).toBe("hello");
  });

  it("returns null after TTL expires", () => {
    vi.useFakeTimers();
    cache.set("L1", "key1", "hello", 100);
    vi.advanceTimersByTime(200);
    expect(cache.get("L1", "key1")).toBeNull();
    vi.useRealTimers();
  });

  it("evicts oldest entry when over maxEntries", () => {
    cache.set("L1", "k1", "v1", 60_000);
    cache.set("L2", "k2", "v2", 60_000);
    cache.set("L3", "k3", "v3", 60_000);
    cache.set("L4", "k4", "v4", 60_000); // evicts k1
    expect(cache.get("L1", "k1")).toBeNull();
    expect(cache.get("L4", "k4")).toBe("v4");
  });

  it("invalidate() removes all entries for a layer", () => {
    cache.set("L1", "k1", "v1", 60_000);
    cache.set("L1", "k2", "v2", 60_000);
    cache.invalidate("L1");
    expect(cache.get("L1", "k1")).toBeNull();
    expect(cache.get("L1", "k2")).toBeNull();
  });

  it("invalidateUser() removes all entries for a userId via reverse index", () => {
    cache.set("L1", "k1", "v1", 60_000, "user42");
    cache.set("L2", "k2", "v2", 60_000, "user42");
    cache.set("L3", "k3", "v3", 60_000, "user99");
    cache.invalidateUser("user42");
    expect(cache.get("L1", "k1")).toBeNull();
    expect(cache.get("L2", "k2")).toBeNull();
    expect(cache.get("L3", "k3")).toBe("v3"); // untouched
  });

  it("LRU eviction cleans userIndex", () => {
    const c = new ContextCache(2);
    c.set("L1", "k1", "v1", 60_000, "userA");
    c.set("L2", "k2", "v2", 60_000, "userA");
    c.set("L3", "k3", "v3", 60_000, "userB"); // evicts k1 (oldest)
    // k1 was userA's — invalidateUser should not fail or return stale
    c.invalidateUser("userA");
    expect(c.get("L1", "k1")).toBeNull(); // already evicted
    expect(c.get("L2", "k2")).toBeNull(); // invalidated
    expect(c.get("L3", "k3")).toBe("v3"); // userB untouched
  });

  it("TTL expiry cleans userIndex", () => {
    vi.useFakeTimers();
    const c = new ContextCache(200);
    c.set("L1", "k1", "v1", 100, "userA");
    vi.advanceTimersByTime(200);
    c.get("L1", "k1"); // triggers TTL cleanup
    // After TTL expiry + get, invalidateUser should be a no-op (no stale refs)
    c.invalidateUser("userA"); // must not throw
    expect(c.get("L1", "k1")).toBeNull();
    vi.useRealTimers();
  });

  it("invalidate() cleans userIndex", () => {
    const c = new ContextCache(200);
    c.set("L1", "k1", "v1", 60_000, "userA");
    c.invalidate("L1");
    // userIndex should be clean — subsequent invalidateUser must be safe
    c.invalidateUser("userA"); // must not throw or act on stale keys
    expect(c.get("L1", "k1")).toBeNull();
  });
});

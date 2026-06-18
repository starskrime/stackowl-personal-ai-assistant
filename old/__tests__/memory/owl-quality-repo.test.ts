// __tests__/memory/owl-quality-repo.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest"
import Database from "better-sqlite3"
import { OwlQualityRepo, OwlPinsRepo, applyV28Element17Migration } from "../../src/memory/db.js"

function makeDb() {
  const db = new Database(":memory:")
  applyV28Element17Migration(db)
  return db
}

describe("OwlQualityRepo", () => {
  let db: Database.Database
  let repo: OwlQualityRepo

  beforeEach(() => { db = makeDb(); repo = new OwlQualityRepo(db) })
  afterEach(() => db.close())

  it("returns null for unknown owl", () => {
    expect(repo.get("aria", "user1")).toBeNull()
  })

  it("starts at 0.7 default after first update", () => {
    repo.update("aria", "user1", 1.0)
    const r = repo.get("aria", "user1")!
    // 0.15 * 1.0 + 0.85 * 0.7 = 0.745
    expect(r.ewmaReward).toBeCloseTo(0.745, 3)
    expect(r.turnCount).toBe(1)
  })

  it("ewma converges toward reward over many updates", () => {
    for (let i = 0; i < 20; i++) repo.update("aria", "user1", 1.0)
    const r = repo.get("aria", "user1")!
    expect(r.ewmaReward).toBeGreaterThan(0.95)
  })

  it("clamps reward to 0-1 before EWMA", () => {
    repo.update("aria", "user1", 999)
    const r = repo.get("aria", "user1")!
    expect(r.ewmaReward).toBeLessThanOrEqual(1.0)
  })

  it("isolates by ownerId", () => {
    repo.update("aria", "user1", 1.0)
    expect(repo.get("aria", "user2")).toBeNull()
  })
})

describe("OwlPinsRepo", () => {
  let db: Database.Database
  let repo: OwlPinsRepo

  beforeEach(() => { db = makeDb(); repo = new OwlPinsRepo(db) })
  afterEach(() => db.close())

  it("returns null when no pin set", () => {
    expect(repo.get("user1", "telegram")).toBeNull()
  })

  it("returns channel-specific pin", () => {
    repo.set("user1", "telegram", "aria", new Date().toISOString())
    expect(repo.get("user1", "telegram")).toBe("aria")
  })

  it("telegram pin does not bleed to CLI", () => {
    repo.set("user1", "telegram", "aria", new Date().toISOString())
    expect(repo.get("user1", "cli")).toBeNull()
  })

  it("falls back to global pin when no channel pin", () => {
    repo.set("user1", "global", "nora", new Date().toISOString())
    expect(repo.get("user1", "telegram")).toBe("nora")
  })

  it("channel pin overrides global pin", () => {
    repo.set("user1", "global", "nora", new Date().toISOString())
    repo.set("user1", "telegram", "aria", new Date().toISOString())
    expect(repo.get("user1", "telegram")).toBe("aria")
  })

  it("clears pin when set to null", () => {
    repo.set("user1", "telegram", "aria", new Date().toISOString())
    repo.set("user1", "telegram", null, new Date().toISOString())
    expect(repo.get("user1", "telegram")).toBeNull()
  })
})

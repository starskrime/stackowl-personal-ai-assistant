import { describe, it, expect } from "vitest"
import { shuffleArray } from "../../src/gateway/core.js"

describe("Parliament shuffled selection", () => {
  it("shuffleArray returns all original elements", () => {
    const arr = [1, 2, 3, 4, 5]
    const result = shuffleArray([...arr])
    expect(result.sort()).toEqual(arr.sort())
  })

  it("shuffleArray produces varied orderings across 20 calls", () => {
    const arr = ["a", "b", "c", "d", "e"]
    const orderings = new Set<string>()
    for (let i = 0; i < 20; i++) {
      orderings.add(shuffleArray([...arr]).join(","))
    }
    expect(orderings.size).toBeGreaterThan(1)
  })

  it("shuffleArray handles empty array", () => {
    expect(shuffleArray([])).toEqual([])
  })

  it("shuffleArray handles single element", () => {
    expect(shuffleArray(["only"])).toEqual(["only"])
  })
})

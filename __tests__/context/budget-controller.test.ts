import { describe, it, expect } from "vitest";
import { BudgetController, estimateTokens } from "../../src/context/budget-controller.js";

describe("estimateTokens", () => {
  it("estimates based on char/3.8 ratio", () => {
    expect(estimateTokens("hello")).toBe(Math.ceil(5 / 3.8));
  });
});

describe("BudgetController", () => {
  it("allows output within layer cap", () => {
    const b = new BudgetController(1000);
    const out = b.apply("L1", "hello world", 50);
    expect(out).toBe("hello world");
    expect(b.consumed).toBeGreaterThan(0);
  });

  it("trims output exceeding layer maxTokens", () => {
    const b = new BudgetController(10000);
    const long = "word ".repeat(200); // ~1000 tokens
    const out = b.apply("L1", long, 10);
    expect(estimateTokens(out)).toBeLessThanOrEqual(11); // slight margin
    expect(out).toContain("…[trimmed]");
  });

  it("returns empty string when global ceiling exhausted", () => {
    const b = new BudgetController(5);
    b.apply("L1", "hello world entire long string", 100);
    const out = b.apply("L2", "anything", 100);
    expect(out).toBe("");
  });

  it("reset() clears consumed counter", () => {
    const b = new BudgetController(1000);
    b.apply("L1", "hello world", 50);
    expect(b.consumed).toBeGreaterThan(0);
    b.reset();
    expect(b.consumed).toBe(0);
  });

  it("trims at sentence boundary when possible", () => {
    const b = new BudgetController(10000);
    const text = "First sentence. Second sentence. Third sentence.";
    const out = b.apply("L1", text, 4); // ~4 tokens = ~15 chars
    expect(out.endsWith(".") || out.endsWith("…[trimmed]")).toBe(true);
  });
});

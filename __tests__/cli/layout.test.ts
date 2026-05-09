import { describe, it, expect } from "vitest";
import { computeLayout } from "../../src/cli/layout.js";

describe("computeLayout", () => {
  it("respects minimum cols/rows", () => {
    const layout = computeLayout(40, 10);
    expect(layout.cols).toBe(80);
    expect(layout.rows).toBe(20);
  });

  it("computes leftW and rightW that sum to cols - 4", () => {
    const layout = computeLayout(120, 40);
    expect(layout.leftW + layout.rightW).toBe(layout.cols - 4);
  });

  it("leftW is at least 32", () => {
    const layout = computeLayout(80, 24);
    expect(layout.leftW).toBeGreaterThanOrEqual(32);
  });
});

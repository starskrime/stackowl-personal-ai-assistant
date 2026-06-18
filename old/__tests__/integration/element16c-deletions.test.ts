import { describe, it, expect } from "vitest";
import { existsSync } from "node:fs";

describe("Element 16c deletions", () => {
  it("src/tools/web-unified.ts is deleted", () => {
    expect(existsSync("src/tools/web-unified.ts")).toBe(false);
  });
  it("__tests__/tools/web-unified.test.ts is deleted", () => {
    expect(existsSync("__tests__/tools/web-unified.test.ts")).toBe(false);
  });
  it("src/compat/tools/web-search.ts (Brave) is deleted", () => {
    expect(existsSync("src/compat/tools/web-search.ts")).toBe(false);
  });
});

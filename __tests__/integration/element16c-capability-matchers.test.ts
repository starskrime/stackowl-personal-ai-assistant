import { describe, it, expect } from "vitest";
import { execSync } from "node:child_process";

describe("Element 16c capability matchers", () => {
  it("no capability-matcher source file references the deleted tool literals", () => {
    // Exclude web-scrapling.ts (scrapling_fetch is its own definition name, not a matcher)
    const out = execSync(
      `grep -rn '"web_crawl"\\|"duckduckgo_search"\\|"scrapling_fetch"' ` +
      `src/ --include='*.ts' ` +
      `--exclude='web-scrapling.ts' || true`,
      { encoding: "utf-8" },
    );
    expect(out.trim()).toBe("");
  });
});

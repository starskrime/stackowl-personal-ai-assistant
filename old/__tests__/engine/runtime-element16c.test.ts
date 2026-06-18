import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

describe("runtime.ts — Element 16c renames", () => {
  const src = readFileSync("src/engine/runtime.ts", "utf-8");

  it("SEQUENTIAL_USE_TOOLS contains web_fetch, not web_crawl", () => {
    expect(src).toMatch(/SEQUENTIAL_USE_TOOLS\s*=\s*new Set\(\[.*"web_fetch"/s);
    expect(src).not.toMatch(/SEQUENTIAL_USE_TOOLS\s*=\s*new Set\(\[.*"web_crawl"/s);
  });

  it("TOOL_FALLBACKS contains web_fetch entry", () => {
    expect(src).toMatch(/web_fetch:\s*\["web_search", "live_browser"\]/);
    expect(src).not.toMatch(/web_crawl:\s*\[/);
  });

  it("Anti-Bot Override prose references live_browser, not camofox", () => {
    const idx = src.indexOf("Anti-Bot Override");
    expect(idx).toBeGreaterThan(0);
    const window = src.slice(idx, idx + 2000);
    expect(window).toMatch(/live_browser/);
    expect(window).not.toMatch(/`?camofox`?\s+for\s+login/i);
  });
});

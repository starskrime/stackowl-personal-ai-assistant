import { describe, it, expect } from "vitest";
import { rewriteText, type RewriteRule } from "../../scripts/scrub-deprecated-tool-refs.js";

describe("scrub-deprecated-tool-refs", () => {
  const rules: RewriteRule[] = [
    { from: /\bweb_crawl\b/g, to: "web_fetch" },
    { from: /\bduckduckgo_search\b/g, to: "web_search" },
  ];

  it("rewrites web_crawl → web_fetch", () => {
    expect(rewriteText("call web_crawl on it", rules)).toBe("call web_fetch on it");
  });

  it("rewrites duckduckgo_search → web_search", () => {
    expect(rewriteText("use duckduckgo_search first", rules)).toBe("use web_search first");
  });

  it("preserves URLs (no false positive on web_crawl as substring)", () => {
    expect(rewriteText("https://example.com/web_crawler", rules)).toBe("https://example.com/web_crawler");
  });
});

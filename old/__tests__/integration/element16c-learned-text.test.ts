import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

const sites = [
  "src/tools/pellet-recall.ts",
  "src/tools/files.ts",
  "src/memory/attempt-log.ts",
];

describe("Element 16c learned-text references", () => {
  for (const path of sites) {
    it(`${path} no longer mentions the deprecated tool names`, () => {
      const text = readFileSync(path, "utf-8");
      expect(text).not.toMatch(/\bweb_crawl\b/);
      expect(text).not.toMatch(/\bduckduckgo_search\b/);
    });
  }
});

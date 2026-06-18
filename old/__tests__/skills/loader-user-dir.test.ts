/**
 * Test for SkillsLoader.userSkillsDir()
 */

import { describe, it, expect } from "vitest";
import { SkillsLoader } from "../../src/skills/loader.js";

describe("SkillsLoader", () => {
  describe("userSkillsDir()", () => {
    it("should return a path containing .stackowl/skills", () => {
      const dir = SkillsLoader.userSkillsDir();
      expect(dir).toContain(".stackowl");
      expect(dir).toContain("skills");
      expect(dir).toMatch(/\.stackowl[/\\]skills$/);
    });

    it("should return a non-empty string", () => {
      const dir = SkillsLoader.userSkillsDir();
      expect(typeof dir).toBe("string");
      expect(dir.length).toBeGreaterThan(0);
    });

    it("should be an absolute path", () => {
      const dir = SkillsLoader.userSkillsDir();
      // On Windows, absolute paths start with a drive letter or \\
      // On Unix-like systems, they start with /
      const isAbsolute = dir.startsWith("/") || /^[A-Z]:/i.test(dir);
      expect(isAbsolute).toBe(true);
    });

    it("should return consistent results", () => {
      const dir1 = SkillsLoader.userSkillsDir();
      const dir2 = SkillsLoader.userSkillsDir();
      expect(dir1).toBe(dir2);
    });
  });
});

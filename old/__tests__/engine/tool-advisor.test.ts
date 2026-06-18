import { describe, it, expect } from "vitest";
import { ToolAdvisor } from "../../src/engine/tool-advisor.js";

describe("ToolAdvisor", () => {
  const advisor = new ToolAdvisor();

  describe("getThreshold", () => {
    it("returns 3 for web_search", () => {
      expect(advisor.getThreshold("web_search")).toBe(3);
    });

    it("returns 20 for live_browser", () => {
      expect(advisor.getThreshold("live_browser")).toBe(20);
    });

    it("returns 6 (default) for unknown_tool", () => {
      expect(advisor.getThreshold("unknown_tool")).toBe(6);
    });
  });

  describe("buildAdvisoryMessage", () => {
    it("includes [TOOL ADVISOR: web_search], the repeat count, and at least one alternative", () => {
      const msg = advisor.buildAdvisoryMessage("web_search", 4, "find zimaboard price");
      expect(msg).toContain("[TOOL ADVISOR: web_search]");
      expect(msg).toContain("4");
      // Should have at least one alternative listed
      expect(msg).toMatch(/- .+/);
    });

    it("includes fallbacks passed by caller that are not already in TOOL_ALTERNATIVES", () => {
      const msg = advisor.buildAdvisoryMessage("web_search", 4, "find zimaboard price", ["live_browser"]);
      // live_browser already exists in TOOL_ALTERNATIVES for web_search, but the message
      // should still mention alternatives
      expect(msg).toContain("[TOOL ADVISOR: web_search]");
      expect(msg).toMatch(/live_browser/);
    });

    it("includes extra fallbacks that are not already listed in TOOL_ALTERNATIVES", () => {
      const msg = advisor.buildAdvisoryMessage("web_search", 4, "find something", ["some_unique_tool"]);
      expect(msg).toContain("some_unique_tool");
    });

    it("returns 'different tool or approach' for unknown_tool with no alternatives", () => {
      const msg = advisor.buildAdvisoryMessage("unknown_tool", 7, "do something");
      expect(msg).toContain("[TOOL ADVISOR: unknown_tool]");
      expect(msg).toContain("different tool or approach");
    });

    it("does not call unknown_tool again — message ends with Do NOT call instruction", () => {
      const msg = advisor.buildAdvisoryMessage("unknown_tool", 7, "do something");
      expect(msg).toContain("Do NOT call `unknown_tool` again");
    });

    it("truncates long userIntent to 150 characters in the message", () => {
      const longIntent = "a".repeat(300);
      const msg = advisor.buildAdvisoryMessage("web_search", 4, longIntent);
      // The snippet is sliced to 150 chars
      const snippet = "a".repeat(150);
      expect(msg).toContain(snippet);
      // The full 300-char string should NOT appear in the message
      expect(msg).not.toContain("a".repeat(151) + "a");
    });
  });
});

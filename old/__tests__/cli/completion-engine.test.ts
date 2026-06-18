import { describe, it, expect } from "vitest";
import { CompletionEngine } from "../../src/cli/completion-engine.js";
import type { CompletionProvider } from "../../src/cli/completion-engine.js";

function makeProvider(): CompletionProvider {
  return {
    topLevelNames: () => ["help", "status", "skills", "specialization", "clear"],
    subcommands: (cmd: string) => {
      const map: Record<string, string[]> = {
        skills: ["list", "install"],
        specialization: ["list", "show", "create", "delete", "update"],
      };
      return map[cmd] ?? [];
    },
  };
}

describe("CompletionEngine", () => {
  describe("command mode", () => {
    it("returns all commands when buf is /", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/");
      expect(result.mode).toBe("command");
      expect(result.items).toEqual(["help", "status", "skills", "specialization", "clear"]);
    });

    it("prefix-filters top-level names", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/s");
      expect(result.mode).toBe("command");
      expect(result.items).toEqual(["status", "skills", "specialization"]);
    });

    it("returns empty items when no top-level match", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/xyz");
      expect(result.mode).toBe("command");
      expect(result.items).toEqual([]);
    });

    it("returns empty when buf does not start with /", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("hello");
      expect(result.items).toEqual([]);
      expect(result.mode).toBe("command");
    });

    it("returns empty for empty buf", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("");
      expect(result.items).toEqual([]);
    });

    it("is case-insensitive", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/S");
      expect(result.items).toEqual(["status", "skills", "specialization"]);
    });
  });

  describe("subcommand mode", () => {
    it("returns all subcommands after command + space", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/skills ");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual(["list", "install"]);
    });

    it("prefix-filters subcommands", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/specialization s");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual(["show"]);
    });

    it("returns all specialization subcommands", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/specialization ");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual(["list", "show", "create", "delete", "update"]);
    });

    it("returns empty for unknown command after space", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/unknown ");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual([]);
    });

    it("returns empty when subcommand partial has no match", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/skills xyz");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual([]);
    });
  });
});

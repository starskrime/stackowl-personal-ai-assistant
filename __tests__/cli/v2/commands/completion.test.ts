import { describe, it, expect } from "vitest";
import { getCompletions } from "../../../../src/cli/v2/commands/completion.js";
import type { CommandContext } from "../../../../src/cli/v2/commands/registry.js";

const ctx = {} as CommandContext;

describe("getCompletions", () => {
  it("returns all commands for '/'", async () => {
    const results = await getCompletions("/", ctx);
    expect(results.length).toBeGreaterThanOrEqual(7);
    expect(results.every((r) => r.kind === "command")).toBe(true);
  });

  it("filters by prefix '/me'", async () => {
    const results = await getCompletions("/me", ctx);
    const names = results.map((r) => r.value);
    expect(names).toContain("/memory");
    expect(names.every((n) => n.startsWith("/me"))).toBe(true);
  });

  it("returns subcommands for '/memory '", async () => {
    const results = await getCompletions("/memory ", ctx);
    expect(results.every((r) => r.kind === "subcommand")).toBe(true);
    const names = results.map((r) => r.value);
    expect(names).toContain("list");
    expect(names).toContain("search");
  });

  it("filters subcommands by prefix '/memory li'", async () => {
    const results = await getCompletions("/memory li", ctx);
    expect(results.map((r) => r.value)).toContain("list");
  });

  it("returns empty array for '/unknown'", async () => {
    const results = await getCompletions("/unknown", ctx);
    expect(results).toHaveLength(0);
  });

  it("returns empty for plain text (no slash)", async () => {
    const results = await getCompletions("hello", ctx);
    expect(results).toHaveLength(0);
  });
});

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

describe("getCompletions — /owl specific cases", () => {
  // ── Mode 1: command name completion ────────────────────────────────────────

  it("'/owl' exact match shows subcommands immediately (no space needed)", async () => {
    // Exact command match with subcommands → show subcommands, not the command itself.
    // This lets the user see options without having to add a trailing space.
    const results = await getCompletions("/owl", ctx);
    expect(results.every((r) => r.kind === "subcommand")).toBe(true);
    expect(results.map((r) => r.value)).toContain("list");
  });

  // ── Mode 2a: trailing space — list all subcommands ─────────────────────────

  it("'/owl ' returns all subcommands", async () => {
    const results = await getCompletions("/owl ", ctx);
    expect(results.every((r) => r.kind === "subcommand")).toBe(true);
    const names = results.map((r) => r.value);
    expect(names).toContain("list");
    expect(names).toContain("show");
    expect(names).toContain("status");
    expect(names).toContain("create");
    expect(names).toContain("from-bmad");
    expect(names).toContain("delete");
    expect(names).toContain("pin");
    expect(names).toContain("unpin");
  });

  // ── Mode 2b: partial subcommand — filters results ──────────────────────────

  it("'/owl cr' returns only 'create' subcommand", async () => {
    const results = await getCompletions("/owl cr", ctx);
    expect(results).toHaveLength(1);
    expect(results[0]!.value).toBe("create");
  });

  it("'/owl li' returns only 'list' subcommand", async () => {
    const results = await getCompletions("/owl li", ctx);
    expect(results).toHaveLength(1);
    expect(results[0]!.value).toBe("list");
  });

  // ── Bug 2 root cause documentation ─────────────────────────────────────────
  // When user types '/owl create' (exact subcommand, no trailing space),
  // getCompletions returns a non-empty list with value="create".
  // In Composer: showPopup = completions.length > 0 && value !== completions[0].value
  //            = true && "/owl create" !== "create"
  //            = true
  // Consequence: Enter is intercepted by popup handler, re-fills value with
  // "/owl create " (trailing space) — the command is NEVER dispatched.
  // Fix: in Composer Enter handler, detect exact subcommand match and allow dispatch.

  it("'/owl create' (exact, no trailing space) returns non-empty completions — triggers Composer popup interception", async () => {
    const value = "/owl create";
    const results = await getCompletions(value, ctx);
    // Non-empty → showPopup = true → Enter blocked in current Composer
    expect(results).toHaveLength(1);
    expect(results[0]!.kind).toBe("subcommand");
    expect(results[0]!.value).toBe("create");
    // The fix must ensure that when lastTypedWord === completion.value, Enter dispatches
    const lastTypedWord = value.trim().split(/\s+/).pop();
    expect(lastTypedWord).toBe(results[0]!.value); // "create" === "create" → should dispatch
  });

  it("'/owl list' exact match also triggers popup interception", async () => {
    const value = "/owl list";
    const results = await getCompletions(value, ctx);
    expect(results).toHaveLength(1);
    expect(results[0]!.value).toBe("list");
    const lastTypedWord = value.trim().split(/\s+/).pop();
    expect(lastTypedWord).toBe(results[0]!.value);
  });

  it("'/owl show' exact match triggers popup interception", async () => {
    const value = "/owl show";
    const results = await getCompletions(value, ctx);
    expect(results).toHaveLength(1);
    expect(results[0]!.value).toBe("show");
  });

  // ── Mode 3: arg completion (none defined for /owl subcommands) ─────────────

  it("'/owl create ' (trailing space) returns empty (no arg completers defined)", async () => {
    const results = await getCompletions("/owl create ", ctx);
    expect(results).toHaveLength(0);
  });
});

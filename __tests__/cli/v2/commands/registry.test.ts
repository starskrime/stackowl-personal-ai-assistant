import { describe, it, expect, vi } from "vitest";
import { REGISTRY, resolveCommand, type CommandContext } from "../../../../src/cli/v2/commands/registry.js";

const ctx = {} as CommandContext;

describe("REGISTRY", () => {
  it("has at least 7 commands", () => {
    expect(REGISTRY.length).toBeGreaterThanOrEqual(7);
  });

  it("resolves /quit by name", () => {
    const result = resolveCommand("/quit");
    expect(result).not.toBeNull();
    expect(result!.spec.name).toBe("/quit");
  });

  it("resolves /exit as alias for /quit", () => {
    const result = resolveCommand("/exit");
    expect(result).not.toBeNull();
    expect(result!.spec.name).toBe("/quit");
  });

  it("resolves /memory subcommand list", () => {
    const result = resolveCommand("/memory list");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("list");
  });

  it("returns null for unknown command", () => {
    expect(resolveCommand("/nonexistent")).toBeNull();
  });
});

describe("REGISTRY — /owl command resolution", () => {
  it("bare /owl resolves to spec with no subcommand", () => {
    const result = resolveCommand("/owl");
    expect(result).not.toBeNull();
    expect(result!.spec.name).toBe("/owl");
    expect(result!.subcommand).toBeUndefined();
    expect(result!.args).toHaveLength(0);
  });

  it("/owl list resolves to list subcommand", () => {
    const result = resolveCommand("/owl list");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("list");
    expect(result!.args).toHaveLength(0);
  });

  it("/owl show <name> resolves to show subcommand with arg", () => {
    const result = resolveCommand("/owl show alice");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("show");
    expect(result!.args).toEqual(["alice"]);
  });

  it("/owl status resolves to status subcommand", () => {
    const result = resolveCommand("/owl status");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("status");
  });

  it("/owl create resolves to create subcommand", () => {
    const result = resolveCommand("/owl create");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("create");
    expect(result!.args).toHaveLength(0);
  });

  it("/owl from-bmad resolves to from-bmad subcommand", () => {
    const result = resolveCommand("/owl from-bmad");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("from-bmad");
  });

  it("/owl from-bmad <name> passes name as arg", () => {
    const result = resolveCommand("/owl from-bmad alice");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("from-bmad");
    expect(result!.args).toEqual(["alice"]);
  });

  it("/owl delete <name> resolves to delete subcommand", () => {
    const result = resolveCommand("/owl delete bob");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("delete");
    expect(result!.args).toEqual(["bob"]);
  });

  it("/owl pin <name> resolves to pin subcommand", () => {
    const result = resolveCommand("/owl pin alice");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("pin");
    expect(result!.args).toEqual(["alice"]);
  });

  it("/owl unpin resolves to unpin subcommand", () => {
    const result = resolveCommand("/owl unpin");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("unpin");
    expect(result!.args).toHaveLength(0);
  });

  it("/owl has 9 subcommands (list show status create from-bmad delete pin unpin switch)", () => {
    const result = resolveCommand("/owl");
    const subs = result!.spec.subcommands ?? [];
    const names = subs.map((s) => s.name);
    expect(names).toContain("list");
    expect(names).toContain("show");
    expect(names).toContain("status");
    expect(names).toContain("create");
    expect(names).toContain("from-bmad");
    expect(names).toContain("delete");
    expect(names).toContain("pin");
    expect(names).toContain("unpin");
    expect(names).toContain("switch");
    expect(subs).toHaveLength(9);
  });

  it("resolves /owl switch as subcommand with name arg", () => {
    const result = resolveCommand("/owl switch Aria");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("switch");
    expect(result!.args).toEqual(["Aria"]);
  });
});

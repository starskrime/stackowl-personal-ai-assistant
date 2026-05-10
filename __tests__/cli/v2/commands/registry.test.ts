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

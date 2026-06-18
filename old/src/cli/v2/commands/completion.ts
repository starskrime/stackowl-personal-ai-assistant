import { REGISTRY } from "./registry.js";
import type { CommandContext } from "./registry.js";

export type CompletionKind = "command" | "subcommand" | "arg";

export interface CompletionEntry {
  kind: CompletionKind;
  value: string;
  description?: string;
}

export async function getCompletions(input: string, ctx: CommandContext): Promise<CompletionEntry[]> {
  if (!input.startsWith("/")) return [];

  const hasTrailingSpace = input.endsWith(" ");
  // Split and filter empty strings caused by trailing spaces
  const parts = input.trim().split(/\s+/).filter((p) => p.length > 0);
  const cmdPart = parts[0] ?? "";

  // Find the command spec (used by modes 1 exact-match and 2a/2b)
  const spec = REGISTRY.find(
    (s) => s.name === cmdPart || (s.aliases ?? []).includes(cmdPart),
  );

  // Mode 1: completing command name (only command word present, no trailing space)
  if (parts.length === 1 && !hasTrailingSpace) {
    // Exact match on a command that has subcommands → show subcommands immediately
    // (no need to add a space first)
    if (spec?.subcommands && (spec.name === cmdPart || (spec.aliases ?? []).includes(cmdPart))) {
      return spec.subcommands.map((sub) => ({
        kind: "subcommand" as const,
        value: sub.name,
        description: sub.description,
      }));
    }
    return REGISTRY.flatMap((s) => {
      const names = [s.name, ...(s.aliases ?? [])];
      return names
        .filter((n) => n.startsWith(cmdPart))
        .map((n) => ({ kind: "command" as const, value: n, description: s.description }));
    });
  }

  if (!spec || !spec.subcommands) return [];

  // Mode 2a: command typed with trailing space — list all subcommands
  if (parts.length === 1 && hasTrailingSpace) {
    return spec.subcommands.map((sub) => ({
      kind: "subcommand" as const,
      value: sub.name,
      description: sub.description,
    }));
  }

  // Mode 2b: partial subcommand name being typed
  if (parts.length === 2 && !hasTrailingSpace) {
    const partial = parts[1] ?? "";
    return spec.subcommands
      .filter((sub) => sub.name.startsWith(partial))
      .map((sub) => ({ kind: "subcommand" as const, value: sub.name, description: sub.description }));
  }

  // Mode 3: dynamic arg completion or static verb list
  if (parts.length >= 2) {
    const subcmdName = parts[1] ?? "";
    const sub = spec.subcommands.find((s) => s.name === subcmdName);
    if (sub) {
      // Dynamic completer (e.g. server names, memory keys) — takes priority
      if (sub.complete && (parts.length > 2 || hasTrailingSpace)) {
        const partial = hasTrailingSpace ? "" : (parts[parts.length - 1] ?? "");
        const values = await sub.complete(ctx, partial);
        return values.map((v) => ({ kind: "arg" as const, value: v }));
      }
      // Static verb list (e.g. /config provider list|add|remove|...)
      if (sub.verbs?.length) {
        if (parts.length === 2 && hasTrailingSpace) {
          return sub.verbs.map((v) => ({ kind: "arg" as const, value: v }));
        }
        if (parts.length === 3 && !hasTrailingSpace) {
          const partial3 = parts[2] ?? "";
          return sub.verbs
            .filter((v) => v.startsWith(partial3))
            .map((v) => ({ kind: "arg" as const, value: v }));
        }
      }
    }
  }

  return [];
}

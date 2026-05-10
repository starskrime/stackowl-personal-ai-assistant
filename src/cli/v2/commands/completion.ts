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

  // Mode 1: completing command name (only command word present, no trailing space)
  if (parts.length === 1 && !hasTrailingSpace) {
    return REGISTRY.flatMap((spec) => {
      const names = [spec.name, ...(spec.aliases ?? [])];
      return names
        .filter((n) => n.startsWith(cmdPart))
        .map((n) => ({ kind: "command" as const, value: n, description: spec.description }));
    });
  }

  // Find the command spec
  const spec = REGISTRY.find(
    (s) => s.name === cmdPart || (s.aliases ?? []).includes(cmdPart),
  );
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

  // Mode 3: dynamic arg completion
  if (parts.length >= 2) {
    const subcmdName = parts[1] ?? "";
    const sub = spec.subcommands.find((s) => s.name === subcmdName);
    if (sub?.complete && (parts.length > 2 || hasTrailingSpace)) {
      const partial = hasTrailingSpace ? "" : (parts[parts.length - 1] ?? "");
      const values = await sub.complete(ctx, partial);
      return values.map((v) => ({ kind: "arg" as const, value: v }));
    }
  }

  return [];
}

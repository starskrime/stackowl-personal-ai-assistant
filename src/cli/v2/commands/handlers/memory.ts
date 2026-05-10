import type { CommandHandler, CommandContext } from "../registry.js";
import { dispatchMemoryCommand } from "../../../../gateway/commands/memory-router.js";

function getDeps(ctx: CommandContext) {
  return { repo: ctx.getMemoryRepo() };
}

// Dynamic completer for /memory get, /memory invalidate, /memory history
export async function completeMemoryKeys(ctx: CommandContext, partial: string): Promise<string[]> {
  const deps = getDeps(ctx);
  const records = await deps.repo.search("", { topK: 50 });
  return records.map((r: { id: string }) => r.id).filter((id: string) => id.startsWith(partial));
}

function textToItems(text: string): Array<{ id: string; label: string }> {
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line, i) => ({ id: `line-${i}`, label: line }));
}

export const handleMemoryList: CommandHandler = async (ctx, _args) => {
  const deps = getDeps(ctx);
  const text = await dispatchMemoryCommand("list", [], deps);
  const lines = text.split("\n").filter((l) => l.trim());

  const headerLine = lines[0] ?? "";
  const itemLines = lines.slice(1);

  const items = itemLines.map((line, i) => {
    const match = line.match(/\[(\w+)\]\s+(\S+)\s+—\s+(.*)/);
    return match
      ? {
          id: `mem-${i}`,
          label: match[2]!,
          meta: `[${match[1]}] ${match[3]!.slice(0, 50)}`,
          data: { rawId: match[2] },
        }
      : { id: `mem-${i}`, label: line.trim() };
  });

  return {
    kind: "panel",
    payload: {
      title: `/memory list — ${headerLine.trim()}`,
      items,
      emptyText: "No memories stored yet.",
    },
  };
};

export const handleMemorySearch: CommandHandler = async (ctx, args) => {
  const deps = getDeps(ctx);
  const query = args.join(" ");
  if (!query) return { kind: "error", text: "Usage: /memory search <query>" };
  const text = await dispatchMemoryCommand("search", args, deps);
  return {
    kind: "panel",
    payload: { title: `/memory search "${query}"`, items: textToItems(text) },
  };
};

export const handleMemoryGet: CommandHandler = async (ctx, args) => {
  const deps = getDeps(ctx);
  const id = args[0];
  if (!id) return { kind: "error", text: "Usage: /memory get <key>" };
  const text = await dispatchMemoryCommand("get", args, deps);
  return {
    kind: "panel",
    payload: { title: `/memory get ${id}`, items: textToItems(text) },
  };
};

export const handleMemoryInvalidate: CommandHandler = async (ctx, args) => {
  const deps = getDeps(ctx);
  const id = args[0];
  if (!id) return { kind: "error", text: "Usage: /memory invalidate <key>" };
  const text = await dispatchMemoryCommand("invalidate", args, deps);
  return { kind: "system-message", text };
};

export const handleMemoryStats: CommandHandler = async (ctx, _args) => {
  const deps = getDeps(ctx);
  const text = await dispatchMemoryCommand("stats", [], deps);
  return {
    kind: "panel",
    payload: { title: "/memory stats", items: textToItems(text) },
  };
};

export const handleMemoryHistory: CommandHandler = async (ctx, args) => {
  const deps = getDeps(ctx);
  const id = args[0];
  if (!id) return { kind: "error", text: "Usage: /memory history <id>" };
  const text = await dispatchMemoryCommand("history", args, deps);
  return {
    kind: "panel",
    payload: { title: `/memory history ${id}`, items: textToItems(text) },
  };
};

export const handleMemoryExport: CommandHandler = async (ctx, _args) => {
  const deps = getDeps(ctx);
  const text = await dispatchMemoryCommand("export", [], deps);
  return {
    kind: "panel",
    payload: { title: "/memory export", items: textToItems(text) },
  };
};

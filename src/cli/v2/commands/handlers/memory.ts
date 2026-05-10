import type { CommandHandler, CommandContext } from "../registry.js";
import { dispatchMemoryCommand } from "../../../../gateway/commands/memory-router.js";
import { globalBridge } from "../../events/bridge.js";

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

  const actions = [
    {
      key: "g",
      label: "get",
      handler: async (item: { id: string; label: string; meta?: string; data?: unknown }) => {
        const memId = (item.data as { rawId: string } | undefined)?.rawId ?? item.label;
        const getText = await dispatchMemoryCommand("get", [memId], getDeps(ctx));
        globalBridge.openPanel("memory-detail", {
          title: `/memory get ${memId}`,
          items: textToItems(getText),
        });
      },
    },
    {
      key: "d",
      label: "invalidate",
      confirm: "Type 'yes' to confirm deletion",
      handler: async (item: { id: string; label: string; meta?: string; data?: unknown }) => {
        const memId = (item.data as { rawId: string } | undefined)?.rawId ?? item.label;
        await dispatchMemoryCommand("invalidate", [memId], getDeps(ctx));
        // Refresh the list panel after deletion
        const refreshed = await handleMemoryList(ctx, []);
        if (refreshed.kind === "panel") {
          globalBridge.openPanel("list", refreshed.payload);
        }
      },
    },
  ];

  return {
    kind: "panel",
    payload: {
      title: `/memory list — ${headerLine.trim()}`,
      items,
      actions,
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

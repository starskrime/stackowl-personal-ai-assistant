/**
 * StackOwl — Element 15 — `/memory` command router.
 *
 * Channel-agnostic dispatcher. Same router backs CLI and Telegram so
 * `/memory list` returns identical text on both surfaces (channel-parity rule).
 */

interface MemoryInvalidation {
  invalidated_at: string;
  invalidated_by: string;
  reason: string;
}

interface MemoryContradiction {
  contradicts_id: string;
  detected_at: string;
}

export interface MemoryRouterDeps {
  repo: {
    search(q: string, opts: { topK: number }): Promise<Array<{ kind: string; id: string; content: string; importance: number }>>;
    stats(): { total: number; invalidated: number; avgImportance: number; byKind: Record<string, number> };
    history(id: string): { record: { kind: string; content: string } | null; invalidations: Array<{ invalidated_at: string; invalidated_by: string; reason: string }>; contradictions: Array<{ contradicts_id: string; detected_at: string }> };
    getById(id: string): unknown;
    invalidate(id: string, opts: { reason: string; invalidatedBy: string }): void;
  };
  unifiedMemory?: {
    list(opts: { topK: number }): Promise<Array<{ kind: string; id: string; content: string }>>;
    recall(opts: { query: string; topK: number }): Promise<Array<{ kind: string; content: string; importance: number }>>;
    stats(): { total: number; invalidated: number; avgImportance: number; byKind: Record<string, number> };
  };
}

interface MemoryInvalidation {
  invalidated_at: string;
  invalidated_by: string;
  reason: string;
}

interface MemoryContradiction {
  contradicts_id: string;
  detected_at: string;
}

const HELP = `/memory commands:
  /memory list            — show recent memories
  /memory search <query>  — semantic search
  /memory stats           — counts by kind
  /memory history <id>    — invalidations + contradictions
  /memory invalidate <id> <reason...>
  /memory get <id>
  /memory export          — JSON dump of all valid memories`;

export async function dispatchMemoryCommand(
  verb: string,
  args: string[],
  deps: MemoryRouterDeps,
): Promise<string> {
  switch (verb) {
    case "list": {
      if (deps.unifiedMemory) {
        const hits = await deps.unifiedMemory.list({ topK: 20 });
        if (hits.length === 0) return "0 memories.";
        return (
          `${hits.length} memories:\n` +
          hits
            .map((h) => `  [${h.kind}] ${h.id.slice(0, 8)} — ${h.content.slice(0, 80)}`)
            .join("\n")
        );
      }
      // fallback to direct repo
      const records = await deps.repo.search("", { topK: 20 });
      if (records.length === 0) return "0 memories.";
      return (
        `${records.length} memories:\n` +
        records
          .map((r) => `  [${r.kind}] ${r.id.slice(0, 8)} — ${r.content.slice(0, 80)}`)
          .join("\n")
      );
    }

    case "search": {
      const q = args.join(" ").trim();
      if (!q) return "Usage: /memory search <query>";
      if (deps.unifiedMemory) {
        const hits = await deps.unifiedMemory.recall({ query: q, topK: 8 });
        if (hits.length === 0) return `No matches for "${q}".`;
        return hits
          .map((h) => `[${h.kind}] ${h.content} (importance=${h.importance.toFixed(2)})`)
          .join("\n");
      }
      // fallback
      const records = await deps.repo.search(q, { topK: 8 });
      if (records.length === 0) return `No matches for "${q}".`;
      return records
        .map((r) => `[${r.kind}] ${r.content} (importance=${r.importance.toFixed(2)})`)
        .join("\n");
    }

    case "stats": {
      if (deps.unifiedMemory) {
        const s = deps.unifiedMemory.stats();
        const lines = [
          `Total: ${s.total}`,
          `Invalidated: ${s.invalidated}`,
          `Avg importance: ${s.avgImportance.toFixed(3)}`,
        ];
        for (const [k, c] of Object.entries(s.byKind)) lines.push(`  ${k}: ${c}`);
        return lines.join("\n");
      }
      // fallback
      const s = deps.repo.stats();
      const lines = [
        `Total: ${s.total}`,
        `Invalidated: ${s.invalidated}`,
        `Avg importance: ${s.avgImportance.toFixed(3)}`,
      ];
      for (const [k, c] of Object.entries(s.byKind)) lines.push(`  ${k}: ${c}`);
      return lines.join("\n");
    }

    case "history": {
      const id = args[0];
      if (!id) return "Usage: /memory history <id>";
      const h = deps.repo.history(id);
      if (!h.record) return `Memory ${id} not found.`;
      const invLines = (h.invalidations as MemoryInvalidation[]).map(
        (i) => `  invalidated ${i.invalidated_at} by ${i.invalidated_by}: ${i.reason}`,
      );
      const conLines = (h.contradictions as MemoryContradiction[]).map(
        (c) => `  contradicts ${c.contradicts_id} (${c.detected_at})`,
      );
      return [`${h.record.kind}: ${h.record.content}`, ...invLines, ...conLines].join("\n");
    }

    case "get": {
      const id = args[0];
      if (!id) return "Usage: /memory get <id>";
      const r = deps.repo.getById(id);
      return r ? JSON.stringify(r, null, 2) : `Memory ${id} not found.`;
    }

    case "invalidate": {
      const id = args[0];
      const reason = args.slice(1).join(" ").trim();
      if (!id || !reason) return "Usage: /memory invalidate <id> <reason>";
      const r = deps.repo.getById(id);
      if (!r) return `Memory ${id} not found.`;
      deps.repo.invalidate(id, { reason, invalidatedBy: "user-command" });
      return `Invalidated ${id}.`;
    }

    case "export": {
      const records = await deps.repo.search("", { topK: 10000 });
      return JSON.stringify(records, null, 2);
    }

    default:
      return HELP;
  }
}

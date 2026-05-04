/**
 * StackOwl — Unified Memory Tool
 *
 * Two flavors:
 *
 *  1. createMemoryUnifiedTool({...}) — legacy dispatcher; routes search/store/
 *     get/write/invalidate to caller-supplied async implementations. Preserved
 *     for back-compat with the current src/index.ts wiring.
 *
 *  2. createMemoryTool({ repo, bus, hitl }) — Element 15 canonical tool. Backed
 *     by MemoryRepository + GatewayEventBus + HitlCheckpointStore. Actions:
 *       - search:    repo.search(query, { kinds, topK })
 *       - get:       repo.getById(id)
 *       - invalidate: repo.invalidate(id, …) — but invalidations of memories
 *                    with importance ≥ APPROVAL_THRESHOLD (0.8) route through
 *                    hitl.create() and require human approval before applying.
 *
 * Phase J wires bootstrap to use #2 instead of #1.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import type { MemoryRepository, MemoryKind, MemoryRecord } from "../memory/repository.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { HitlCheckpointStore } from "../engine/hitl.js";

export interface MemoryUnifiedDeps {
  search?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  store?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  get?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  write?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  invalidate?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
}

export function createMemoryUnifiedTool(deps: MemoryUnifiedDeps): ToolImplementation {
  return {
    definition: {
      name: "memory",
      description:
        "Unified memory tool. Use action:search to find memories, action:store to save a memory, " +
        "action:get to retrieve by ID, action:write to directly write a fact with category/confidence, " +
        "action:invalidate to mark matching facts as invalidated by keyword. " +
        "Example: {action:'search', query:'last project discussion'} or {action:'store', content:'User prefers MP4 format'} " +
        "or {action:'get', id:'mem_abc123'} or {action:'write', content:'User prefers dark mode', category:'prefs', confidence:0.9} " +
        "or {action:'invalidate', query:'dark mode'}.",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            description: "One of: search, store, get, write, invalidate",
            enum: ["search", "store", "get", "write", "invalidate"],
          },
          query: {
            type: "string",
            description: "Search query (for action:search or action:invalidate)",
          },
          content: {
            type: "string",
            description: "Content to store (for action:store or action:write)",
          },
          id: {
            type: "string",
            description: "Memory ID to retrieve (for action:get)",
          },
          tags: {
            type: "string",
            description: "Comma-separated tags (for action:store)",
          },
          category: {
            type: "string",
            description: "Fact category (for action:write)",
          },
          confidence: {
            type: "number",
            description: "Confidence score 0-1 (for action:write, default 0.8)",
          },
        },
        required: ["action"],
      },
      capabilities: ["memory_search", "memory_store", "memory_get", "memory_write", "memory_invalidate"],
      executionPolicy: { timeoutMs: 10_000, maxRetries: 0 },
    },
    category: "memory" as any,
    execute: async (args, context) => {
      const action = args["action"] as string;
      const impl = deps[action as keyof MemoryUnifiedDeps];

      if (!impl) {
        return JSON.stringify({
          success: false,
          data: null,
          error: {
            code: "ACTION_NOT_SUPPORTED",
            message: `Memory action '${action}' is not configured.`,
            suggestion: `Available actions: search, store, get, write, invalidate`,
          },
        });
      }

      return impl(args, context);
    },
  };
}

// ── Element 15 canonical memory tool ─────────────────────────────────────

const APPROVAL_THRESHOLD = 0.8;
const HITL_TTL_MINUTES = 60;

export interface MemoryToolDeps {
  repo: MemoryRepository;
  bus?: GatewayEventBus;
  hitl: Pick<HitlCheckpointStore, "create">;
}

function ok<T>(data: T): string {
  return JSON.stringify({ success: true, data, error: null });
}

function err(code: string, message: string, suggestion?: string): string {
  return JSON.stringify({
    success: false,
    data: null,
    error: { code, message, ...(suggestion ? { suggestion } : {}) },
  });
}

function recordToView(r: MemoryRecord) {
  return {
    id: r.id,
    kind: r.kind,
    content: r.content,
    importance: r.importance,
    goal_id: r.goal_id,
    verdict: r.verdict,
    valid_at: r.valid_at,
    invalid_at: r.invalid_at,
  };
}

export function createMemoryTool(deps: MemoryToolDeps): ToolImplementation {
  return {
    definition: {
      name: "memory",
      description:
        "Search, retrieve, or invalidate the assistant's long-term memory about the user. " +
        "Actions: search | get | invalidate. " +
        "Examples: {action:'search', query:'recent project'} → top matches. " +
        "{action:'get', id:'mem_abc'} → record by id. " +
        "{action:'invalidate', id:'mem_abc', reason:'user changed preference'} — " +
        "invalidations of high-importance memories (≥0.8) require human approval.",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            description: "One of: search, get, invalidate",
            enum: ["search", "get", "invalidate"],
          },
          query: { type: "string", description: "Search query (action:search)" },
          id: { type: "string", description: "Memory id (action:get|invalidate)" },
          reason: { type: "string", description: "Reason for invalidation (action:invalidate)" },
          kinds: {
            type: "array",
            items: { type: "string" },
            description:
              "Filter by kind (action:search). Allowed: semantic, episodic, working, procedural.",
          },
          topK: { type: "number", description: "Max results (action:search, default 10)" },
        },
        required: ["action"],
      },
      capabilities: ["memory_read", "memory_write"],
      executionPolicy: { timeoutMs: 10_000, maxRetries: 0 },
    },
    category: "memory",
    source: "builtin",
    execute: async (args: Record<string, unknown>, ctx: ToolContext): Promise<string> => {
      const action = args["action"] as string;
      try {
        switch (action) {
          case "search": {
            const query = (args["query"] as string) ?? "";
            const rawKinds = args["kinds"];
            const kinds = Array.isArray(rawKinds)
              ? (rawKinds.filter((k) => typeof k === "string") as MemoryKind[])
              : undefined;
            const topK = typeof args["topK"] === "number" ? (args["topK"] as number) : 10;
            const results = await deps.repo.search(query, { kinds, topK });
            return ok({ count: results.length, results: results.map(recordToView) });
          }

          case "get": {
            const id = args["id"] as string;
            if (!id) return err("MISSING_ID", "id is required for action:get");
            const record = deps.repo.getById(id);
            if (!record) return err("NOT_FOUND", `memory id=${id} not found`);
            return ok({ record: recordToView(record) });
          }

          case "invalidate": {
            const id = args["id"] as string;
            const reason = (args["reason"] as string) ?? "tool-invoked";
            if (!id) return err("MISSING_ID", "id is required for action:invalidate");
            const record = deps.repo.getById(id);
            if (!record) return err("NOT_FOUND", `memory id=${id} not found`);

            if (record.importance >= APPROVAL_THRESHOLD) {
              const sessionId =
                (ctx.engineContext?.sessionId as string | undefined) ?? "unknown-session";
              const ledgerId =
                (ctx.engineContext as unknown as { ledgerId?: string })?.ledgerId ?? "memory-tool";
              // ledgerSnapshot intentionally minimal — invalidation isn't a planned ledger task,
              // it's a side-effect on memory state. The HITL UI uses memo to render the prompt.
              const checkpointId = await deps.hitl.create(
                sessionId,
                ledgerId,
                {
                  kind: "approval",
                  pendingAction: `memory:invalidate:${id}`,
                  memo: {
                    whatIDid: `Identified a high-importance memory to invalidate (importance=${record.importance.toFixed(2)}).`,
                    whatINeed: `Approval to invalidate memory ${id}: "${record.content.slice(0, 200)}". Reason: ${reason}.`,
                    options: ["Approve", "Reject"],
                    recommendation: "Approve only if the fact is genuinely outdated or wrong.",
                  },
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  ledgerSnapshot: {} as any,
                },
                HITL_TTL_MINUTES,
              );
              return ok({
                requiresApproval: true,
                checkpointId,
                memoryId: id,
                importance: record.importance,
              });
            }

            deps.repo.invalidate(id, { reason, invalidatedBy: "memory-tool" });
            return ok({ invalidated: 1, memoryId: id });
          }

          default:
            return err(
              "ACTION_NOT_SUPPORTED",
              `Memory action '${action}' is not supported.`,
              "Available actions: search, get, invalidate",
            );
        }
      } catch (e) {
        return err(
          "EXECUTION_FAILED",
          e instanceof Error ? e.message : String(e),
          "Check repository state and HITL store availability",
        );
      }
    },
  };
}

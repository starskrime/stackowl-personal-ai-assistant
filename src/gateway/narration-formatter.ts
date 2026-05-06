import type { GatewaySystemEvent } from "./event-bus.js";

export type ToolSystemEvent = Extract<GatewaySystemEvent, { type: `tool:${string}` }>;

export function formatSignalPromoted(
  e: Extract<GatewaySystemEvent, { type: "signal:promoted" }>,
): string {
  return `🔭 [${e.signal.source}] ${e.signal.title} — advances "${e.goal.title}" (verdict: ${e.verdict})`;
}

const WEB_SEARCH_TOOLS = new Set(["web_search"]);
const WEB_FETCH_TOOLS  = new Set(["web_fetch"]);
const MEM_SEARCH_TOOLS = new Set(["recall_memory", "memory_search", "pellet_recall", "memory"]);
const MEM_STORE_TOOLS  = new Set(["remember"]);

export function formatToolEvent(event: ToolSystemEvent): string | null {
  switch (event.type) {
    case "tool:start": {
      const { toolName, args } = event;

      if (WEB_SEARCH_TOOLS.has(toolName)) {
        const q = String(args["query"] ?? args["q"] ?? "");
        return q ? `Searching the web for "${q}"…` : "Searching the web…";
      }
      if (WEB_FETCH_TOOLS.has(toolName)) {
        const url = String(args["url"] ?? "");
        return url ? `Fetching ${url}…` : "Fetching page…";
      }

      if (toolName === "memory" && typeof args["action"] === "string") {
        const action = args["action"] as string;
        if (action === "search") {
          const q = String(args["query"] ?? "");
          return q ? `Searching memory for "${q}"…` : "Searching memory…";
        }
        if (action === "store") return "Saving to memory…";
        if (action === "get")   return "Retrieving from memory…";
      }
      if (MEM_SEARCH_TOOLS.has(toolName)) {
        const q = String(args["query"] ?? "");
        return q ? `Searching memory for "${q}"…` : "Searching memory…";
      }
      if (MEM_STORE_TOOLS.has(toolName)) return "Saving to memory…";

      if (toolName === "run_shell_command") {
        const cmd = String(args["command"] ?? "").slice(0, 60);
        return `Running: ${cmd}${cmd.length >= 60 ? "…" : ""}`;
      }
      if (toolName === "read_file") {
        const p = String(args["path"] ?? args["file_path"] ?? "");
        return p ? `Reading ${p}…` : "Reading file…";
      }
      if (toolName === "write_file" || toolName === "edit_file") {
        const p = String(args["path"] ?? args["file_path"] ?? "");
        return p ? `Writing ${p}…` : "Writing file…";
      }
      if (toolName === "orchestrate_tasks" || toolName === "summon_parliament") {
        return "Gathering perspectives…";
      }

      return `Using ${toolName}…`;
    }

    case "tool:result":
      return event.success ? null : `⚠ ${event.toolName} failed, trying alternative…`;

    case "tool:retry":
      return `Retrying ${event.toolName} (attempt ${event.attempt})…`;

    case "tool:fallback":
      return `${event.fromTool} blocked, switching to ${event.toTool}…`;

    case "tool:goal_advance":
      return null;

    case "tool:goal_blocked":
      return event.suggestion
        ? `${event.toolName} didn't advance the goal. Trying: ${event.suggestion}`
        : `${event.toolName} didn't advance the goal, finding alternative…`;

    default:
      return null;
  }
}

import type { TierAttempt } from "../browser/envelope.js";

export type NarrationChannel = "cli" | "telegram" | "slack" | "web";

export function formatWebAttempts(attempts: TierAttempt[], channel: NarrationChannel): string {
  if (channel === "cli") {
    return attempts.map((a) => {
      const ext = a.httpStatus ? `${a.httpStatus}, ${a.blockedReason ?? a.outcome}` : (a.blockedReason ?? a.outcome);
      return `  → ${a.name} (${ext})`;
    }).join("\n");
  }
  if (channel === "telegram" || channel === "slack") {
    return `Tried ${attempts.length} ways: ${attempts.map((a) => `${a.name} ${a.outcome}`).join(", ")}.`;
  }
  // web
  const rows = attempts.map((a) => `<li>${a.tier}. ${a.name} — ${a.outcome}${a.blockedReason ? " (" + a.blockedReason + ")" : ""}</li>`).join("");
  return `<details><summary>Tier attempts (${attempts.length})</summary><ol>${rows}</ol></details>`;
}

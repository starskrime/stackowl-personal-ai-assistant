/**
 * StackOwl — Session Tools
 *
 * Provides inter-session communication similar to OpenCLAW.
 * Allows querying other sessions, spawning sub-agents, etc.
 */

import type { ToolImplementation, ToolContext } from "../../tools/registry.js";
import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";

export class SessionsListTool implements ToolImplementation {
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  definition = {
    name: "sessions_list",
    description: `List all active sessions. Returns session IDs, timestamps, and message counts.

Examples:
- sessions_list: List all sessions
- sessions_list limit=10: List last 10 sessions`,
    parameters: {
      type: "object" as const,
      properties: {
        limit: {
          type: "number",
          description: "Maximum sessions to return (default: 10)",
        },
      },
    },
  };

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const sessionsDir = join(this.workspacePath, "sessions");

    try {
      const files = await readdir(sessionsDir);
      const sessions = files
        .filter((f) => f.endsWith(".json"))
        .slice(0, (args["limit"] as number) || 10);

      const details = [];
      for (const f of sessions) {
        try {
          const content = await readFile(join(sessionsDir, f), "utf-8");
          const data = JSON.parse(content);
          details.push({
            id: data.id,
            messages: data.messages?.length || 0,
            lastUpdated: data.metadata?.lastUpdatedAt || 0,
            owl: data.metadata?.owlName || "unknown",
          });
        } catch {
          // Skip invalid files
        }
      }

      return JSON.stringify({ sessions: details }, null, 2);
    } catch (error) {
      return JSON.stringify({ error: String(error) });
    }
  }
}

export class SessionsHistoryTool implements ToolImplementation {
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  definition = {
    name: "sessions_history",
    description: `Get conversation history from another session.

Examples:
- sessions_history sessionKey="main": Get main session history
- sessions_history sessionKey="session_123" limit=10: Get last 10 messages`,
    parameters: {
      type: "object" as const,
      properties: {
        sessionKey: {
          type: "string",
          description: "Session ID to fetch history from",
        },
        limit: {
          type: "number",
          description: "Number of messages to return (default: 20)",
        },
      },
      required: ["sessionKey"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const sessionKey = args["sessionKey"] as string;
    const limit = (args["limit"] as number) || 20;

    if (!sessionKey) {
      return "ERROR: sessionKey is required";
    }

    try {
      const sessionsDir = join(this.workspacePath, "sessions");
      let resolvedKey = sessionKey;

      // "main", "latest", "current" → resolve to most recently modified session
      if (["main", "latest", "current"].includes(sessionKey.toLowerCase())) {
        const files = await readdir(sessionsDir).catch(() => [] as string[]);
        const jsonFiles = files.filter((f) => f.endsWith(".json"));
        if (jsonFiles.length === 0) {
          return JSON.stringify({ error: `No sessions found in ${sessionsDir}` });
        }
        const withMtime = await Promise.all(
          jsonFiles.map(async (f) => {
            const s = await stat(join(sessionsDir, f)).catch(() => null);
            return { f, mtime: s?.mtimeMs ?? 0 };
          }),
        );
        withMtime.sort((a, b) => b.mtime - a.mtime);
        resolvedKey = withMtime[0].f.replace(/\.json$/, "");
      }

      const sessionPath = join(sessionsDir, `${resolvedKey}.json`);
      const content = await readFile(sessionPath, "utf-8");
      const session = JSON.parse(content);

      const messages = session.messages
        ?.slice(-limit)
        .map((m: { role: string; content: string }) => ({
          role: m.role,
          content: m.content?.slice(0, 500) || "",
        }));

      return JSON.stringify({ sessionKey: resolvedKey, messages }, null, 2);
    } catch (error) {
      return JSON.stringify({ error: `Session not found: ${String(error)}` });
    }
  }
}

export class SessionStatusTool implements ToolImplementation {
  definition = {
    name: "session_status",
    description: `Get current session status.

Examples:
- session_status: Get current session info`,
    parameters: {
      type: "object" as const,
      properties: {},
    },
  };

  async execute(
    _args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    return JSON.stringify({
      status: "active",
      cwd: context.cwd,
      message: "Session is active and ready",
    });
  }
}

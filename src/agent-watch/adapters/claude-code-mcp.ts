/**
 * StackOwl — Agent Watch: Claude Code Channels (MCP) Adapter
 *
 * Implements the Claude Code Channels protocol as an MCP stdio server.
 * Claude Code spawns this as a subprocess and communicates over stdin/stdout.
 *
 * Transport: stdio only (Claude Code requirement)
 * Capabilities: claude/channel + claude/channel/permission (permission relay)
 *
 * Setup:
 *   1. Add to .mcp.json: { "mcpServers": { "stackowl-watch": { "command": "node", "args": ["...path/to/mcp-entry.js"] } } }
 *   2. Run: claude --dangerously-load-development-channels server:stackowl-watch
 *
 * Permission relay protocol:
 *   - Claude Code sends: notifications/claude/channel/permission_request
 *     { request_id: string (5 chars, no 'l'), tool_name, description, input_preview }
 *   - StackOwl relays to Telegram, waits for "yes abcde" / "no abcde" reply
 *   - StackOwl sends: notifications/claude/channel/permission
 *     { request_id, behavior: "allow" | "deny" }
 */

import { EventEmitter } from "node:events";
import { log } from "../../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface PermissionRequest {
  request_id: string;
  tool_name: string;
  description: string;
  input_preview: string;
}

// ─── MCP Channel Server ───────────────────────────────────────────

export class ClaudeCodeMcpAdapter extends EventEmitter {
  // Retained for future stop() graceful shutdown
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  private mcpHandle: { close?: () => void } | null = null;
  private running = false;

  /**
   * Start the MCP stdio server.
   * Called from AgentWatchManager when Claude Code Channels mode is enabled.
   * Emits: "permission_request" with PermissionRequest payload
   */
  async start(
    onPermissionRequest: (req: PermissionRequest) => Promise<"allow" | "deny">,
  ): Promise<void> {
    if (this.running) return;

    try {
      // Dynamically import MCP SDK to avoid hard dependency
      const { Server } = await import("@modelcontextprotocol/sdk/server/index.js");
      const { StdioServerTransport } = await import(
        "@modelcontextprotocol/sdk/server/stdio.js"
      );
      const { z } = await import("zod");

      const server = new Server(
        { name: "stackowl-watch", version: "1.0.0" },
        {
          capabilities: {
            experimental: {
              "claude/channel": {},
              "claude/channel/permission": {},
            },
            tools: {},
          },
          instructions:
            "StackOwl supervision channel. All tool permission requests are relayed to the user via Telegram. Reply with the granted/denied verdict.",
        },
      );

      // Handle incoming permission_request notifications from Claude Code
      const PermissionRequestSchema = z.object({
        method: z.literal("notifications/claude/channel/permission_request"),
        params: z.object({
          request_id: z.string(),
          tool_name: z.string(),
          description: z.string(),
          input_preview: z.string(),
        }),
      });

      // @ts-ignore — MCP SDK generic handler
      server.setNotificationHandler(PermissionRequestSchema, async ({ params }) => {
        log.engine.info(
          `[AgentWatch/MCP] Permission request: ${params.tool_name} (${params.request_id})`,
        );

        const behavior = await onPermissionRequest(params);

        // Send verdict back to Claude Code
        await server.notification({
          method: "notifications/claude/channel/permission",
          params: {
            request_id: params.request_id,
            behavior,
          },
        });
      });

      const transport = new StdioServerTransport();
      await server.connect(transport);
      this.mcpHandle = server as { close?: () => void };
      this.running = true;

      log.engine.info("[AgentWatch/MCP] Claude Code Channels server started (stdio)");
    } catch (err) {
      log.engine.warn(
        `[AgentWatch/MCP] Failed to start MCP server: ${err instanceof Error ? err.message : err}`,
      );
      log.engine.warn(
        "[AgentWatch/MCP] Install @modelcontextprotocol/sdk to enable Channels support",
      );
    }
  }

  async stop(): Promise<void> {
    this.running = false;
    this.mcpHandle?.close?.();
    this.mcpHandle = null;
  }

  isRunning(): boolean {
    return this.running;
  }
}

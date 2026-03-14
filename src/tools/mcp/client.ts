/**
 * StackOwl — MCP Client
 *
 * Connects to a single MCP (Model Context Protocol) server and wraps
 * its tools as StackOwl ToolImplementations for seamless integration
 * with the existing ToolRegistry.
 *
 * Supports stdio and SSE transports.
 */

import { spawn, type ChildProcess } from "node:child_process";
import type { ToolImplementation, ToolContext } from "../registry.js";
import type { ToolDefinition } from "../../providers/base.js";
import { log } from "../../logger.js";

export interface MCPServerConfig {
  name: string;
  transport: "stdio" | "sse";
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
}

interface MCPRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

interface MCPResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code: number; message: string };
}

interface MCPTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export class MCPClient {
  private process: ChildProcess | null = null;
  private requestId = 0;
  private pending = new Map<number, {
    resolve: (value: unknown) => void;
    reject: (error: Error) => void;
  }>();
  private buffer = "";
  private connected = false;
  private discoveredTools: MCPTool[] = [];

  constructor(private config: MCPServerConfig) {}

  get serverName(): string {
    return this.config.name;
  }

  get isConnected(): boolean {
    return this.connected;
  }

  /**
   * Connect to the MCP server and perform initialization handshake.
   */
  async connect(): Promise<void> {
    if (this.config.transport === "stdio") {
      await this.connectStdio();
    } else {
      throw new Error(`MCP transport "${this.config.transport}" not yet supported. Use "stdio".`);
    }

    // Initialize handshake
    await this.sendRequest("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "stackowl", version: "0.1.0" },
    });

    // Send initialized notification
    this.sendNotification("notifications/initialized", {});

    this.connected = true;
    log.engine.info(`[MCP] Connected to "${this.config.name}"`);
  }

  /**
   * Discover available tools from the MCP server.
   */
  async listTools(): Promise<MCPTool[]> {
    const result = await this.sendRequest("tools/list", {}) as { tools: MCPTool[] };
    this.discoveredTools = result.tools ?? [];
    return this.discoveredTools;
  }

  /**
   * Call a tool on the MCP server.
   */
  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
    const result = await this.sendRequest("tools/call", {
      name,
      arguments: args,
    }) as { content: Array<{ type: string; text?: string }> };

    // Extract text content from the response
    const textParts = (result.content ?? [])
      .filter((c) => c.type === "text" && c.text)
      .map((c) => c.text!);

    return textParts.join("\n") || JSON.stringify(result);
  }

  /**
   * Convert discovered MCP tools to StackOwl ToolImplementations.
   */
  toToolImplementations(): ToolImplementation[] {
    return this.discoveredTools.map((mcpTool) => {
      const prefixedName = `mcp_${this.config.name}_${mcpTool.name}`;

      const definition: ToolDefinition = {
        name: prefixedName,
        description: mcpTool.description ?? `MCP tool: ${mcpTool.name}`,
        parameters: (mcpTool.inputSchema ?? { type: "object", properties: {} }) as ToolDefinition["parameters"],
      };

      const client = this;
      const originalName = mcpTool.name;

      return {
        definition,
        category: "mcp" as const,
        source: "mcp",
        async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
          return client.callTool(originalName, args);
        },
      };
    });
  }

  /**
   * Disconnect from the MCP server.
   */
  disconnect(): void {
    this.connected = false;
    if (this.process) {
      this.process.kill();
      this.process = null;
    }
    // Reject any pending requests
    for (const [, { reject }] of this.pending) {
      reject(new Error("MCP connection closed"));
    }
    this.pending.clear();
    log.engine.info(`[MCP] Disconnected from "${this.config.name}"`);
  }

  // ─── Private ──────────────────────────────────────────────────

  private async connectStdio(): Promise<void> {
    if (!this.config.command) {
      throw new Error(`MCP stdio transport requires a "command" field.`);
    }

    const env = { ...process.env, ...this.config.env };
    this.process = spawn(this.config.command, this.config.args ?? [], {
      stdio: ["pipe", "pipe", "pipe"],
      env,
    });

    this.process.stdout!.on("data", (data: Buffer) => {
      this.buffer += data.toString();
      this.processBuffer();
    });

    this.process.stderr!.on("data", (data: Buffer) => {
      log.engine.warn(`[MCP:${this.config.name}:stderr] ${data.toString().trim()}`);
    });

    this.process.on("exit", (code) => {
      log.engine.warn(`[MCP:${this.config.name}] Process exited with code ${code}`);
      this.connected = false;
    });

    // Wait a moment for process to start
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => resolve(), 2000);
      this.process!.on("error", (err) => {
        clearTimeout(timeout);
        reject(new Error(`Failed to start MCP server "${this.config.name}": ${err.message}`));
      });
    });
  }

  private processBuffer(): void {
    // JSON-RPC messages are separated by newlines
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const msg = JSON.parse(trimmed) as MCPResponse;
        if (msg.id !== undefined && this.pending.has(msg.id)) {
          const { resolve, reject } = this.pending.get(msg.id)!;
          this.pending.delete(msg.id);
          if (msg.error) {
            reject(new Error(`MCP error: ${msg.error.message}`));
          } else {
            resolve(msg.result);
          }
        }
      } catch {
        // Skip malformed lines
      }
    }
  }

  private sendRequest(method: string, params: Record<string, unknown>): Promise<unknown> {
    const id = ++this.requestId;
    const request: MCPRequest = { jsonrpc: "2.0", id, method, params };

    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });

      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`MCP request "${method}" timed out after 30s`));
      }, 30000);

      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timeout); resolve(value); },
        reject: (error) => { clearTimeout(timeout); reject(error); },
      });

      this.process?.stdin?.write(JSON.stringify(request) + "\n");
    });
  }

  private sendNotification(method: string, params: Record<string, unknown>): void {
    const notification = { jsonrpc: "2.0", method, params };
    this.process?.stdin?.write(JSON.stringify(notification) + "\n");
  }
}

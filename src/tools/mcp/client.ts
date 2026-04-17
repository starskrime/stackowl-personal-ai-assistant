/**
 * StackOwl — MCP Client
 *
 * Connects to a single MCP (Model Context Protocol) server and wraps
 * its tools as StackOwl ToolImplementations for seamless integration
 * with the existing ToolRegistry.
 *
 * Supports:
 *   - stdio transport (subprocess via command + args)
 *   - SSE transport  (HTTP EventSource to a URL endpoint)
 *
 * Resilience:
 *   - Automatic reconnect with exponential backoff (stdio)
 *   - 30-second per-request timeout with clean rejection
 *   - Graceful disconnect that rejects all in-flight requests
 */

import { spawn, type ChildProcess } from "node:child_process";
import type { ToolImplementation, ToolContext } from "../registry.js";
import type { ToolDefinition } from "../../providers/base.js";
import { log } from "../../logger.js";

export interface MCPServerConfig {
  name: string;
  transport: "stdio" | "sse";
  /** stdio: executable command */
  command?: string;
  /** stdio: arguments to pass to command */
  args?: string[];
  /** sse: full URL to the SSE endpoint (e.g. http://localhost:3000/sse) */
  url?: string;
  /** Extra env vars merged into the subprocess environment (stdio only) */
  env?: Record<string, string>;
  /** Reconnect on crash? Default: true (stdio only) */
  autoReconnect?: boolean;
  /** Max reconnect delay in ms. Default: 30_000 */
  maxReconnectDelayMs?: number;
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

const REQUEST_TIMEOUT_MS = 30_000;
const STDIO_CONNECT_TIMEOUT_MS = 5_000;

export class MCPClient {
  private process: ChildProcess | null = null;
  private requestId = 0;
  private pending = new Map<
    number,
    { resolve: (value: unknown) => void; reject: (error: Error) => void }
  >();
  private buffer = "";
  private connected = false;
  private discoveredTools: MCPTool[] = [];
  private reconnectAttempt = 0;
  private destroyed = false;

  // SSE state
  private sseAbort: AbortController | null = null;

  constructor(private config: MCPServerConfig) {}

  get serverName(): string {
    return this.config.name;
  }

  get isConnected(): boolean {
    return this.connected;
  }

  get toolCount(): number {
    return this.discoveredTools.length;
  }

  // ─── Public API ──────────────────────────────────────────────────

  /**
   * Connect to the MCP server and perform the JSON-RPC initialization handshake.
   */
  async connect(): Promise<void> {
    this.destroyed = false;

    if (this.config.transport === "stdio") {
      await this.connectStdio();
    } else if (this.config.transport === "sse") {
      await this.connectSSE();
    } else {
      throw new Error(
        `[MCP] Unknown transport "${(this.config as any).transport}". Use "stdio" or "sse".`,
      );
    }

    // MCP initialization handshake
    await this.sendRequest("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "stackowl", version: "0.1.0" },
    });
    this.sendNotification("notifications/initialized", {});

    this.connected = true;
    this.reconnectAttempt = 0;
    log.engine.info(`[MCP] Connected to "${this.config.name}" (${this.config.transport})`);
  }

  /**
   * Discover available tools from the MCP server.
   */
  async listTools(): Promise<MCPTool[]> {
    const result = (await this.sendRequest("tools/list", {})) as {
      tools: MCPTool[];
    };
    this.discoveredTools = result.tools ?? [];
    return this.discoveredTools;
  }

  /**
   * Refresh tool list from the server (handles tools/list_changed notifications).
   */
  async refreshTools(toolRegistry: import("../registry.js").ToolRegistry): Promise<void> {
    const prefix = `mcp_${this.config.name}_`;

    // Unregister old tools
    for (const tool of this.discoveredTools) {
      toolRegistry.unregister(`${prefix}${tool.name}`);
    }

    // Re-discover and re-register
    await this.listTools();
    for (const impl of this.toToolImplementations()) {
      try {
        toolRegistry.register(impl);
      } catch {
        // Tool collision: already registered from another source
      }
    }

    log.engine.info(
      `[MCP] "${this.config.name}": refreshed — ${this.discoveredTools.length} tool(s)`,
    );
  }

  /**
   * Call a tool on the MCP server.
   */
  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
    const result = (await this.sendRequest("tools/call", {
      name,
      arguments: args,
    })) as {
      content?: Array<{ type: string; text?: string; data?: string }>;
      isError?: boolean;
    };

    if (result.isError) {
      const errText = (result.content ?? [])
        .filter((c) => c.type === "text" && c.text)
        .map((c) => c.text!)
        .join("\n");
      throw new Error(`MCP tool "${name}" returned an error: ${errText}`);
    }

    const textParts = (result.content ?? [])
      .filter((c) => c.type === "text" && c.text)
      .map((c) => c.text!);

    return textParts.join("\n") || JSON.stringify(result);
  }

  /**
   * Convert discovered MCP tools into StackOwl ToolImplementations.
   * Tool names are prefixed: mcp_<serverName>_<toolName>
   */
  toToolImplementations(): ToolImplementation[] {
    return this.discoveredTools.map((mcpTool) => {
      const prefixedName = `mcp_${this.config.name}_${mcpTool.name}`;

      const definition: ToolDefinition = {
        name: prefixedName,
        description:
          `[MCP: ${this.config.name}] ` +
          (mcpTool.description ?? mcpTool.name),
        parameters: (mcpTool.inputSchema ?? {
          type: "object",
          properties: {},
        }) as ToolDefinition["parameters"],
      };

      const client = this;
      const originalName = mcpTool.name;

      return {
        definition,
        category: "mcp" as const,
        source: `mcp:${this.config.name}`,
        async execute(
          args: Record<string, unknown>,
          _context: ToolContext,
        ): Promise<string> {
          if (!client.isConnected) {
            throw new Error(
              `MCP server "${client.config.name}" is not connected. ` +
                `Use /mcp reconnect ${client.config.name} to restore the connection.`,
            );
          }
          return client.callTool(originalName, args);
        },
      } satisfies ToolImplementation;
    });
  }

  /**
   * Disconnect and clean up. Sets destroyed=true to prevent auto-reconnect.
   */
  disconnect(): void {
    this.destroyed = true;
    this.connected = false;

    // Abort SSE connection
    this.sseAbort?.abort();
    this.sseAbort = null;

    // Kill subprocess
    if (this.process) {
      this.process.kill();
      this.process = null;
    }

    // Reject all in-flight requests
    for (const [, { reject }] of this.pending) {
      reject(new Error(`MCP server "${this.config.name}" disconnected`));
    }
    this.pending.clear();

    log.engine.info(`[MCP] Disconnected from "${this.config.name}"`);
  }

  // ─── stdio transport ──────────────────────────────────────────────

  private async connectStdio(): Promise<void> {
    if (!this.config.command) {
      throw new Error(
        `MCP stdio transport requires a "command" field in server config.`,
      );
    }

    const env = { ...process.env, ...this.config.env };

    this.process = spawn(this.config.command, this.config.args ?? [], {
      stdio: ["pipe", "pipe", "pipe"],
      env,
    });

    this.buffer = "";

    this.process.stdout!.on("data", (data: Buffer) => {
      this.buffer += data.toString();
      this.processBuffer();
    });

    this.process.stderr!.on("data", (data: Buffer) => {
      const text = data.toString().trim();
      if (text) {
        log.engine.warn(`[MCP:${this.config.name}:stderr] ${text}`);
      }
    });

    this.process.on("exit", (code, signal) => {
      log.engine.warn(
        `[MCP:${this.config.name}] Process exited (code=${code}, signal=${signal})`,
      );
      this.connected = false;

      // Reject all pending requests on unexpected exit
      for (const [, { reject }] of this.pending) {
        reject(new Error(`MCP server "${this.config.name}" process exited unexpectedly`));
      }
      this.pending.clear();

      if (!this.destroyed && (this.config.autoReconnect ?? true)) {
        this.scheduleReconnect();
      }
    });

    this.process.on("error", (err) => {
      log.engine.warn(
        `[MCP:${this.config.name}] Spawn error: ${err.message}`,
      );
    });

    // Wait for the process to be ready to receive stdin
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        // Process started — consider it ready even if we haven't received data yet
        // (many MCP servers don't emit anything until the first request)
        resolve();
      }, STDIO_CONNECT_TIMEOUT_MS);

      this.process!.on("error", (err) => {
        clearTimeout(timer);
        reject(
          new Error(`Failed to start MCP server "${this.config.name}": ${err.message}`),
        );
      });

      // If process dies before timer, reject immediately
      this.process!.on("exit", (code) => {
        if (code !== 0 && code !== null) {
          clearTimeout(timer);
          reject(
            new Error(
              `MCP server "${this.config.name}" exited with code ${code} immediately after spawn`,
            ),
          );
        }
      });
    });
  }

  private scheduleReconnect(): void {
    if (this.destroyed) return;

    const maxDelay = this.config.maxReconnectDelayMs ?? 30_000;
    const delay = Math.min(1_000 * Math.pow(2, this.reconnectAttempt), maxDelay);
    this.reconnectAttempt++;

    log.engine.info(
      `[MCP] "${this.config.name}": reconnecting in ${delay}ms (attempt ${this.reconnectAttempt})…`,
    );

    setTimeout(async () => {
      if (this.destroyed) return;
      try {
        await this.connect();
        log.engine.info(`[MCP] "${this.config.name}": reconnected successfully.`);
      } catch (err) {
        log.engine.warn(
          `[MCP] "${this.config.name}": reconnect failed: ${
            err instanceof Error ? err.message : err
          }`,
        );
        this.scheduleReconnect();
      }
    }, delay);
  }

  // ─── SSE transport ────────────────────────────────────────────────

  private async connectSSE(): Promise<void> {
    if (!this.config.url) {
      throw new Error(
        `MCP SSE transport requires a "url" field (e.g. "http://localhost:3000/sse").`,
      );
    }

    this.sseAbort = new AbortController();
    const { signal } = this.sseAbort;

    // Open the SSE stream — the server will send a JSON-RPC endpoint via the first event
    const sseResponse = await fetch(this.config.url, {
      headers: { Accept: "text/event-stream" },
      signal,
    });

    if (!sseResponse.ok) {
      throw new Error(
        `[MCP:SSE] "${this.config.name}" connection failed: HTTP ${sseResponse.status}`,
      );
    }

    if (!sseResponse.body) {
      throw new Error(
        `[MCP:SSE] "${this.config.name}" returned no response body.`,
      );
    }

    // The MCP SSE spec sends the POST endpoint as the first "endpoint" event
    let postEndpoint: string | null = null;

    await new Promise<void>((resolve, reject) => {
      const reader = sseResponse.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      const pump = async () => {
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buf += decoder.decode(value, { stream: true });
            const lines = buf.split("\n");
            buf = lines.pop() ?? "";

            let eventType = "";
            let eventData = "";

            for (const line of lines) {
              if (line.startsWith("event:")) {
                eventType = line.slice(6).trim();
              } else if (line.startsWith("data:")) {
                eventData = line.slice(5).trim();
              } else if (line === "" && eventData) {
                // Dispatch the event
                if (eventType === "endpoint") {
                  // postEndpoint is relative or absolute URL
                  postEndpoint = eventData.startsWith("http")
                    ? eventData
                    : new URL(eventData, this.config.url!).href;
                  resolve();
                } else if (eventType === "message" || !eventType) {
                  // Incoming JSON-RPC message from server
                  try {
                    const msg = JSON.parse(eventData) as MCPResponse;
                    this.handleIncomingMessage(msg);
                  } catch {
                    // skip malformed
                  }
                }
                eventType = "";
                eventData = "";
              }
            }

            if (!postEndpoint) continue;
            // Yield pump back to event loop — don't block forever here
          }
        } catch (err: unknown) {
          if ((err as Error)?.name !== "AbortError") {
            log.engine.warn(
              `[MCP:SSE] "${this.config.name}" stream error: ${(err as Error).message}`,
            );
            this.connected = false;
            if (!this.destroyed && (this.config.autoReconnect ?? true)) {
              this.scheduleReconnect();
            }
          }
        } finally {
          reader.releaseLock();
        }
      };

      // Start pumping asynchronously
      pump().catch(() => {});

      // Timeout if we never receive the endpoint event
      setTimeout(() => {
        if (!postEndpoint) {
          reject(
            new Error(
              `[MCP:SSE] "${this.config.name}" timed out waiting for endpoint event`,
            ),
          );
        }
      }, STDIO_CONNECT_TIMEOUT_MS);
    });

    if (!postEndpoint) {
      throw new Error(`[MCP:SSE] "${this.config.name}" did not provide a POST endpoint.`);
    }

    // Store the post endpoint so sendRequest can use it
    (this as any)._ssePostEndpoint = postEndpoint;
    log.engine.info(
      `[MCP:SSE] "${this.config.name}": POST endpoint = ${postEndpoint}`,
    );
  }

  // ─── Message handling ─────────────────────────────────────────────

  private handleIncomingMessage(msg: MCPResponse): void {
    if (msg.id !== undefined && this.pending.has(msg.id)) {
      const { resolve, reject } = this.pending.get(msg.id)!;
      this.pending.delete(msg.id);
      if (msg.error) {
        reject(new Error(`MCP error [${msg.error.code}]: ${msg.error.message}`));
      } else {
        resolve(msg.result);
      }
    }
  }

  private processBuffer(): void {
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const msg = JSON.parse(trimmed) as MCPResponse;
        this.handleIncomingMessage(msg);
      } catch {
        // Skip malformed lines
      }
    }
  }

  private sendRequest(
    method: string,
    params: Record<string, unknown>,
  ): Promise<unknown> {
    const id = ++this.requestId;
    const request: MCPRequest = { jsonrpc: "2.0", id, method, params };

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new Error(`MCP request "${method}" to "${this.config.name}" timed out after 30s`),
        );
      }, REQUEST_TIMEOUT_MS);

      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); },
      });

      if (this.config.transport === "stdio") {
        this.process?.stdin?.write(JSON.stringify(request) + "\n");
      } else {
        // SSE transport: POST to the endpoint URL provided by the server
        const postUrl = (this as any)._ssePostEndpoint as string | undefined;
        if (!postUrl) {
          reject(new Error(`[MCP:SSE] No POST endpoint available for "${this.config.name}"`));
          return;
        }
        fetch(postUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
          signal: this.sseAbort?.signal,
        }).catch((err) => {
          this.pending.delete(id);
          clearTimeout(timer);
          reject(new Error(`[MCP:SSE] POST failed: ${err.message}`));
        });
      }
    });
  }

  private sendNotification(
    method: string,
    params: Record<string, unknown>,
  ): void {
    const notification = { jsonrpc: "2.0", method, params };
    if (this.config.transport === "stdio") {
      this.process?.stdin?.write(JSON.stringify(notification) + "\n");
    } else {
      const postUrl = (this as any)._ssePostEndpoint as string | undefined;
      if (postUrl) {
        fetch(postUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(notification),
        }).catch(() => {});
      }
    }
  }
}

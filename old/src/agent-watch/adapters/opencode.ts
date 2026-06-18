/**
 * StackOwl — Agent Watch: OpenCode Adapter
 *
 * OpenCode runs a built-in HTTP server (default port 4096).
 * This adapter:
 *   1. Subscribes to the SSE event stream at GET /event
 *   2. Detects when OpenCode is waiting for user input (permission prompts,
 *      clarifying questions, tool approvals)
 *   3. Relays the question via the onQuestion callback
 *   4. Injects the answer via POST /tui/submit-prompt
 *
 * OpenCode server must be running: `opencode` (it starts the server automatically)
 * Auth: set OPENCODE_SERVER_PASSWORD env var if configured
 *
 * Setup instructions for the user: nothing to configure — just ensure
 * OpenCode is running and tell StackOwl the port.
 */

import { randomBytes } from "node:crypto";
import { log } from "../../logger.js";
import type { AgentQuestion, Decision } from "./base.js";
import { RiskClassifier } from "../risk-classifier.js";

// ─── Types ────────────────────────────────────────────────────────

export interface OpenCodeAdapterConfig {
  /** OpenCode server port. Default: 4096 */
  port?: number;
  /** OpenCode server password (OPENCODE_SERVER_PASSWORD). Optional. */
  password?: string;
  /** Agent session ID to associate with (userId:channelId) */
  sessionId: string;
}

export interface OpenCodeDiagnosis {
  reachable: boolean;
  port: number;
  reason?: "not_running" | "server_error" | "timeout" | "unknown";
  detail?: string;
  /** Whether the opencode binary is installed */
  installed?: boolean;
  /** A different port where something responded */
  altPort?: number;
}

interface OpenCodeEvent {
  type: string;
  properties?: Record<string, unknown>;
}

// Events that indicate OpenCode is waiting for user input
const WAITING_EVENT_TYPES = new Set([
  "session.tool.permission",
  "session.ask",
  "assistant.message.updated",  // catch-all for messages that end with a question
]);

// ─── OpenCode Adapter ─────────────────────────────────────────────

export class OpenCodeAdapter {
  private port: number;
  private baseUrl: string;
  private headers: Record<string, string>;
  private classifier = new RiskClassifier();
  private running = false;
  private abortController: AbortController | null = null;
  readonly sessionId: string;

  constructor(private config: OpenCodeAdapterConfig) {
    this.sessionId = config.sessionId;
    this.port = config.port ?? 4096;
    this.baseUrl = `http://localhost:${this.port}`;
    this.headers = {
      "Content-Type": "application/json",
      ...(config.password
        ? { Authorization: `Bearer ${config.password}` }
        : {}),
    };
  }

  /**
   * Start watching the OpenCode SSE stream.
   * Calls onQuestion whenever a decision is needed.
   */
  async start(
    onQuestion: (q: AgentQuestion) => Promise<Decision>,
  ): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.abortController = new AbortController();

    log.engine.info(
      `[OpenCode] Subscribing to SSE stream at ${this.baseUrl}/event`,
    );

    this.streamLoop(onQuestion).catch((err) => {
      if (this.running) {
        log.engine.warn(
          `[OpenCode] SSE stream error: ${err instanceof Error ? err.message : err}`,
        );
      }
    });
  }

  async stop(): Promise<void> {
    this.running = false;
    this.abortController?.abort();
    this.abortController = null;
    log.engine.info("[OpenCode] Stopped watching");
  }

  isRunning(): boolean {
    return this.running;
  }

  // ─── SSE Stream Loop ─────────────────────────────────────────

  private async streamLoop(
    onQuestion: (q: AgentQuestion) => Promise<Decision>,
  ): Promise<void> {
    while (this.running) {
      try {
        await this.connectAndProcess(onQuestion);
      } catch {
        if (!this.running) break;
        // Reconnect after 5 seconds
        await new Promise((r) => setTimeout(r, 5000));
        log.engine.info("[OpenCode] Reconnecting to SSE stream...");
      }
    }
  }

  private async connectAndProcess(
    onQuestion: (q: AgentQuestion) => Promise<Decision>,
  ): Promise<void> {
    const response = await fetch(`${this.baseUrl}/event`, {
      headers: { ...this.headers, Accept: "text/event-stream" },
      signal: this.abortController?.signal,
    });

    if (!response.ok) {
      throw new Error(
        `SSE connection failed: ${response.status} ${response.statusText}`,
      );
    }

    if (!response.body) throw new Error("No response body");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (this.running) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6).trim();
          if (data && data !== "[DONE]") {
            this.handleEvent(data, onQuestion);
          }
        }
      }
    }
  }

  private handleEvent(
    rawData: string,
    onQuestion: (q: AgentQuestion) => Promise<Decision>,
  ): void {
    let event: OpenCodeEvent;
    try {
      event = JSON.parse(rawData) as OpenCodeEvent;
    } catch {
      return; // ignore malformed events
    }

    if (!WAITING_EVENT_TYPES.has(event.type)) return;

    const question = this.buildQuestion(event);
    if (!question) return;

    // Run async without blocking the stream reader
    onQuestion(question)
      .then((decision) => this.sendAnswer(decision, event))
      .catch((err) =>
        log.engine.warn(
          `[OpenCode] Answer injection failed: ${err instanceof Error ? err.message : err}`,
        ),
      );
  }

  private buildQuestion(event: OpenCodeEvent): AgentQuestion | null {
    const props = event.properties ?? {};

    // Tool permission event
    if (event.type === "session.tool.permission") {
      const toolName = String(props["tool"] ?? props["tool_name"] ?? "Unknown");
      const toolInput = (props["input"] as Record<string, unknown>) ?? {};
      const { risk } = this.classifier.classify(toolName, toolInput);

      return {
        id: randomBytes(3).toString("hex").slice(0, 4),
        sessionId: this.config.sessionId,
        toolName,
        toolInput,
        risk,
        receivedAt: Date.now(),
        raw: props,
      };
    }

    // Generic "ask" event
    if (event.type === "session.ask") {
      const message = String(props["message"] ?? props["content"] ?? "Question from OpenCode");
      return {
        id: randomBytes(3).toString("hex").slice(0, 4),
        sessionId: this.config.sessionId,
        toolName: "AskUser",
        toolInput: { message },
        risk: "medium",
        receivedAt: Date.now(),
        raw: props,
      };
    }

    return null;
  }

  // ─── Answer Injection ─────────────────────────────────────────

  private async sendAnswer(
    decision: Decision,
    _event: OpenCodeEvent,
  ): Promise<void> {
    const text = decision === "allow" ? "yes" : "no";

    // Try submit-prompt endpoint first
    try {
      const r = await fetch(`${this.baseUrl}/tui/submit-prompt`, {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify({ text }),
      });
      if (r.ok) {
        log.engine.info(`[OpenCode] Answer injected: ${text}`);
        return;
      }
    } catch {
      // fall through to append+submit
    }

    // Fallback: append then submit
    await fetch(`${this.baseUrl}/tui/append-prompt`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({ text }),
    });
    await fetch(`${this.baseUrl}/tui/submit-prompt`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({ text: "" }),
    });

    log.engine.info(`[OpenCode] Answer injected via append+submit: ${text}`);
  }

  // ─── Health Check ─────────────────────────────────────────────

  /** Check if OpenCode server is reachable */
  async ping(): Promise<boolean> {
    const result = await this.diagnose();
    return result.reachable;
  }

  /**
   * Detailed connectivity check.
   * Returns what's wrong and why, not just true/false.
   */
  async diagnose(): Promise<OpenCodeDiagnosis> {
    // 1. Try the configured port
    try {
      const r = await fetch(`${this.baseUrl}/`, {
        headers: this.headers,
        signal: AbortSignal.timeout(3000),
      });
      if (r.status < 500) {
        return { reachable: true, port: this.port };
      }
      return {
        reachable: false,
        port: this.port,
        reason: "server_error",
        detail: `Server responded with HTTP ${r.status}`,
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const isRefused = msg.includes("ECONNREFUSED") || msg.includes("Connection refused");
      const isTimeout = msg.includes("timeout") || msg.includes("AbortError");

      // 2. Check if opencode is installed
      const installed = await this.checkInstalled();

      // 3. Check if it's running on a different common port
      const altPort = await this.findAltPort();

      return {
        reachable: false,
        port: this.port,
        reason: isRefused ? "not_running" : isTimeout ? "timeout" : "unknown",
        detail: msg,
        installed,
        altPort,
      };
    }
  }

  private async checkInstalled(): Promise<boolean> {
    try {
      const { execFile } = await import("node:child_process");
      const { promisify } = await import("node:util");
      const exec = promisify(execFile);
      await exec("which", ["opencode"]);
      return true;
    } catch {
      return false;
    }
  }

  private async findAltPort(): Promise<number | undefined> {
    // Check other common ports OpenCode might use
    for (const port of [3000, 8080, 4097, 4095]) {
      try {
        const r = await fetch(`http://localhost:${port}/`, {
          signal: AbortSignal.timeout(500),
        });
        if (r.status < 500) return port;
      } catch {
        // not there
      }
    }
    return undefined;
  }
}

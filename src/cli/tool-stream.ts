/**
 * StackOwl — Real-Time Tool Streaming
 *
 * Streams real-time tool execution status to the CLI TUI.
 * Updates left panel with tool status as tools are called.
 */

import type { StreamEvent } from "../providers/base.js";

export type ToolStatus = "queued" | "running" | "done" | "error";

export interface ToolExecution {
  toolCallId: string;
  toolName: string;
  arguments: string;
  status: ToolStatus;
  startedAt: number;
  endedAt?: number;
  elapsedMs?: number;
  error?: string;
}

export interface ToolStreamCallbacks {
  onToolStart?: (toolName: string, toolCallId: string) => void;
  onToolEnd?: (toolName: string, toolCallId: string, success: boolean, elapsedMs: number) => void;
  onToolError?: (toolName: string, toolCallId: string, error: string) => void;
}

export class ToolStream {
  private tools: Map<string, ToolExecution> = new Map();
  private callbacks: ToolStreamCallbacks;
  private streamCallback: ((event: StreamEvent) => Promise<void>) | null = null;

  constructor(callbacks: ToolStreamCallbacks = {}) {
    this.callbacks = callbacks;
  }

  /**
   * Set the callback for stream events (for integration with engine).
   */
  setStreamCallback(cb: (event: StreamEvent) => Promise<void>): void {
    this.streamCallback = cb;
  }

  /**
   * Create a handler for onStreamEvent that updates tool status.
   */
  createStreamHandler(): (event: StreamEvent) => Promise<void> {
    return async (event: StreamEvent) => {
      this._handleEvent(event);
      if (this.streamCallback) {
        await this.streamCallback(event);
      }
    };
  }

  private _handleEvent(event: StreamEvent): void {
    switch (event.type) {
      case "tool_start":
        this._startTool(event.toolCallId, event.toolName);
        break;
      case "tool_args_delta": {
        const tool = this.tools.get(event.toolCallId);
        if (tool) {
          tool.arguments += event.argsDelta;
        }
        break;
      }
      case "tool_end":
        this._endTool(event.toolCallId, event.toolName, event.arguments);
        break;
    }
  }

  private _startTool(toolCallId: string, toolName: string): void {
    const tool: ToolExecution = {
      toolCallId,
      toolName,
      arguments: "",
      status: "running",
      startedAt: Date.now(),
    };
    this.tools.set(toolCallId, tool);
    this.callbacks.onToolStart?.(toolName, toolCallId);
  }

  private _endTool(toolCallId: string, toolName: string, _arguments: unknown): void {
    const tool = this.tools.get(toolCallId);
    if (!tool) return;

    tool.status = "done";
    tool.endedAt = Date.now();
    tool.elapsedMs = tool.endedAt - tool.startedAt;
    this.callbacks.onToolEnd?.(toolName, toolCallId, true, tool.elapsedMs);
  }

  /**
   * Mark a tool as errored.
   */
  errorTool(toolCallId: string, error: string): void {
    const tool = this.tools.get(toolCallId);
    if (!tool) return;

    tool.status = "error";
    tool.endedAt = Date.now();
    tool.elapsedMs = tool.endedAt - tool.startedAt;
    tool.error = error;
    this.callbacks.onToolError?.(tool.toolName, toolCallId, error);
  }

  /**
   * Get all currently running tools.
   */
  getRunningTools(): ToolExecution[] {
    return Array.from(this.tools.values()).filter(t => t.status === "running");
  }

  /**
   * Get all completed tools.
   */
  getCompletedTools(): ToolExecution[] {
    return Array.from(this.tools.values()).filter(t => t.status === "done");
  }

  /**
   * Get all tools with errors.
   */
  getErroredTools(): ToolExecution[] {
    return Array.from(this.tools.values()).filter(t => t.status === "error");
  }

  /**
   * Get all tracked tools.
   */
  getAllTools(): ToolExecution[] {
    return Array.from(this.tools.values());
  }

  /**
   * Get tool by ID.
   */
  getTool(toolCallId: string): ToolExecution | undefined {
    return this.tools.get(toolCallId);
  }

  /**
   * Clear all tool tracking.
   */
  clear(): void {
    this.tools.clear();
  }

  /**
   * Reset for a new response (keep only in-progress tools).
   */
  reset(): void {
    const inProgress = this.getRunningTools();
    this.tools.clear();
    for (const tool of inProgress) {
      this.tools.set(tool.toolCallId, tool);
    }
  }

  /**
   * Get total elapsed time for all tools.
   */
  getTotalElapsedMs(): number {
    const tools = this.getAllTools();
    if (tools.length === 0) return 0;
    return tools.reduce((sum, t) => sum + (t.elapsedMs ?? 0), 0);
  }

  /**
   * Get count of tools by status.
   */
  getToolCounts(): { queued: number; running: number; done: number; error: number } {
    const tools = this.getAllTools();
    return {
      queued: tools.filter(t => t.status === "queued").length,
      running: tools.filter(t => t.status === "running").length,
      done: tools.filter(t => t.status === "done").length,
      error: tools.filter(t => t.status === "error").length,
    };
  }
}

export function createToolStream(callbacks?: ToolStreamCallbacks): ToolStream {
  return new ToolStream(callbacks);
}
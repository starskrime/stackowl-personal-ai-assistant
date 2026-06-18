import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export type ToolStatus = "pending" | "running" | "done" | "failed";

export interface ToolCall {
  toolCallId: string;
  turnId: string;
  toolName: string;
  status: ToolStatus;
  startedAt: number;
  elapsedMs: number;
  progressMessage?: string;
  /** Truncated to last 4KB in state; full output written to disk by bridge. */
  outputSummary?: string;
  error?: string;
}

export interface ToolsState {
  /** SINGLE source of truth — replaces the old renderer._state.toolCalls + ToolStream duality. */
  toolCalls: Map<string, ToolCall>;
}

export const initialToolsState: ToolsState = {
  toolCalls: new Map(),
};

export function applyToolsEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "tool.requested": {
      const next = new Map(state.toolCalls);
      next.set(event.toolCallId, {
        toolCallId: event.toolCallId,
        turnId: event.turnId,
        toolName: event.toolName,
        status: "running",
        startedAt: Date.now(),
        elapsedMs: 0,
      });
      return { ...state, toolCalls: next };
    }

    case "tool.progress": {
      const existing = state.toolCalls.get(event.toolCallId);
      if (!existing) return state;
      const next = new Map(state.toolCalls);
      next.set(event.toolCallId, {
        ...existing,
        elapsedMs: event.elapsedMs,
        progressMessage: event.message,
      });
      return { ...state, toolCalls: next };
    }

    case "tool.completed": {
      const existing = state.toolCalls.get(event.toolCallId);
      if (!existing) return state;
      const next = new Map(state.toolCalls);
      next.set(event.toolCallId, {
        ...existing,
        status: "done",
        elapsedMs: event.elapsedMs,
        outputSummary: event.outputSummary,
      });
      return { ...state, toolCalls: next };
    }

    case "tool.failed": {
      const existing = state.toolCalls.get(event.toolCallId);
      if (!existing) return state;
      const next = new Map(state.toolCalls);
      next.set(event.toolCallId, {
        ...existing,
        status: "failed",
        elapsedMs: event.elapsedMs,
        error: event.error,
      });
      return { ...state, toolCalls: next };
    }

    default:
      return state;
  }
}

import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export type UiMode = "chat" | "parliament" | "onboarding" | "skills" | "sessions" | "owls";

export interface UiSliceState {
  mode: UiMode;
  /** Active owl shown in footer. */
  activeOwlName: string;
  activeOwlEmoji: string;
  activeModel: string;
  activeProvider: string;
  /** Turn in progress (generation active). */
  generating: boolean;
  /** Cumulative token + cost for the session. */
  totalTokens: number;
  totalCostUsd: number;
  /** Inline overlays shown above the Composer in ChatScreen. */
  showHelp: boolean;
  showSkillsOverlay: boolean;
  showMcpOverlay: boolean;
}

export const initialUiSliceState: UiSliceState = {
  mode: "chat",
  activeOwlName: "",
  activeOwlEmoji: "🦉",
  activeModel: "",
  activeProvider: "",
  generating: false,
  totalTokens: 0,
  totalCostUsd: 0,
  showHelp: false,
  showSkillsOverlay: false,
  showMcpOverlay: false,
};

export function applyUiEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "turn.started":
      return {
        ...state,
        generating: true,
        activeOwlName: event.owlName,
        activeOwlEmoji: event.owlEmoji,
        ...(event.model !== undefined ? { activeModel: event.model } : {}),
      };

    case "turn.committed": {
      const tokens = event.usage
        ? state.totalTokens + event.usage.promptTokens + event.usage.completionTokens
        : state.totalTokens;
      const cost = event.usage
        ? state.totalCostUsd + event.usage.costUsd
        : state.totalCostUsd;
      return { ...state, generating: false, totalTokens: tokens, totalCostUsd: cost };
    }

    case "parliament.round.started":
      return { ...state, mode: "parliament" };

    case "parliament.synthesis.ready":
      return { ...state, mode: "chat" };

    case "parliament.view.requested":
      return { ...state, mode: "parliament" };

    case "parliament.view.dismissed":
      return { ...state, mode: "chat" };

    case "sessions.view.requested":
      return { ...state, mode: "sessions" };

    case "sessions.view.dismissed":
      return { ...state, mode: "chat" };

    // ─── Owls picker ─────────────────────────────────────────────

    case "owls.view.requested":
      return { ...state, mode: "owls" };

    case "owls.view.dismissed":
      return { ...state, mode: "chat" };

    case "owl.changed":
      return {
        ...state,
        mode: "chat",
        activeOwlName: event.owlName,
        activeOwlEmoji: event.owlEmoji,
        ...(event.model !== undefined ? { activeModel: event.model } : {}),
      };

    // ─── Help overlay ─────────────────────────────────────────────

    case "help.view.requested":
      return { ...state, showHelp: true };

    case "help.view.dismissed":
      return { ...state, showHelp: false };

    // ─── Skills overlay ──────────────────────────────────────────

    case "skills.view.requested":
      return { ...state, showSkillsOverlay: true };

    case "skills.view.dismissed":
      return { ...state, showSkillsOverlay: false };

    // ─── MCP overlay ─────────────────────────────────────────────

    case "mcp.view.requested":
      return { ...state, showMcpOverlay: true };

    case "mcp.view.dismissed":
      return { ...state, showMcpOverlay: false };

    default:
      return state;
  }
}

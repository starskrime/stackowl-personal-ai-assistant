import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";
import { getContextWindow } from "../model-context.js";

export type UiMode = "chat" | "parliament" | "onboarding" | "skill-wizard" | "sessions" | "owls";

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
  /** Context window utilisation [0–100]. */
  contextWindowPct: number;
  /** Inline overlays shown above the Composer in ChatScreen. */
  showHelp: boolean;
  /** When non-null, the Composer captures the next Enter as a prompt answer instead of sending to LLM. */
  promptQuestion: string | null;
  promptChoices?: string[];
  promptDefault?: string;
  /** Phrase override for ThinkingIndicator, supplied by TuiProgressNotifier. Null = use random fallback. */
  thinkingPhrase: string | null;
  /** Current tool status text shown beneath the thinking spinner. Null = not shown. */
  thinkingTool: string | null;
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
  contextWindowPct: 0,
  showHelp: false,
  promptQuestion: null,
  promptChoices: undefined,
  promptDefault: undefined,
  thinkingPhrase: null,
  thinkingTool: null,
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
      const ctxWindow = getContextWindow(state.activeModel);
      const contextWindowPct = ctxWindow && event.usage
        ? Math.round((event.usage.promptTokens / ctxWindow) * 100)
        : state.contextWindowPct;
      return { ...state, generating: false, totalTokens: tokens, totalCostUsd: cost, contextWindowPct, thinkingPhrase: null, thinkingTool: null };
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

    case "prompt.requested":
      return { ...state, promptQuestion: event.question, promptChoices: event.choices, promptDefault: event.defaultChoice };

    case "prompt.submitted":
      return { ...state, promptQuestion: null, promptChoices: undefined, promptDefault: undefined };

    case "thinking.phrase":
      return { ...state, thinkingPhrase: event.phrase || null, thinkingTool: null };

    case "thinking.tool":
      return { ...state, thinkingTool: event.text };

    case "session.changed":
      return { ...state, thinkingPhrase: null, thinkingTool: null };

    default:
      return state;
  }
}

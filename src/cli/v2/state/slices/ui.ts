import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export type UiMode = "chat" | "parliament" | "onboarding" | "skills";

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
};

export function applyUiEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "turn.started":
      return {
        ...state,
        generating: true,
        activeOwlName: event.owlName,
        activeOwlEmoji: event.owlEmoji,
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

    default:
      return state;
  }
}

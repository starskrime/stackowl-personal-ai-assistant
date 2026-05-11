import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export interface ActivePanel {
  id: string;
  props: unknown;
}

export interface PanelSliceState {
  panelStack: ActivePanel[];
  activePanel: ActivePanel | null;  // mirrors panelStack top; kept for consumer compat
  panelFocus: "composer" | "panel";
}

export const initialPanelSliceState: PanelSliceState = {
  panelStack: [],
  activePanel: null,
  panelFocus: "composer",
};

export function applyPanelEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "panel.opened": {
      const next = { id: event.id, props: event.props };
      const newStack = [...state.panelStack, next];
      return { ...state, panelStack: newStack, activePanel: next, panelFocus: "panel" };
    }
    case "panel.popped": {
      const newStack = state.panelStack.slice(0, -1);
      const top = newStack.at(-1) ?? null;
      return { ...state, panelStack: newStack, activePanel: top, panelFocus: top ? "panel" : "composer" };
    }
    case "panel.closed":
      return { ...state, panelStack: [], activePanel: null, panelFocus: "composer" };
    case "onboarding.view.requested":
      return { ...state, mode: "onboarding" };
    case "onboarding.view.dismissed":
      return { ...state, mode: "chat" };
    default:
      return state;
  }
}

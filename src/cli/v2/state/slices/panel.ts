import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export interface ActivePanel {
  id: string;
  props: unknown;
}

export interface PanelSliceState {
  activePanel: ActivePanel | null;
  panelFocus: "composer" | "panel";
}

export const initialPanelSliceState: PanelSliceState = {
  activePanel: null,
  panelFocus: "composer",
};

export function applyPanelEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "panel.opened":
      return { ...state, activePanel: { id: event.id, props: event.props }, panelFocus: "panel" };
    case "panel.closed":
      return { ...state, activePanel: null, panelFocus: "composer" };
    case "onboarding.view.requested":
      return { ...state, mode: "onboarding" };
    case "onboarding.view.dismissed":
      return { ...state, mode: "chat" };
    default:
      return state;
  }
}

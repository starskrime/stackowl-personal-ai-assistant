import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export interface HeartbeatMessage {
  id: string;
  owlId: string;
  owlName: string;
  owlEmoji: string;
  text: string;
  timestamp: number;
  read: boolean;
}

export interface Notice {
  id: string;
  source: string;
  text: string;
  severity: "info" | "warn" | "error";
  timestamp: number;
}

export interface HeartbeatState {
  heartbeats: HeartbeatMessage[];
  notices: Notice[];
}

export const initialHeartbeatState: HeartbeatState = {
  heartbeats: [],
  notices: [],
};

let _noticeSeq = 0;

export function applyHeartbeatEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "heartbeat.message": {
      const msg: HeartbeatMessage = {
        id: `hb-${event.timestamp}`,
        owlId: event.owlId,
        owlName: event.owlName,
        owlEmoji: event.owlEmoji,
        text: event.text,
        timestamp: event.timestamp,
        read: false,
      };
      return { ...state, heartbeats: [...state.heartbeats, msg] };
    }

    case "notice": {
      const notice: Notice = {
        id: `notice-${++_noticeSeq}`,
        source: event.source,
        text: event.text,
        severity: event.severity ?? "info",
        timestamp: Date.now(),
      };
      return { ...state, notices: [...state.notices, notice] };
    }

    default:
      return state;
  }
}

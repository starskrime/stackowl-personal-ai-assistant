/**
 * UiEvent — typed discriminated union consumed by the v2 TUI.
 *
 * All engine signals (StreamEvent, gateway bus, heartbeat, parliament)
 * are translated into this shape by events/bridge.ts — the ONE translator.
 * Nothing else may produce UiEvents.
 */

export type OwlId = string;

// ─── Session ─────────────────────────────────────────────────────────────────

export interface SessionChangedEvent {
  kind: "session.changed";
  sessionId: string;
  title?: string;
}

// ─── Turn lifecycle ───────────────────────────────────────────────────────────

export interface TurnStartedEvent {
  kind: "turn.started";
  turnId: string;
  owlId: OwlId;
  owlName: string;
  owlEmoji: string;
  owlRole?: string;
}

export interface TokenDeltaEvent {
  kind: "token.delta";
  turnId: string;
  text: string;
}

export interface TurnCommittedEvent {
  kind: "turn.committed";
  turnId: string;
  /** Full final text of the turn */
  text: string;
  usage?: { promptTokens: number; completionTokens: number; costUsd: number };
}

// ─── Tool calls ───────────────────────────────────────────────────────────────

export interface ToolRequestedEvent {
  kind: "tool.requested";
  toolCallId: string;
  turnId: string;
  toolName: string;
  input?: unknown;
}

export interface ToolProgressEvent {
  kind: "tool.progress";
  toolCallId: string;
  message: string;
  elapsedMs: number;
}

export interface ToolCompletedEvent {
  kind: "tool.completed";
  toolCallId: string;
  elapsedMs: number;
  outputSummary?: string;
}

export interface ToolFailedEvent {
  kind: "tool.failed";
  toolCallId: string;
  elapsedMs: number;
  error: string;
}

// ─── Parliament ───────────────────────────────────────────────────────────────

export interface ParliamentRoundStartedEvent {
  kind: "parliament.round.started";
  debateId: string;
  round: number;
  totalRounds: number;
  owls: Array<{ owlId: OwlId; owlName: string; owlEmoji: string }>;
}

export interface ParliamentPositionReadyEvent {
  kind: "parliament.position.ready";
  debateId: string;
  owlId: OwlId;
  owlName: string;
  owlEmoji: string;
  position: string;
}

export interface ParliamentChallengeReadyEvent {
  kind: "parliament.challenge.ready";
  debateId: string;
  owlId: OwlId;
  owlName: string;
  owlEmoji: string;
  challenge: string;
}

export interface ParliamentSynthesisReadyEvent {
  kind: "parliament.synthesis.ready";
  debateId: string;
  synthesis: string;
  owlId: OwlId;
  owlName: string;
}

// ─── Heartbeat / notices ──────────────────────────────────────────────────────

export interface HeartbeatMessageEvent {
  kind: "heartbeat.message";
  owlId: OwlId;
  owlName: string;
  owlEmoji: string;
  text: string;
  timestamp: number;
}

export interface NoticeEvent {
  kind: "notice";
  /** e.g. "instinct", "perch", "skill", "agent-watch" */
  source: string;
  text: string;
  severity?: "info" | "warn" | "error";
}

// ─── Union ────────────────────────────────────────────────────────────────────

export type UiEvent =
  | SessionChangedEvent
  | TurnStartedEvent
  | TokenDeltaEvent
  | TurnCommittedEvent
  | ToolRequestedEvent
  | ToolProgressEvent
  | ToolCompletedEvent
  | ToolFailedEvent
  | ParliamentRoundStartedEvent
  | ParliamentPositionReadyEvent
  | ParliamentChallengeReadyEvent
  | ParliamentSynthesisReadyEvent
  | HeartbeatMessageEvent
  | NoticeEvent;

export type UiEventKind = UiEvent["kind"];

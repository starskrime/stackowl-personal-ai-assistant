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

export interface SessionSummaryRecord {
  sessionId: string;
  title: string;
  lastActiveAt: number;
}

export interface SessionsLoadedEvent {
  kind: "sessions.loaded";
  sessions: SessionSummaryRecord[];
}

/** Emitted when the user types /sessions to open the session picker. */
export interface SessionsViewRequestedEvent {
  kind: "sessions.view.requested";
}

/** Emitted when the user dismisses the session picker (Escape or selects a session). */
export interface SessionsViewDismissedEvent {
  kind: "sessions.view.dismissed";
}

// ─── Turn lifecycle ───────────────────────────────────────────────────────────

export interface TurnStartedEvent {
  kind: "turn.started";
  turnId: string;
  owlId: OwlId;
  owlName: string;
  owlEmoji: string;
  owlRole?: string;
  model?: string;
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
  /** Optional owl identity — populated for broadcast/sendToUser messages that
   *  are not preceded by a turn.started event. Renderers should use these when
   *  no prior turn.started exists for this turnId. */
  owlEmoji?: string;
  owlName?: string;
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

/** Emitted when the user presses Ctrl+P to open the Parliament view. */
export interface ParliamentViewRequestedEvent {
  kind: "parliament.view.requested";
}

/** Emitted when the user dismisses the Parliament view (Ctrl+P again or back shortcut). */
export interface ParliamentViewDismissedEvent {
  kind: "parliament.view.dismissed";
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

// ─── Owls ─────────────────────────────────────────────────────────────────────

export interface OwlSummaryRecord {
  name: string;
  emoji: string;
  /** Short description / specialties joined, used in the picker */
  description: string;
  isActive: boolean;
}

export interface OwlsLoadedEvent {
  kind: "owls.loaded";
  owls: OwlSummaryRecord[];
}

export interface OwlsViewRequestedEvent {
  kind: "owls.view.requested";
}

export interface OwlsViewDismissedEvent {
  kind: "owls.view.dismissed";
}

export interface OwlChangedEvent {
  kind: "owl.changed";
  owlName: string;
  owlEmoji: string;
}

// ─── Skills ───────────────────────────────────────────────────────────────────

export interface SkillSummaryRecord {
  name: string;
  description: string;
  enabled: boolean;
}

export interface SkillsLoadedEvent {
  kind: "skills.loaded";
  skills: SkillSummaryRecord[];
}

export interface SkillsViewRequestedEvent {
  kind: "skills.view.requested";
}

export interface SkillsViewDismissedEvent {
  kind: "skills.view.dismissed";
}

// ─── MCP ──────────────────────────────────────────────────────────────────────

export interface McpServerRecord {
  name: string;
  transport: string;
  connected: boolean;
  toolCount: number;
}

export interface McpStatusLoadedEvent {
  kind: "mcp.loaded";
  servers: McpServerRecord[];
}

export interface McpViewRequestedEvent {
  kind: "mcp.view.requested";
}

export interface McpViewDismissedEvent {
  kind: "mcp.view.dismissed";
}

// ─── Help overlay ─────────────────────────────────────────────────────────────

export interface HelpViewRequestedEvent {
  kind: "help.view.requested";
}

export interface HelpViewDismissedEvent {
  kind: "help.view.dismissed";
}

// ─── Union ────────────────────────────────────────────────────────────────────

export type UiEvent =
  | SessionChangedEvent
  | SessionsLoadedEvent
  | SessionsViewRequestedEvent
  | SessionsViewDismissedEvent
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
  | ParliamentViewRequestedEvent
  | ParliamentViewDismissedEvent
  | HeartbeatMessageEvent
  | NoticeEvent
  | OwlsLoadedEvent
  | OwlsViewRequestedEvent
  | OwlsViewDismissedEvent
  | OwlChangedEvent
  | SkillsLoadedEvent
  | SkillsViewRequestedEvent
  | SkillsViewDismissedEvent
  | McpStatusLoadedEvent
  | McpViewRequestedEvent
  | McpViewDismissedEvent
  | HelpViewRequestedEvent
  | HelpViewDismissedEvent;

export type UiEventKind = UiEvent["kind"];

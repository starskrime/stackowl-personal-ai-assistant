import type { ChatMessage } from "../providers/base.js";

export type SessionStatus =
  | "pending"
  | "running"
  | "awaiting_input"
  | "completed"
  | "terminated"
  | "failed";

export const TERMINAL_STATUSES: ReadonlySet<SessionStatus> = new Set([
  "completed",
  "terminated",
  "failed",
]);

export interface SessionMetadata {
  owl?: string;
  model?: string;
  channel?: string;
  userId?: string;
}

export interface Session {
  id: string;
  parentId: string | null;
  status: SessionStatus;
  prompt: string;
  history: ChatMessage[];
  result?: string;
  error?: string;
  metadata: SessionMetadata;
  createdAt: string;
  updatedAt: string;
  terminatedAt?: string;
}

export type MessageDirection = "to_session" | "from_session";

export interface SessionMessage {
  id: number;
  sessionId: string;
  direction: MessageDirection;
  content: string;
  createdAt: string;
  consumedAt?: string;
}

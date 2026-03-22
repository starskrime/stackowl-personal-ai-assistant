/**
 * StackOwl — Agent Communication Protocol (ACP) Types
 *
 * In-process typed message passing between agents.
 * Improvements over OpenClaw's HTTP-based ACP:
 * - Typed channels with generics (compile-time safety)
 * - Session bridging via lazy proxies (not full copies)
 * - Pull-based backpressure (bounded inbox per agent)
 * - Streaming via AsyncIterableIterator
 */

import type { ChatMessage } from "../providers/base.js";

// ─── Message Envelope ──────────────────────────────────────────

export interface ACPMessage<T = unknown> {
  /** Unique message ID */
  id: string;
  /** Sender agent ID */
  from: string;
  /** Recipient agent ID or capability query */
  to: string;
  /** Typed channel name */
  channel: string;
  /** The actual payload */
  payload: T;
  /** ID of message this replies to (for request/response) */
  replyTo?: string;
  /** Session reference for bridging context */
  sessionRef?: string;
  /** When this message was created */
  timestamp: number;
  /** Time-to-live in milliseconds. Message expires after this. */
  ttlMs?: number;
}

// ─── Channel Definition ────────────────────────────────────────

export interface ACPChannel {
  /** Unique channel name */
  name: string;
  /** Human-readable description */
  description: string;
  /** JSON Schema for input validation */
  inputSchema?: Record<string, unknown>;
  /** JSON Schema for output validation */
  outputSchema?: Record<string, unknown>;
}

// ─── Agent Capability Advertisement ────────────────────────────

export interface ACPCapability {
  /** Capability name (e.g. "code-review", "web-search", "cost-analysis") */
  name: string;
  /** ACP channels this agent listens on */
  channels: string[];
  /** Maximum concurrent messages this agent can handle */
  concurrency: number;
  /** Priority for routing ties (lower = preferred) */
  priority: number;
}

// ─── Session Bridge ────────────────────────────────────────────

export interface SessionBridge {
  /** The session being bridged */
  readonly sessionId: string;
  /** Get recent message history (lazy, not a full copy) */
  getHistory(limit?: number): Promise<ChatMessage[]>;
  /** Read a context value from the originating session */
  getContext(key: string): unknown;
  /** Write a context value (if permitted) */
  setContext(key: string, value: unknown): void;
  /** Read-only metadata from the originating session */
  readonly metadata: Record<string, unknown>;
}

export interface BridgePermissions {
  /** Can the receiving agent read message history? */
  readHistory: boolean;
  /** Can it access pellets from the session? */
  readPellets: boolean;
  /** Can it write context values? */
  writeContext: boolean;
  /** Max messages to expose in history */
  maxHistoryDepth: number;
}

// ─── Streaming ─────────────────────────────────────────────────

export interface ACPStreamWriter<T> {
  /** Write a chunk to the stream */
  write(chunk: T): void;
  /** Signal stream completion */
  end(): void;
  /** Signal stream error */
  error(err: Error): void;
}

export interface ACPStream<T = unknown> {
  /** Message ID this stream is associated with */
  readonly messageId: string;
  /** Sender agent ID */
  readonly from: string;
  /** Async iteration over stream chunks */
  [Symbol.asyncIterator](): AsyncIterableIterator<T>;
  /** Cancel the stream */
  cancel(): void;
}

// ─── Delivery Status ───────────────────────────────────────────

export type DeliveryStatus =
  | "delivered"
  | "rejected"
  | "expired"
  | "backpressure"
  | "not-found";

// ─── Message Handler ───────────────────────────────────────────

export type ACPMessageHandler<T = unknown> = (
  message: ACPMessage<T>,
  bridge?: SessionBridge,
) => Promise<unknown>;

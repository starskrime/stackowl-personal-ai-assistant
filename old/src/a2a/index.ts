/**
 * StackOwl — A2A (Agent-to-Agent) Barrel
 *
 * IMPORTANT: A2AMessage is intentionally NOT exported here.
 * It is an internal registry envelope; callers never construct messages
 * directly — they call registry.send() / registry.broadcast().
 *
 * Exported surface:
 *   Classes  : A2ARegistry
 *   Types    : A2AAgent, A2AContext, A2AResult, A2ABroadcastResult, SessionStore
 *   Errors   : A2ADuplicateAgentError, A2AAgentNotFoundError
 *   Enums    : A2AStatus
 */

export { A2ARegistry } from "./registry.js";

export type {
  A2AAgent,
  A2AContext,
  A2AResult,
  A2ABroadcastResult,
  A2AStatus,
  SessionStore,
} from "./types.js";

export { A2ADuplicateAgentError, A2AAgentNotFoundError } from "./types.js";

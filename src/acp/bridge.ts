/**
 * StackOwl — ACP Session Bridge
 *
 * Creates lazy session proxies so agents can share context
 * without copying the full session. Permissions control what
 * the receiving agent can see and do.
 */

import type { SessionBridge, BridgePermissions } from "./types.js";
import type { SessionStore } from "../memory/store.js";
import type { ChatMessage } from "../providers/base.js";

export class SessionBridgeFactory {
  constructor(
    private sessionStore: SessionStore,
    _pelletStore?: { search(query: string, limit: number): Promise<unknown[]> },
  ) {}

  /**
   * Create a scoped, read-mostly proxy into a session.
   * The bridge lazily fetches data on demand, not upfront.
   */
  createBridge(sessionId: string, permissions: BridgePermissions): SessionBridge {
    const store = this.sessionStore;
    const contextStore = new Map<string, unknown>();

    return {
      sessionId,

      async getHistory(limit?: number): Promise<ChatMessage[]> {
        if (!permissions.readHistory) return [];

        const session = await store.loadSession(sessionId);
        if (!session) return [];

        const maxDepth = Math.min(
          limit ?? permissions.maxHistoryDepth,
          permissions.maxHistoryDepth,
        );
        return session.messages.slice(-maxDepth);
      },

      getContext(key: string): unknown {
        return contextStore.get(key);
      },

      setContext(key: string, value: unknown): void {
        if (!permissions.writeContext) return;
        contextStore.set(key, value);
      },

      get metadata(): Record<string, unknown> {
        return {
          sessionId,
          readHistory: permissions.readHistory,
          readPellets: permissions.readPellets,
          writeContext: permissions.writeContext,
          maxHistoryDepth: permissions.maxHistoryDepth,
        };
      },
    };
  }
}

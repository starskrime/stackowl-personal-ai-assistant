/**
 * StackOwl — Agent Watch: Session Registry
 *
 * Tracks active agent sessions being supervised.
 * Maps: agentSessionId → { userId, channelId, allowlist, denylist, stats }
 */

import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface WatchSession {
  /** Agent's session ID (from the hook payload) */
  agentSessionId: string;
  /** StackOwl user to notify */
  userId: string;
  /** Telegram chat ID / channel */
  channelId: string;
  /** Tools auto-approved for this session ("always allow this session") */
  sessionAllowlist: Set<string>;
  /** Tools auto-denied for this session */
  sessionDenylist: Set<string>;
  /** Stats */
  stats: {
    approved: number;
    denied: number;
    autoApproved: number;
    autoDenied: number;
  };
  startedAt: number;
  lastActivityAt: number;
}

export interface RegisterOptions {
  userId: string;
  channelId: string;
  /** Optional: pre-set session token so Claude Code config can be generated before first hook fires */
  token?: string;
}

// ─── Registry ─────────────────────────────────────────────────────

export class SessionRegistry {
  /** agentSessionId → WatchSession */
  private sessions = new Map<string, WatchSession>();
  /** token → userId+channelId (for pre-registered sessions) */
  private tokens = new Map<string, { userId: string; channelId: string }>();

  /**
   * Register a watch token before the agent session starts.
   * The token is included in the generated settings.json hook config.
   */
  registerToken(token: string, userId: string, channelId: string): void {
    this.tokens.set(token, { userId, channelId });
    log.engine.info(`[SessionRegistry] Token registered for user ${userId}`);
  }

  /**
   * Called when the first hook arrives for a session.
   * Creates or updates the session record.
   */
  getOrCreate(agentSessionId: string, token: string): WatchSession | null {
    if (this.sessions.has(agentSessionId)) {
      return this.sessions.get(agentSessionId)!;
    }

    const owner = this.tokens.get(token);
    if (!owner) {
      log.engine.warn(
        `[SessionRegistry] Unknown token "${token}" — hook rejected`,
      );
      return null;
    }

    const session: WatchSession = {
      agentSessionId,
      userId: owner.userId,
      channelId: owner.channelId,
      sessionAllowlist: new Set(),
      sessionDenylist: new Set(),
      stats: { approved: 0, denied: 0, autoApproved: 0, autoDenied: 0 },
      startedAt: Date.now(),
      lastActivityAt: Date.now(),
    };

    this.sessions.set(agentSessionId, session);
    log.engine.info(
      `[SessionRegistry] Session started: ${agentSessionId} for user ${owner.userId}`,
    );
    return session;
  }

  get(agentSessionId: string): WatchSession | null {
    return this.sessions.get(agentSessionId) ?? null;
  }

  /** Add a tool to the session's allow/deny list (user said "yes all" or "no all") */
  addToAllowlist(agentSessionId: string, toolName: string): void {
    this.sessions.get(agentSessionId)?.sessionAllowlist.add(toolName);
  }

  addToDenylist(agentSessionId: string, toolName: string): void {
    this.sessions.get(agentSessionId)?.sessionDenylist.add(toolName);
  }

  /** Record decision in stats */
  recordDecision(
    agentSessionId: string,
    type: "approved" | "denied" | "autoApproved" | "autoDenied",
  ): void {
    const s = this.sessions.get(agentSessionId);
    if (s) {
      s.stats[type]++;
      s.lastActivityAt = Date.now();
    }
  }

  /** Remove a session (agent finished or user unwatched) */
  remove(agentSessionId: string): WatchSession | null {
    const s = this.sessions.get(agentSessionId) ?? null;
    this.sessions.delete(agentSessionId);
    return s;
  }

  /** All active sessions for a user */
  getForUser(userId: string): WatchSession[] {
    return [...this.sessions.values()].filter((s) => s.userId === userId);
  }

  getAllSessions(): WatchSession[] {
    return [...this.sessions.values()];
  }
}

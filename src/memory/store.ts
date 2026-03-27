/**
 * StackOwl — Memory Store
 *
 * Manages short-term conversation history (sessions) and
 * long-term persistent memory.
 */

import { join } from "node:path";
import { mkdir, readFile, writeFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import type { ChatMessage } from "../providers/base.js";

export interface Session {
  id: string;
  messages: ChatMessage[];
  metadata: {
    owlName: string;
    startedAt: number;
    lastUpdatedAt: number;
    title?: string;
  };
}

export class SessionStore {
  private sessionsDir: string;

  constructor(workspacePath: string) {
    this.sessionsDir = join(workspacePath, "sessions");
  }

  /** Ensure the sessions directory exists */
  async init(): Promise<void> {
    if (!existsSync(this.sessionsDir)) {
      await mkdir(this.sessionsDir, { recursive: true });
    }
  }

  /**
   * Create a new empty session.
   */
  createSession(owlName: string): Session {
    const id = `session_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    return {
      id,
      messages: [],
      metadata: {
        owlName,
        startedAt: Date.now(),
        lastUpdatedAt: Date.now(),
      },
    };
  }

  /**
   * Save a session to disk.
   */
  async saveSession(session: Session): Promise<void> {
    await this.init();
    const filePath = join(this.sessionsDir, `${session.id}.json`);
    session.metadata.lastUpdatedAt = Date.now();
    await writeFile(filePath, JSON.stringify(session, null, 2), "utf-8");
  }

  /**
   * Load a session from disk by ID.
   */
  async loadSession(sessionId: string): Promise<Session | null> {
    const filePath = join(this.sessionsDir, `${sessionId}.json`);
    if (!existsSync(filePath)) return null;

    try {
      const data = await readFile(filePath, "utf-8");
      return JSON.parse(data) as Session;
    } catch (error) {
      console.error(
        `[SessionStore] Failed to load session ${sessionId}:`,
        error,
      );
      return null;
    }
  }

  /**
   * List all available sessions, sorted by most recent first.
   */
  async listSessions(): Promise<Session[]> {
    await this.init();
    try {
      const files = await readdir(this.sessionsDir);
      const sessions: Session[] = [];

      for (const file of files) {
        if (file.endsWith(".json")) {
          const id = file.replace(".json", "");
          const session = await this.loadSession(id);
          if (session) sessions.push(session);
        }
      }

      return sessions.sort(
        (a, b) => b.metadata.lastUpdatedAt - a.metadata.lastUpdatedAt,
      );
    } catch (error) {
      console.error("[SessionStore] Failed to list sessions:", error);
      return [];
    }
  }

  /**
   * Get the most recent session for a specific owl, or create a new one.
   */
  async getRecentOrCreate(owlName: string): Promise<Session> {
    const sessions = await this.listSessions();
    const owlSessions = sessions.filter((s) => s.metadata.owlName === owlName);

    if (owlSessions.length > 0) {
      // Check if the latest session is less than 12 hours old
      const latest = owlSessions[0];
      const ageHours =
        (Date.now() - latest.metadata.lastUpdatedAt) / (1000 * 60 * 60);

      if (ageHours < 12) {
        return latest;
      }
    }

    return this.createSession(owlName);
  }
}

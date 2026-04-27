/**
 * StackOwl — CLI Session Persistence
 *
 * Persists CLI conversation history across session restarts.
 * Saves session to JSON file on quit and loads on startup.
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import type { ChatMessage } from "../providers/base.js";

export interface PersistedSession {
  sessionId: string;
  messages: ChatMessage[];
  owlName: string;
  createdAt: string;
  updatedAt: string;
  turnCount: number;
}

interface SessionPersistenceOptions {
  workspacePath: string;
}

export class SessionPersistence {
  private sessionsDir: string;
  private currentSession: PersistedSession | null = null;
  private autoSaveTimer: ReturnType<typeof setInterval> | null = null;
  private readonly AUTO_SAVE_INTERVAL_MS = 30_000;

  constructor(options: SessionPersistenceOptions) {
    this.sessionsDir = join(options.workspacePath, "cli-sessions");
    this._ensureDir();
  }

  private _ensureDir(): void {
    if (!existsSync(this.sessionsDir)) {
      mkdirSync(this.sessionsDir, { recursive: true });
    }
  }

  private _sessionFilePath(sessionId: string): string {
    const safeId = sessionId.replace(/[^a-z0-9_-]/gi, "_");
    return join(this.sessionsDir, `${safeId}.json`);
  }

  /**
   * Start a new session or resume an existing one.
   */
  async startSession(sessionId: string, owlName: string): Promise<PersistedSession | null> {
    const filePath = this._sessionFilePath(sessionId);

    if (existsSync(filePath)) {
      try {
        const raw = readFileSync(filePath, "utf-8");
        const session: PersistedSession = JSON.parse(raw);
        this.currentSession = session;
        this._startAutoSave();
        return session;
      } catch {
        return null;
      }
    }

    this.currentSession = {
      sessionId,
      messages: [],
      owlName,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      turnCount: 0,
    };
    this._startAutoSave();
    return null;
  }

  /**
   * Add a message to the current session.
   */
  addMessage(role: "user" | "assistant" | "system", content: string, label?: string): void {
    if (!this.currentSession) return;

    const msg: ChatMessage = { role, content };
    if (label && role === "assistant") {
      (msg as any).label = label;
    }
    this.currentSession.messages.push(msg);
    this.currentSession.updatedAt = new Date().toISOString();
  }

  /**
   * Increment turn count for the session.
   */
  incrementTurn(): void {
    if (!this.currentSession) return;
    this.currentSession.turnCount++;
    this.currentSession.updatedAt = new Date().toISOString();
  }

  /**
   * Get current session messages for context injection.
   */
  getMessages(): ChatMessage[] {
    return this.currentSession?.messages ?? [];
  }

  /**
   * Save session to disk immediately.
   */
  async save(): Promise<void> {
    if (!this.currentSession) return;
    const filePath = this._sessionFilePath(this.currentSession.sessionId);
    this.currentSession.updatedAt = new Date().toISOString();
    writeFileSync(filePath, JSON.stringify(this.currentSession, null, 2), "utf-8");
  }

  /**
   * Save and stop the current session.
   */
  async endSession(): Promise<void> {
    this._stopAutoSave();
    await this.save();
    this.currentSession = null;
  }

  /**
   * List all saved sessions (most recent first).
   */
  async listSessions(): Promise<PersistedSession[]> {
    const { readdirSync } = await import("node:fs");
    const files = readdirSync(this.sessionsDir).filter(f => f.endsWith(".json"));
    const sessions: PersistedSession[] = [];

    for (const file of files) {
      try {
        const raw = readFileSync(join(this.sessionsDir, file), "utf-8");
        const session: PersistedSession = JSON.parse(raw);
        sessions.push(session);
      } catch {
        // Skip corrupted session files
      }
    }

    return sessions.sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
    );
  }

  /**
   * Load a specific session by ID.
   */
  async loadSession(sessionId: string): Promise<PersistedSession | null> {
    const filePath = this._sessionFilePath(sessionId);
    if (!existsSync(filePath)) return null;

    try {
      const raw = readFileSync(filePath, "utf-8");
      return JSON.parse(raw) as PersistedSession;
    } catch {
      return null;
    }
  }

  /**
   * Delete a session by ID.
   */
  async deleteSession(sessionId: string): Promise<void> {
    const filePath = this._sessionFilePath(sessionId);
    const { unlinkSync } = await import("node:fs");
    try {
      unlinkSync(filePath);
    } catch {
      // File may not exist
    }
  }

  /**
   * Export session as JSON string for external tools.
   */
  exportSession(): string | null {
    if (!this.currentSession) return null;
    return JSON.stringify(this.currentSession, null, 2);
  }

  /**
   * Import session from JSON string.
   */
  async importSession(json: string): Promise<boolean> {
    try {
      const session: PersistedSession = JSON.parse(json);
      if (!session.sessionId || !Array.isArray(session.messages)) {
        return false;
      }
      this.currentSession = session;
      await this.save();
      return true;
    } catch {
      return false;
    }
  }

  private _startAutoSave(): void {
    this._stopAutoSave();
    this.autoSaveTimer = setInterval(() => {
      this.save().catch(() => {/* non-fatal */});
    }, this.AUTO_SAVE_INTERVAL_MS);
  }

  private _stopAutoSave(): void {
    if (this.autoSaveTimer) {
      clearInterval(this.autoSaveTimer);
      this.autoSaveTimer = null;
    }
  }

  get currentSessionId(): string | null {
    return this.currentSession?.sessionId ?? null;
  }
}
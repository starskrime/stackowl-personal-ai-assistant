import { randomUUID } from "node:crypto";
import {
  readFileSync,
  writeFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
} from "node:fs";
import { join } from "node:path";
import { Logger } from "../logger.js";
import type {
  SharedSession,
  CollabMessage,
  Participant,
  ParticipantRole,
  SessionSettings,
  CollabConfig,
} from "./types.js";

const log = new Logger("COLLAB");

const DEFAULT_CONFIG: CollabConfig = {
  maxActiveSessions: 5,
  sessionTimeoutMinutes: 120,
  maxMessagesPerSession: 200,
};

const DEFAULT_SETTINGS: SessionSettings = {
  maxParticipants: 10,
  allowObservers: true,
  roundRobin: false,
  decisionMode: "owner_decides",
  autoSummarize: true,
};

export class CollabSessionManager {
  private sessions = new Map<string, SharedSession>();
  private config: CollabConfig;
  private sessionsDir: string;

  constructor(workspacePath: string, config?: Partial<CollabConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.sessionsDir = join(workspacePath, "collab-sessions");
  }

  createSession(
    name: string,
    owlName: string,
    owner: { userId: string; displayName: string; channelId: string },
    settings?: Partial<SessionSettings>,
  ): SharedSession {
    if (this.sessions.size >= this.config.maxActiveSessions) {
      this.pruneStale();
      if (this.sessions.size >= this.config.maxActiveSessions) {
        throw new Error(
          `Maximum active sessions (${this.config.maxActiveSessions}) reached`,
        );
      }
    }

    const now = new Date().toISOString();
    const session: SharedSession = {
      id: randomUUID(),
      name,
      owlName,
      participants: [
        {
          userId: owner.userId,
          displayName: owner.displayName,
          role: "owner",
          joinedAt: now,
          lastActiveAt: now,
          channelId: owner.channelId,
        },
      ],
      messages: [],
      metadata: {
        createdAt: now,
        lastActivity: now,
      },
      settings: { ...DEFAULT_SETTINGS, ...settings },
    };

    this.sessions.set(session.id, session);
    this.saveSession(session);
    log.info(
      `Created collab session "${name}" (${session.id}) by ${owner.displayName}`,
    );
    return session;
  }

  joinSession(
    sessionId: string,
    user: { userId: string; displayName: string; channelId: string },
    role: ParticipantRole = "member",
  ): SharedSession {
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);

    const existing = session.participants.find((p) => p.userId === user.userId);
    if (existing) {
      existing.lastActiveAt = new Date().toISOString();
      return session;
    }

    if (!session.settings.allowObservers && role === "observer") {
      throw new Error("This session does not allow observers");
    }

    if (session.participants.length >= session.settings.maxParticipants) {
      throw new Error(
        `Session is full (max ${session.settings.maxParticipants})`,
      );
    }

    const now = new Date().toISOString();
    const participant: Participant = {
      userId: user.userId,
      displayName: user.displayName,
      role,
      joinedAt: now,
      lastActiveAt: now,
      channelId: user.channelId,
    };

    session.participants.push(participant);
    session.metadata.lastActivity = now;
    this.saveSession(session);
    log.info(`${user.displayName} joined session "${session.name}" as ${role}`);
    return session;
  }

  leaveSession(sessionId: string, userId: string): void {
    const session = this.sessions.get(sessionId);
    if (!session) return;

    session.participants = session.participants.filter(
      (p) => p.userId !== userId,
    );
    session.metadata.lastActivity = new Date().toISOString();

    if (session.participants.length === 0) {
      this.sessions.delete(sessionId);
      log.info(`Session "${session.name}" ended (all participants left)`);
    } else {
      // Transfer ownership if owner left
      if (!session.participants.some((p) => p.role === "owner")) {
        const newOwner = session.participants.find((p) => p.role === "member");
        if (newOwner) newOwner.role = "owner";
      }
      this.saveSession(session);
    }
  }

  addMessage(
    sessionId: string,
    message: Omit<CollabMessage, "id" | "timestamp">,
  ): CollabMessage {
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);

    if (session.messages.length >= this.config.maxMessagesPerSession) {
      throw new Error(
        `Session message limit (${this.config.maxMessagesPerSession}) reached`,
      );
    }

    const now = new Date().toISOString();
    const full: CollabMessage = {
      ...message,
      id: randomUUID(),
      timestamp: now,
    };

    session.messages.push(full);
    session.metadata.lastActivity = now;

    // Update participant activity
    const participant = session.participants.find(
      (p) => p.userId === message.userId,
    );
    if (participant) participant.lastActiveAt = now;

    this.saveSession(session);
    return full;
  }

  getSession(sessionId: string): SharedSession | null {
    return this.sessions.get(sessionId) ?? null;
  }

  listSessions(): SharedSession[] {
    return Array.from(this.sessions.values());
  }

  getUserSessions(userId: string): SharedSession[] {
    return this.listSessions().filter((s) =>
      s.participants.some((p) => p.userId === userId),
    );
  }

  endSession(sessionId: string): SharedSession {
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);

    this.saveSession(session);
    this.sessions.delete(sessionId);
    log.info(`Session "${session.name}" ended`);
    return session;
  }

  buildCollabContext(sessionId: string): string {
    const session = this.sessions.get(sessionId);
    if (!session) return "";

    const participantCount = session.participants.filter(
      (p) => p.role !== "observer",
    ).length;
    const lines: string[] = [
      `<collaborative_session name="${session.name}" participants="${participantCount}">`,
      "  <participants>",
    ];

    for (const p of session.participants) {
      const expertise = p.expertise?.length
        ? ` expertise="${p.expertise.join(", ")}"`
        : "";
      const suffix = p.role === "observer" ? " (observing)" : "";
      lines.push(
        `    <user id="${p.userId}" role="${p.role}"${expertise}>${p.displayName}${suffix}</user>`,
      );
    }

    lines.push("  </participants>");
    lines.push("  <instructions>");
    lines.push(
      "    This is a multi-user collaborative session. Address participants by name.",
    );
    lines.push("    When users disagree, help facilitate discussion.");
    lines.push(`    Decision mode: ${session.settings.decisionMode}.`);
    lines.push("    Tailor explanations to each user's expertise level.");

    if (session.settings.roundRobin) {
      lines.push("    Respond to each participant in turn (round-robin mode).");
    }

    lines.push("  </instructions>");
    lines.push("</collaborative_session>");

    return lines.join("\n");
  }

  pruneStale(): void {
    const cutoff = Date.now() - this.config.sessionTimeoutMinutes * 60_000;
    const stale: string[] = [];

    for (const [id, session] of this.sessions) {
      if (new Date(session.metadata.lastActivity).getTime() < cutoff) {
        stale.push(id);
      }
    }

    for (const id of stale) {
      const session = this.sessions.get(id);
      this.sessions.delete(id);
      if (session) log.info(`Pruned stale session: "${session.name}"`);
    }
  }

  saveSession(session: SharedSession): void {
    try {
      if (!existsSync(this.sessionsDir))
        mkdirSync(this.sessionsDir, { recursive: true });
      const filePath = join(this.sessionsDir, `${session.id}.json`);
      writeFileSync(filePath, JSON.stringify(session, null, 2), "utf-8");
    } catch (err) {
      log.error(`Failed to save collab session: ${err}`);
    }
  }

  loadAll(): void {
    if (!existsSync(this.sessionsDir)) return;

    try {
      const files = readdirSync(this.sessionsDir).filter((f) =>
        f.endsWith(".json"),
      );
      for (const file of files) {
        const raw = readFileSync(join(this.sessionsDir, file), "utf-8");
        const session = JSON.parse(raw) as SharedSession;
        this.sessions.set(session.id, session);
      }
      log.info(`Loaded ${this.sessions.size} collab session(s)`);
    } catch (err) {
      log.warn(`Failed to load collab sessions: ${err}`);
    }
  }
}

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { SessionPersistence, type PersistedSession } from "../../src/cli/session-persistence.js";
import { readFileSync, writeFileSync, existsSync, unlinkSync, readdirSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const TEST_DIR = "/tmp/stackowl-test-sessions-epic8";

function cleanDir(): void {
  try {
    const sessionsDir = join(TEST_DIR, "cli-sessions");
    const files = readdirSync(sessionsDir);
    for (const file of files) {
      unlinkSync(join(sessionsDir, file));
    }
  } catch {
    // dir may not exist
  }
}

describe("SessionPersistence", () => {
  let persistence: SessionPersistence;

  beforeEach(() => {
    try { mkdirSync(join(TEST_DIR, "cli-sessions"), { recursive: true }); } catch {}
    cleanDir();
    persistence = new SessionPersistence({ workspacePath: TEST_DIR });
  });

  afterEach(() => {
    cleanDir();
  });

  describe("startSession", () => {
    it("creates new session when none exists", async () => {
      const result = await persistence.startSession("cli-local", "Noctua");
      expect(result).toBeNull();
      expect(persistence.currentSessionId).toBe("cli-local");
    });

    it("loads existing session if found", async () => {
      const existing: PersistedSession = {
        sessionId: "cli-local",
        messages: [{ role: "user", content: "hello" }],
        owlName: "Noctua",
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        turnCount: 1,
      };
      const sessionsDir = join(TEST_DIR, "cli-sessions");
      writeFileSync(join(sessionsDir, "cli-local.json"), JSON.stringify(existing));

      persistence = new SessionPersistence({ workspacePath: TEST_DIR });
      const result = await persistence.startSession("cli-local", "Noctua");

      expect(result).not.toBeNull();
      expect(result!.messages).toHaveLength(1);
      expect(result!.owlName).toBe("Noctua");
    });

    it("returns null for corrupted session files", async () => {
      const sessionsDir = join(TEST_DIR, "cli-sessions");
      writeFileSync(join(sessionsDir, "cli-local.json"), "not valid json");

      persistence = new SessionPersistence({ workspacePath: TEST_DIR });
      const result = await persistence.startSession("cli-local", "Noctua");
      expect(result).toBeNull();
    });
  });

  describe("addMessage", () => {
    it("adds user message to session", async () => {
      await persistence.startSession("cli-local", "Noctua");
      persistence.addMessage("user", "hello");

      const msgs = persistence.getMessages();
      expect(msgs).toHaveLength(1);
      expect(msgs[0]!.role).toBe("user");
      expect(msgs[0]!.content).toBe("hello");
    });

    it("adds assistant message with label", async () => {
      await persistence.startSession("cli-local", "Noctua");
      persistence.addMessage("assistant", "hi there", "🦉 Noctua");

      const msgs = persistence.getMessages();
      expect(msgs).toHaveLength(1);
      expect(msgs[0]!.content).toBe("hi there");
      expect((msgs[0] as any).label).toBe("🦉 Noctua");
    });

    it("does nothing without active session", () => {
      persistence.addMessage("user", "test");
      expect(persistence.getMessages()).toHaveLength(0);
    });
  });

  describe("save and load", () => {
    it("persists session to disk", async () => {
      await persistence.startSession("cli-local", "Noctua");
      persistence.addMessage("user", "test message");
      persistence.incrementTurn();
      await persistence.save();

      const sessionsDir = join(TEST_DIR, "cli-sessions");
      const filePath = join(sessionsDir, "cli-local.json");
      expect(existsSync(filePath)).toBe(true);

      const raw = readFileSync(filePath, "utf-8");
      const session: PersistedSession = JSON.parse(raw);
      expect(session.messages).toHaveLength(1);
      expect(session.turnCount).toBe(1);
    });

    it("loads session via loadSession", async () => {
      await persistence.startSession("cli-local", "Noctua");
      persistence.addMessage("user", "saved message");
      await persistence.save();

      const loaded = await persistence.loadSession("cli-local");
      expect(loaded).not.toBeNull();
      expect(loaded!.messages).toHaveLength(1);
    });

    it("returns null for non-existent session", async () => {
      const loaded = await persistence.loadSession("non-existent");
      expect(loaded).toBeNull();
    });
  });

  describe("endSession", () => {
    it("saves and clears current session", async () => {
      await persistence.startSession("cli-local", "Noctua");
      persistence.addMessage("user", "test");
      await persistence.endSession();

      expect(persistence.currentSessionId).toBeNull();
      const loaded = await persistence.loadSession("cli-local");
      expect(loaded).not.toBeNull();
    });
  });

  describe("listSessions", () => {
    it("returns sessions sorted by updatedAt descending", async () => {
      await persistence.startSession("session-1", "Noctua");
      await persistence.save();
      await persistence.endSession();

      persistence = new SessionPersistence({ workspacePath: TEST_DIR });
      await new Promise(r => setTimeout(r, 10));
      await persistence.startSession("session-2", "Archimedes");
      await persistence.save();
      await persistence.endSession();

      const sessions = await persistence.listSessions();
      expect(sessions).toHaveLength(2);
      expect(sessions[0]!.sessionId).toBe("session-2");
      expect(sessions[1]!.sessionId).toBe("session-1");
    });
  });

  describe("deleteSession", () => {
    it("deletes session file", async () => {
      await persistence.startSession("cli-local", "Noctua");
      await persistence.save();
      await persistence.deleteSession("cli-local");

      const loaded = await persistence.loadSession("cli-local");
      expect(loaded).toBeNull();
    });
  });

  describe("export/import", () => {
    it("exports current session as JSON", async () => {
      await persistence.startSession("cli-local", "Noctua");
      persistence.addMessage("user", "export me");
      const json = persistence.exportSession();

      expect(json).not.toBeNull();
      const parsed = JSON.parse(json!);
      expect(parsed.messages).toHaveLength(1);
    });

    it("imports session from JSON", async () => {
      const json = JSON.stringify({
        sessionId: "imported-session",
        messages: [{ role: "user", content: "imported" }],
        owlName: "Noctua",
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        turnCount: 0,
      });

      const success = await persistence.importSession(json);
      expect(success).toBe(true);
      expect(persistence.currentSessionId).toBe("imported-session");
    });

    it("returns false for invalid JSON import", async () => {
      const success = await persistence.importSession("not valid json");
      expect(success).toBe(false);
    });
  });
});
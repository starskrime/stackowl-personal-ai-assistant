import { SessionStore } from "../memory/store.js";
import { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";

export async function migrateJsonSessionsToSQLite(
  sessionStore: SessionStore,
  db: MemoryDatabase,
  owlName: string,
): Promise<void> {
  const sessions = await sessionStore.listSessions();

  for (const session of sessions) {
    const sessionId = session.id;

    const existing = db.messages.countSession(sessionId);
    if (existing > 0) {
      await sessionStore.deleteSession(sessionId);
      continue;
    }

    if (session.messages.length === 0) {
      await sessionStore.deleteSession(sessionId);
      continue;
    }

    // extract userId from sessionId (format: "channelId:userId")
    const parts = sessionId.split(":");
    const userId = parts.length >= 2 ? parts.slice(1).join(":") : sessionId;

    db.messages.append(sessionId, userId, owlName, session.messages);
    await sessionStore.deleteSession(sessionId);

    log.engine.info(`[Migration] Migrated session ${sessionId} — ${session.messages.length} messages`);
  }
}

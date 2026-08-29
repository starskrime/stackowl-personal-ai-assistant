-- FTS5 over session messages, kept in sync by TRIGGERS.
--
-- D11.2. session_search's `discover` mode built ONE substring pattern from the
-- whole query — `LIKE '%<entire query>%'` — so a multi-word query could only
-- match when those exact characters were adjacent. Measured 2026-08-29: of six
-- real `discover` calls, THREE returned zero rows. Substring is not relevance.
--
-- TRIGGERS, NOT APPLICATION WRITES, AND THIS TREE ALREADY SHOWS WHY. There are
-- exactly two triggers in this database today and both guard audit_log; every
-- existing FTS mirror is a standalone fts5 table synced by application code.
-- One of them is already dead: committed_facts_fts holds 0 rows while its shadow
-- tables still carry 1,112 — a stale index of content that no longer exists,
-- because D08.1 removed the writer and nothing told the mirror. A mirror that
-- depends on someone remembering to write to it WILL drift.
--
-- external content (content='messages') so the text is stored ONCE. The FTS table
-- holds only the index; `messages` stays the single source of truth, which is the
-- same "one store" boundary session_search's docstring set out to protect when it
-- chose LIKE over a second store. This is an index over the existing store, not a
-- second store.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='rowid'
);

-- Backfill what is already there. rowid is stable for existing rows.
INSERT INTO messages_fts (rowid, content)
    SELECT rowid, content FROM messages WHERE content IS NOT NULL;

-- Keep it in sync. The 'delete' command rows are how external-content FTS5 is
-- told to forget a row; without them a deleted or edited message would keep
-- matching for ever.
CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts (rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
    INSERT INTO messages_fts (rowid, content) VALUES (new.rowid, new.content);
END;

-- D01.5 — enforce at the boundary what the code already guarantees by interface.
--
-- WHY. the reference platform incident #48879: an interrupted turn persists a `tool -> user`
-- tail that strict providers (Gemini, Claude) reject, continuing the user's
-- message instead of answering it. StackOwl cannot produce that shape in
-- PERSISTED history — TranscriptStore.record_turn has no `role` parameter at
-- all (it takes user_text/assistant_text and hardcodes the roles), and there is
-- exactly ONE INSERT into this table with exactly ONE caller.
--
-- BUT THAT GUARANTEE IS AN INTERFACE, NOT A CONSTRAINT. The CHECK permitted
-- 'system' and 'tool' as well, so a second writer — or a `role` parameter added
-- to record_turn in good faith — would reintroduce the incident silently, and
-- the database would accept every row. Bakir's call (2026-07-29) was to enforce
-- it where it cannot be bypassed rather than rely on there continuing to be one
-- writer.
--
-- WHY NOT A TEST INSTEAD. A test asserting "no stored role is outside
-- {user, assistant}" is an assertion that CANNOT FAIL: it passes vacuously today
-- and would keep passing after a regression until someone happened to write a
-- tool row. That is the D01.1 trap this program has paid for more than once. The
-- constraint fails LOUD at the moment of the bad write instead.
--
-- SAFE: verified before writing this — 42 rows, 0 of which violate the tighter
-- CHECK; no other table's DDL REFERENCES messages; no triggers on it. The
-- inbound-FK check matters because SQLite rewrites other tables' REFERENCES
-- clauses during ALTER TABLE ... RENAME, so a rebuild with inbound references is
-- a different and riskier operation than this one.
--
-- The runner wraps every migration in BEGIN EXCLUSIVE with ROLLBACK on failure,
-- so this is atomic: the table is either fully rebuilt or untouched.
--
-- Column definitions are copied VERBATIM from the live schema, including the
-- outbound FK to conversations(id); the only change is the role CHECK.

CREATE TABLE messages_new (
    id              TEXT    NOT NULL PRIMARY KEY,
    conversation_id TEXT    NOT NULL REFERENCES conversations(id),
    role            TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT    NOT NULL,
    token_count     INTEGER,
    model           TEXT,
    created_at      TEXT    NOT NULL,
    trace_id        TEXT,
    owner_id        TEXT    NOT NULL DEFAULT 'principal-default'
);

-- Explicit column list, not SELECT *, so a future column added to one table and
-- not the other fails loudly here rather than silently shifting values.
INSERT INTO messages_new (
    id, conversation_id, role, content, token_count, model, created_at,
    trace_id, owner_id
)
SELECT
    id, conversation_id, role, content, token_count, model, created_at,
    trace_id, owner_id
FROM messages;

DROP TABLE messages;

ALTER TABLE messages_new RENAME TO messages;

-- Recreated verbatim — a rebuild drops them with the old table.
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_owner        ON messages(owner_id);

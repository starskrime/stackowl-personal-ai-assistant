-- Migration 0141 the compaction that is REMEMBERED.
--
-- Bakir, 2026-09-09: "platfomr does not remember when last time did compaction and
-- now doing for my each request." He was right and the evidence is total. MEASURED
-- across every retained log: 34 compression events over six days and
-- had_prior_summary=false on EVERY ONE. Never once true.
--
-- IT WAS A READ WITH NO WRITER. conversation_compressor's Selection.prior_summary is
-- read by select(), branched on by apply() and reported by classify -- and outside the
-- compressor module the string appears in exactly ONE place in src/, the log line that
-- says whether there was one. The reuse path is reachable code with no producer.
--
-- WHY IT COULD NOT BE WIRED WITHOUT THIS TABLE. select() expects the summary to arrive
-- as a message carrying SUMMARY_MARKER inside the history it is handed, but history
-- comes from recent_conversation_turns, whose unit is a USER/ASSISTANT TURN PAIR -- and
-- a summary is neither half of a turn. There was nowhere in the store's shape to put
-- one, so the feature could be designed and could not be wired.
--
-- KEYED BY scope_key, THE SAME KEY THE TURNS USE. Conversation turns are filed under
-- owner_scope_key(state) -- identity_key or session_key -- and that string is the
-- isolation boundary for this subsystem today. A summary of a conversation belongs in
-- exactly the same bucket as the conversation. An owner_id column would be worse than
-- useless here: SqliteMemoryBridge is constructed without one and could not populate
-- it, so the column would be a scoping claim nothing enforces.
--
-- NO covered_through COLUMN, deliberately. The first draft had one -- the newest turn
-- timestamp a summary accounts for, so a later pass could trim already-represented
-- turns out of the raw read. Nothing in this change would have written a real value
-- into it, and a column with no meaningful writer is the EXACT defect this migration
-- exists to fix, one table over. It goes in when the trimming step is built and can
-- populate it honestly.
-- NOTE no semicolons inside comments per the runner split gotcha.

CREATE TABLE IF NOT EXISTS conversation_summaries (
    scope_key  TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

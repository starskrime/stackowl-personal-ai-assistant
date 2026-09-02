-- Migration 0131 — delete machine text that was filed as things the user SAID.
--
-- Bakir, 2026-09-02: "Delete them also find the root issue why these garbage was
-- written to memory."
--
-- THE ROOT ISSUE, and it is already fixed. Conversation persistence keyed on "a
-- turn happened", not on "a HUMAN spoke". The RCA pipeline runs its stages as
-- ordinary turns, so each stage's synthetic prompt became the turn's input_text
-- and was written into the conversation store as user speech. Same for retry
-- carry-forward text and incident verdicts. Measured: the newest such row is
-- 2026-08-31T10:16:26 while staged_facts runs to 2026-09-02T01:25:56 — nothing
-- new since the writer was closed on 2026-08-31. These 141 rows are residue.
--
-- WHY IT MATTERED, and this is the reach: staged_facts is what
-- `recent_conversation_turns` reads for the short-term history block. So a
-- secretary conversation could be handed "User: You are the VERIFIER owl in a
-- fixed-stage incident root-cause analysis..." as something the operator had
-- said, and answer it.
--
-- THREE PROVABLE SHAPES, measured before deleting (141 of 369 rows):
--     5  RCA stage prompts  — "You are the {EVIDENCE-GATHERER|HYPOTHESIS|
--        VERIFIER} in a fixed-stage incident root-cause analysis". No human types
--        this; it is a system prompt.
--   102  retry carry-forward — "Retry attempt N. What happened last time...".
--    50  incident verdicts   — "Incident: web_fetch failed with stop — ...".
--   (the shapes overlap; 141 is the union.)
--
-- WHAT IS DELIBERATELY LEFT: 228 rows, including job-search prompts of the form
-- "User: Search LinkedIn for ...". Those are AMBIGUOUS — they read identically
-- whether the operator typed them or a delegated task generated them — and this
-- programme does not delete the operator's memory on a guess. They are reported,
-- not purged.

DELETE FROM staged_facts
WHERE content LIKE 'User: You are the %fixed-stage incident root-cause analysis%'
   OR content LIKE '%Retry attempt%'
   OR content LIKE '%What happened last time%'
   OR content LIKE 'Incident: %';

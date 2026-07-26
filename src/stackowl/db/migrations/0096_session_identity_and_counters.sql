-- D01.7 slice 3b part 4 — the lane row tells the truth.
--
-- TWO PROBLEMS, ONE MIGRATION.
--
-- 1. turn_count COUNTED NOTHING. It was minted at 0, reset to 0 on every
--    rollover, persisted, read back, and PUBLISHED IN THE session.rollover
--    PAYLOAD — and no code path anywhere ever incremented it. Live proof at the
--    time of writing: the only existing lane read turn_count=0 while its
--    transcript held 4 messages. Any consumer gating on it read a constant, and
--    the structural notability gate originally designed for the rollover summary
--    would have been a permanent no-op.
--
--    It is RENAMED rather than repaired because the column is three days old
--    (0092), nothing outside src/stackowl/sessions/ reads it, and this whole item
--    exists because an identifier whose name lied about what it held cost a
--    571-file rename. Two counters replace it, because the DIFFERENCE between
--    them is the signal: message_count rises on every inbound message, and
--    completed_turns only when a turn produced a reply. A lane where the two
--    diverge is a lane that is failing.
--
-- 2. THE LANE DID NOT KNOW WHO IT BELONGED TO. Durable knowledge in this codebase
--    is filed under the PERSON (see pipeline/services.py owner_scope_key), not
--    under the owl-prefixed lane — telling Brain your timezone and having Scout
--    not know it is the failure that rule exists to prevent. The rollover summary
--    must therefore be staged under the identity, and the background sweeper that
--    fires it runs at 4 AM with NO ingress context to re-derive one from. So the
--    identity is stamped onto the lane at ingress, where it is already resolved,
--    and read back by whoever needs it later.
--
-- NULL identity_key is a real answer, not a missing one: a channel with no
-- resolver, or a runner lane with no person behind it, has no identity, and
-- fabricating one would misattribute somebody's memory.

ALTER TABLE sessions RENAME COLUMN turn_count TO message_count;
ALTER TABLE sessions ADD COLUMN completed_turns INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN identity_key TEXT;

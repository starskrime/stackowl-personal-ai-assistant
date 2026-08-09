-- D01.7 slice 3b part 7 — a runner lane remembers the conversation that asked.
--
-- Q9: "EVERY runner gets a lane — cron jobs, delegated subagents, objectives. One
-- rule, no special cases." Until now `resolve_for` had exactly ONE caller, the chat
-- ingress, so background work had no lane, no incarnation, no frozen prompt and no
-- boundary.
--
-- WHY WE DIVERGE HERE. the reference platform gives non-chat work no lane at all:
-- delegated runs inherit the ambient session key and cron is an external service
-- that delivers into a chat. We give autonomous work its OWN lane so it earns its
-- own stable prompt — the D01.1 win their model forfeits — and this column keeps
-- the story whole by linking it back to the conversation that asked for it, so the
-- rollover summary is attributed to the parent rather than fragmenting into a
-- separate memory nobody connects.
--
-- Both halves of Bakir's answer therefore hold at once: own lane (Q17) AND one
-- summary covering the work it spawned (Q19), at the cost of one column.
--
-- NULL means the runner has no originating conversation — a cron job nobody asked
-- for. That is the honest reading, and inventing a parent would misattribute the
-- summary to a conversation that never requested the work.

ALTER TABLE sessions ADD COLUMN parent_session_key TEXT;

-- Attribution walks child -> parent when a summary is filed.
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions (parent_session_key);

-- D01.6 — turn metrics. Five columns on cost_records, all defaulted so the
-- migration is idempotent and every pre-existing row stays valid.
--
-- These five answer the four questions in docs/hermes-mapping/designs/D01.6.md:
--   1. is the prefix cache working?  cached_input_tokens / input_tokens
--   2. does it feel fast?            ttft_ms
--   3. what does a conversation cost? SUM(cost_usd) GROUP BY session_id
--   4. is the prompt actually stable? COUNT(DISTINCT prompt_hash) per session_id
--
-- (4) is the pass/fail invariant for D01.1: within one session it must be 1.
-- Today it will equal the turn count, which IS the CONFLICT, measured.
--
-- NOTE: cached_input_tokens = 0 is ambiguous BY CONSTRUCTION — it means "no cache
-- hit" OR "provider did not report". Readers must distinguish the two (D01.6 I4);
-- the rows_reporting count in the report query is how.

ALTER TABLE cost_records ADD COLUMN session_id TEXT NOT NULL DEFAULT '';
ALTER TABLE cost_records ADD COLUMN cached_input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cost_records ADD COLUMN prompt_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE cost_records ADD COLUMN system_prompt_chars INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cost_records ADD COLUMN ttft_ms INTEGER;

-- Per-conversation aggregation is the common read; without this it is a full scan.
CREATE INDEX IF NOT EXISTS ix_cost_records_session ON cost_records (session_id);

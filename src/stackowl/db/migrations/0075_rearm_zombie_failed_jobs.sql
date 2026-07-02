-- Migration 0075 — re-arm pre-F-60 zombie recurring jobs (FR-11/12 item 3).
--
-- F-60 (commit f6604c4b) changed _mark_failed so a recurring job re-arms to
-- 'pending' on retry exhaustion instead of dying to terminal 'failed'. That
-- fix is forward-only: rows that went terminal under the OLD behavior, before
-- F-60 landed, are permanently stuck even though the code path that revives
-- their kind now exists. Distinguish a genuine pre-F-60 zombie from a
-- legitimately circuit-broken owl job (S11c) or a correctly-terminal one-shot
-- by TWO signals, both required:
--   1. recurring (params.run_once absent/false -- mirrors _is_recurring in
--      scheduler.py)
--   2. NO audit_log row at all for this job_id from either terminal-transition
--      event ('job_failed_terminal' one-shot death, 'owl_job_circuit_broken'
--      S11c pause) -- every _mark_failed transition since F-60 writes one of
--      these, so a legitimately-terminal row always has one; a zombie has
--      none.
--
-- Idempotent: re-running only ever matches rows still status='failed' with no
-- audit trail; nothing to do on a second run.
UPDATE jobs
SET status = 'pending',
    retry_count = 0,
    retry_at = NULL
WHERE status = 'failed'
  AND enabled = 1
  AND COALESCE(json_extract(params, '$.run_once'), 0) = 0
  AND job_id NOT IN (
      SELECT target FROM audit_log
      WHERE event_type IN ('job_failed_terminal', 'owl_job_circuit_broken')
        AND target IS NOT NULL
  )

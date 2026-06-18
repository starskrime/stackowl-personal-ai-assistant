-- Migration 0058 contradiction-scan boundary fact_ids (C3 review fast-follow).
--
-- The watermark stored max(committed_at) of the scanned batch and the scan
-- filtered committed_at > since (STRICT). committed_at is millisecond precision
-- and SQLite 'now' is constant within a statement, so a later promotion landing
-- in the SAME millisecond as a prior already-watermarked batch was PERMANENTLY
-- skipped -- a contradiction blind spot.
--
-- Fix: the scan now uses committed_at >= since AND excludes the fact_ids already
-- processed at the boundary timestamp. This column persists those boundary ids
-- (a JSON array of the fact_ids whose committed_at == the watermark) so a later
-- same-ms fact IS scanned while the boundary pair is never re-emitted. Advanced
-- atomically with the watermark, so crash-safety is preserved (both move only
-- after the scan results are recorded). NULL/absent = no boundary exclusions.
--
-- Idempotent: ALTER ... ADD COLUMN guarded by a column-existence check is not
-- portable in plain SQL, so we rely on the runner applying each migration once.
-- NOTE no semicolons inside comments per the runner split gotcha.

ALTER TABLE contradiction_scan_state ADD COLUMN boundary_fact_ids TEXT;

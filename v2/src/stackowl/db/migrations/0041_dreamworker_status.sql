-- DreamWorker failure tracker (reliability upgrade). Records per-run terminal
-- status and error so a failed consolidation pass is observable, not just
-- inferred from phase counters. stuck_eligible records how many eligible
-- memories were still staged after the pass (outcome verification signal).
-- Additive ALTERs. The runner applies this once (tracked in schema_migrations).
ALTER TABLE dreamworker_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'running';
ALTER TABLE dreamworker_runs ADD COLUMN error TEXT;
ALTER TABLE dreamworker_runs ADD COLUMN stuck_eligible INTEGER NOT NULL DEFAULT 0;

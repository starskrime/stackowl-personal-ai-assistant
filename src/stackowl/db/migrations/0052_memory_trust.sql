-- Migration 0052 memory trust provenance
-- 3-tier trust (trusted/self/untrusted) on staged + committed facts
-- Additive ADD COLUMN (a new column has no source_type CHECK to alter,
-- unlike 0036/0039). Legacy rows backfill to 'untrusted' (fail-safe:
-- unknown provenance is fenced, never grandfathered trusted).
-- Enum enforced in Python (memory/trust.py)
-- No SQL CHECK keeps ADD COLUMN trivially idempotent
ALTER TABLE staged_facts    ADD COLUMN trust TEXT NOT NULL DEFAULT 'untrusted';
ALTER TABLE committed_facts ADD COLUMN trust TEXT NOT NULL DEFAULT 'untrusted';

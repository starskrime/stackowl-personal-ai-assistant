-- Migration 0057 contradiction-scan watermark (C3 / F063).
--
-- The contradiction scan was O(n^2) over the FULL committed_facts corpus every
-- run (load_committed_for_scan had no WHERE/LIMIT) -- every embedding BLOB into
-- RAM, ~50M cosine ops at 10k facts. The fix bounds the LEFT side of the scan to
-- facts committed AFTER a watermark, while an ANN candidate lookup supplies the
-- RIGHT side (so a new fact is still compared against the WHOLE corpus).
--
-- This one-row table holds the high-water mark (the committed_at of the newest
-- fact scanned). It is advanced ONLY after the scan commits, so a crash mid-run
-- re-scans the same window rather than skipping unscanned facts (which would be a
-- permanent contradiction blind spot). NULL/absent = never scanned = scan all.
--
-- Idempotent: CREATE IF NOT EXISTS + a single seeded row keyed on a constant id.
-- NOTE no semicolons inside comments per the runner split gotcha.

CREATE TABLE IF NOT EXISTS contradiction_scan_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_contradiction_scan_at TEXT
);

INSERT OR IGNORE INTO contradiction_scan_state (id, last_contradiction_scan_at)
VALUES (1, NULL);

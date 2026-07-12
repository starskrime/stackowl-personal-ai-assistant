-- Migration 0083: add approach_rating to task_outcomes
-- Telegram Like/Dislike buttons rate the turn's APPROACH (tool choice,
-- reasoning path), not the output content. Nullable, default NULL: only
-- stamped when the user taps a button; historical rows stay NULL/unrated.
ALTER TABLE task_outcomes ADD COLUMN approach_rating TEXT
    CHECK (approach_rating IN ('positive', 'negative') OR approach_rating IS NULL);

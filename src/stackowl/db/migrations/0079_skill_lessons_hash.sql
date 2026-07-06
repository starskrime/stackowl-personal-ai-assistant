-- Migration 0079: add lessons_published_hash to skills
-- Root cause: SkillsAssembly._publish_to_lessons re-embeds + re-publishes every
-- loaded skill into the LessonsIndex on EVERY boot with no cache check (unlike
-- its sibling _embed_missing, which already skips unchanged skills via a
-- stored hash). On this box that costs ~24s of uncached local embedding
-- inference per boot, doubled by the gateway/core split (~48s total) for
-- content that hasn't changed since the last boot. Nullable, default NULL:
-- existing rows re-publish once more on the first post-migration boot, then
-- cache going forward.
ALTER TABLE skills ADD COLUMN lessons_published_hash TEXT

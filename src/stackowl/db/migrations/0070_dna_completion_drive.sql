-- Migration 0070 add completion_drive trait column to the DNA stores (F-52)
-- Adds the persistence/initiative trait to every owl-DNA table so a tenacious
-- owl is distinguishable from a lazy one. DEFAULT 0.5 is the behaviour-neutral
-- value, so every existing row backfills to neutral and current owls are
-- unchanged until evolution drifts the trait.
-- NOTE no semicolons in comments per migration runner gotcha.

ALTER TABLE owl_dna ADD COLUMN completion_drive REAL NOT NULL DEFAULT 0.5;

ALTER TABLE owl_dna_authored ADD COLUMN completion_drive REAL NOT NULL DEFAULT 0.5;

ALTER TABLE dna_checkpoints ADD COLUMN completion_drive REAL NOT NULL DEFAULT 0.5;

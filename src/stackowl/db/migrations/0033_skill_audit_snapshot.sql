-- Migration 0033 add snapshot_json to skill_audit
-- Per operator vote in Learning Commit 3 sub-phase 3e — every write to a
-- non-builtin skill carries a snapshot of the directory contents (text files
-- only, capped at 256KB total per entry). Lets /skill restore <name> --version
-- <hash> reproduce the exact prior state. Stored as JSON object mapping
-- relative path -> file contents (utf-8). Empty {} is valid for enable/disable
-- ops that don't change content.
-- NOTE no semicolons in comments per migration runner gotcha.

ALTER TABLE skill_audit ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '{}';

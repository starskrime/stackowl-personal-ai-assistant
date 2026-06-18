-- PLUG-1 (E-PLUGIN) — record the verified integrity digest of an installed plugin
-- so a verified remote install (PLUG-2) is auditable and re-verifiable on re-hydrate
-- A locally-installed plugin has no remote digest, so DEFAULT '' backfills every
-- legacy row as the empty (unverified-local) sentinel which is correct
-- NOTE no semicolons in comments per migration runner gotcha
ALTER TABLE plugins ADD COLUMN sha256 TEXT NOT NULL DEFAULT ''

-- Migration 0072 add skill_ownership table for synth-skill ownership (PA4b)
-- A synthesized "learned" skill is born UNREACHABLE unless it is attached to an
-- owning owl manifest. This table is the DURABLE record of that attachment so it
-- survives the gateway/core split and process restarts. The boot hydrator
-- (skill_ownership.hydrate_skill_ownership) reads these rows and re-overlays the
-- skill name onto the live owl manifest, exactly like owl_dna -> dna_hydrator.
-- owner_id is part of the PK for tenant isolation (no cross-principal bleed).
-- NOTE no semicolons in comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS skill_ownership (
    owner_id    TEXT NOT NULL,
    owl_name    TEXT NOT NULL,
    skill_name  TEXT NOT NULL,
    attached_at REAL NOT NULL,
    PRIMARY KEY (owner_id, owl_name, skill_name)
);

-- Migration 0042 tenancy principals (Pass 1, owner-scoped persistence)
-- Creates the root ownership table. A principal is the owner of all user data
-- a single user, or a team. In single-user mode the whole system runs under
-- one stable principal 'principal-default', seeded idempotently below and
-- referenced as the owner_id column DEFAULT in migration 0043.
-- NOTE no semicolons inside comments per migration runner gotcha.

CREATE TABLE IF NOT EXISTS principals (
    principal_id    TEXT    PRIMARY KEY,
    principal_type  TEXT    NOT NULL,
    display_name    TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_principals_type ON principals(principal_type);

-- Seed the single-user default owner. INSERT OR IGNORE makes this idempotent
-- the PRIMARY KEY guarantees a re-run cannot create a duplicate. created_at is
-- a fixed epoch-zero ISO timestamp so the DDL stays deterministic (no runtime
-- now() is needed for the seed row the application can update display_name).
INSERT OR IGNORE INTO principals (principal_id, principal_type, display_name, created_at)
VALUES ('principal-default', 'user', 'Default Owner', '1970-01-01T00:00:00+00:00');

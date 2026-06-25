-- Migration 0066: Objective Manager — standing multi-turn objectives.
--
-- An objective is a persistent intent the assistant decomposes into ordered
-- sub-goals and works across many autonomous turns until done/blocked. Unlike a
-- one-shot scheduler goal (the `jobs` table), an objective holds state across
-- days: its sub-goals, their per-step status, and an activity-event log.
--
-- All three tables are owner-scoped (composite PK on owner_id) so they slot onto
-- OwnedRepository exactly like the durable `tasks` table.

CREATE TABLE IF NOT EXISTS objectives (
    objective_id      TEXT NOT NULL,
    owner_id          TEXT NOT NULL DEFAULT 'principal-default',
    intent            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',   -- active | blocked | done | abandoned
    channel           TEXT,                              -- originating channel (delivery context)
    target_channels   TEXT,                              -- JSON list[str]; NULL when empty
    target_addresses  TEXT,                              -- JSON dict channel->native target; NULL when empty
    blocker           TEXT,                              -- why blocked (awaiting an irreversible decision)
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (owner_id, objective_id)
);
CREATE INDEX IF NOT EXISTS idx_objectives_owner ON objectives(owner_id);
CREATE INDEX IF NOT EXISTS idx_objectives_status ON objectives(status);

CREATE TABLE IF NOT EXISTS objective_subgoals (
    subgoal_id    TEXT NOT NULL,
    owner_id      TEXT NOT NULL DEFAULT 'principal-default',
    objective_id  TEXT NOT NULL,
    position      INTEGER NOT NULL,                      -- ordering within the objective
    description   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',       -- pending | running | done | failed | blocked
    result        TEXT,                                  -- produced answer / failure reason
    task_id       TEXT,                                  -- durable task that ran this sub-goal
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (owner_id, subgoal_id)
);
CREATE INDEX IF NOT EXISTS idx_subgoals_objective
    ON objective_subgoals(owner_id, objective_id, position);

CREATE TABLE IF NOT EXISTS objective_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id      TEXT NOT NULL DEFAULT 'principal-default',
    objective_id  TEXT NOT NULL,
    at            TEXT NOT NULL,
    kind          TEXT NOT NULL,                         -- created|decomposed|subgoal_done|subgoal_failed|blocked|completed|...
    detail        TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objective_events
    ON objective_events(owner_id, objective_id, at);

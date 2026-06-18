-- Migration 0043 owner-scope retrofit (Pass 1, owner-scoped persistence)
--
-- FULL RETROFIT: every EXISTING user-DATA table gains an owner_id column that
-- points at a principals.principal_id (migration 0042). The column is added
-- with NOT NULL DEFAULT 'principal-default' so SQLite atomically backfills all
-- pre-existing rows to the single-user default owner. This is purely additive
-- existing queries that never mention owner_id keep returning the same rows, so
-- behavior is unchanged until a later pass actually scopes reads by owner.
--
-- Shareable entities (owls, skills) ALSO gain visibility TEXT NOT NULL DEFAULT
-- 'private' so a future pass can mark an owl/skill shared without a schema
-- change.
--
-- SQLite forbids a non-constant DEFAULT, so the literal 'principal-default'
-- MUST equal tenancy.principal.DEFAULT_PRINCIPAL_ID forever (see that module).
-- The runner applies a migration once (tracked in schema_migrations), so plain
-- ADD COLUMN DDL is safe no IF NOT EXISTS exists for columns and is not needed.
-- NOTE no semicolons inside comments per migration runner gotcha.
--
-- ============================ CLASSIFICATION ==============================
-- owner_id ADDED (user-DATA, owned by a principal):
--   conversations        user conversation threads
--   messages             user conversation turns
--   memory_facts         user long-term memory rows
--   staged_facts         user facts pending promotion
--   committed_facts      user promoted facts
--   fact_rejections      user fact-rejection records
--   owl_profiles         the user's owl personas        (+ visibility)
--   owl_dna              owl personality state          (child of an owl)
--   dna_checkpoints      owl DNA history                (child of an owl)
--   pellets              user knowledge artifacts
--   parliament_sessions  user brainstorm sessions
--   cost_records         per-user spend ledger
--   task_outcomes        user agent-run outcomes
--   reflections          user agent reflections
--   tool_heuristics      per-user learned tool heuristics
--   user_preferences     per-user settings
--   onboarding           per-user onboarding state
--   skills               user/agent skills              (+ visibility, shareable)
--
-- visibility ADDED (shareable entities only): owl_profiles, skills
--
-- SKIPPED (FRAMEWORK / GLOBAL / infra logs not owned by a principal):
--   stackowl_meta          framework key/value metadata
--   schema_migrations      migration bookkeeping
--   langgraph_checkpoints  framework checkpointer state (out of scope)
--   audit_log              security/audit trail (system-wide, integrity-hashed)
--   callback_log           OAuth/webhook callback log (infra)
--   plugins                global plugin registry (installed system-wide)
--   skill_audit            forensic log child of skills (not owned content)
--   thread_registry        channel->session routing registry (infra)
--   onboarding_events      onboarding telemetry stream (infra/event log)
--   jobs / job_runs / job_queue / job_results   scheduler runtime (framework)
--   notification_queue / notification_log / notification_overrides  delivery infra
--   webhook_events_log     webhook rate/event log (infra)
--   reindex_queue          memory reindex work queue (pipeline internal)
--   kuzu_sync_log          graph-sync bookkeeping (pipeline internal)
--   dreamworker_runs       memory consolidation run log (pipeline internal)
--   staged_facts_new       transient table-rebuild scratch (does not persist)
-- =========================================================================

-- NOTE: referential integrity for owner_id is enforced at the APPLICATION layer
-- (PrincipalStore.get() / ensure_default()), NOT at the schema layer. SQLite's
-- ALTER TABLE ADD COLUMN syntax does not allow an inline REFERENCES clause, so
-- no FK constraint is declared here. The invariant is: every write that sets
-- owner_id must first verify the principal exists via PrincipalStore.
-- ---- user-DATA tables: add owner_id (backfills existing rows to default) ----

ALTER TABLE conversations       ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner_id);

ALTER TABLE messages            ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_messages_owner ON messages(owner_id);

ALTER TABLE memory_facts        ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_memory_facts_owner ON memory_facts(owner_id);

ALTER TABLE staged_facts        ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_staged_facts_owner ON staged_facts(owner_id);

ALTER TABLE committed_facts     ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_committed_facts_owner ON committed_facts(owner_id);

ALTER TABLE fact_rejections     ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_fact_rejections_owner ON fact_rejections(owner_id);

ALTER TABLE owl_profiles        ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_owl_profiles_owner ON owl_profiles(owner_id);

ALTER TABLE owl_dna             ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_owl_dna_owner ON owl_dna(owner_id);

ALTER TABLE dna_checkpoints     ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_dna_checkpoints_owner ON dna_checkpoints(owner_id);

ALTER TABLE pellets             ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_pellets_owner ON pellets(owner_id);

ALTER TABLE parliament_sessions ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_parliament_sessions_owner ON parliament_sessions(owner_id);

ALTER TABLE cost_records        ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_cost_records_owner ON cost_records(owner_id);

ALTER TABLE task_outcomes       ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_task_outcomes_owner ON task_outcomes(owner_id);

ALTER TABLE reflections         ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_reflections_owner ON reflections(owner_id);

ALTER TABLE tool_heuristics     ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_tool_heuristics_owner ON tool_heuristics(owner_id);

ALTER TABLE user_preferences    ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_user_preferences_owner ON user_preferences(owner_id);

ALTER TABLE onboarding          ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_onboarding_owner ON onboarding(owner_id);

ALTER TABLE skills              ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'principal-default';
CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_id);

-- ---- shareable entities: add visibility (private by default) ----

ALTER TABLE owl_profiles ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private';
ALTER TABLE skills       ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private';

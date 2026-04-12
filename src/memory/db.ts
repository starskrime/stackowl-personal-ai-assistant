/**
 * StackOwl — MemoryDatabase (SQLite)
 *
 * Single source of truth for all agent memory. Replaces:
 *   - sessions/*.json         → messages table
 *   - memory/facts.json       → facts + facts_fts tables
 *   - memory/episodes.json    → episodes table
 *   - memory/digests/*.json   → digests table
 *   - memory/feedback.json    → feedback table
 *   - AttemptLog (RAM-only)   → attempts table (now persistent!)
 *   - (new) summaries         → compressed message batches
 *   - (new) owl_performance   → per-owl metrics
 *   - (new) owl_learnings     → cross-owl shared knowledge
 *
 * Uses better-sqlite3 (synchronous — fits the existing codebase pattern).
 * FTS5 full-text search built into SQLite — no external search engine needed.
 * Embeddings stored as JSON blobs; cosine similarity computed in Node.js.
 */

import Database from "better-sqlite3";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";
import type { ChatMessage } from "../providers/base.js";
import type { ModelProvider } from "../providers/base.js";

// ─── Schema version — bump when adding columns/tables ───────────
const SCHEMA_VERSION = 7;

// ─── Types ───────────────────────────────────────────────────────

export type FactCategory =
  | "skill" | "preference" | "project_detail" | "personal"
  | "context" | "goal" | "habit" | "relationship" | "decision"
  | "open_question" | "active_goal" | "sub_goal";

export type FactSource = "explicit" | "inferred" | "confirmed";

export interface Fact {
  id: string;
  userId: string;
  owlName: string;
  fact: string;
  entity?: string;
  category: FactCategory;
  confidence: number;
  source: FactSource;
  embedding?: number[];
  accessCount: number;
  expiresAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface Summary {
  id: string;
  sessionId: string;
  userId: string;
  owlName: string;
  fromSeq: number;
  toSeq: number;
  messageCount: number;
  summaryText: string;
  task?: string;
  accomplished?: string;
  keyFacts: string[];
  decisions: string[];
  failedApproaches: string[];
  openQuestions: string[];
  tokensSaved: number;
  createdAt: string;
}

export interface Episode {
  id: string;
  sessionId: string;
  userId: string;
  owlName: string;
  summary: string;
  keyFacts: string[];
  topics: string[];
  sentiment: string;
  importance: number;
  embedding?: number[];
  createdAt: string;
}

export interface Digest {
  sessionId: string;
  userId: string;
  task: string;
  artifacts: Array<{ type: string; value: string; label?: string }>;
  decisions: string[];
  failed: string[];
  openQuestions: string[];
  updatedAt: string;
}

export interface Attempt {
  id: string;
  sessionId: string;
  userId: string;
  owlName: string;
  turn: number;
  toolName: string;
  argsSummary?: string;
  outcome: "success" | "soft-fail" | "hard-fail" | "duplicate-blocked";
  resultSummary?: string;
  createdAt: string;
}

export interface FeedbackRecord {
  id: string;
  sessionId: string;
  userId: string;
  owlName: string;
  signal: "like" | "dislike";
  userMessage?: string;
  assistantSummary?: string;
  toolsUsed: string[];
  createdAt: string;
}

export type PerfMetric =
  | "feedback_like" | "feedback_dislike"
  | "tool_success" | "tool_failure"
  | "loop_exhausted" | "task_completed";

export interface OwlPerfRecord {
  id: string;
  owlName: string;
  sessionId: string;
  userId: string;
  metric: PerfMetric;
  contextTopic?: string;
  value: number;
  createdAt: string;
}

export interface OwlPerfSummary {
  owlName: string;
  totalInteractions: number;
  likeRatio: number;
  toolSuccessRate: number;
  loopExhaustionRate: number;
  topTopics: string[];
  days: number;
}

export type LearningCategory = "skill" | "failure" | "insight" | "preference" | "boundary";

export interface OwlLearning {
  id: string;
  owlName: string;
  learning: string;
  category: LearningCategory;
  confidence: number;
  reinforcementCount: number;
  sourceSessionId?: string;
  createdAt: string;
  updatedAt: string;
}

export interface TaskState {
  sessionId: string;
  owlName: string;
  goal: string;
  /** Ordered list of approaches the model should try — set during PLAN phase */
  plannedApproaches: string[];
  /** Approaches that have been tried and failed this session — never retry these */
  eliminatedApproaches: string[];
  /** Running log of what happened this task, newest first */
  stepLog: string[];
  /** Current status */
  status: "active" | "completed" | "abandoned";
  updatedAt: string;
  createdAt: string;
}

export interface SynthesisRecord {
  id: string;
  owlName: string;
  /** What the user asked for that triggered synthesis */
  capabilityDescription: string;
  /** "skill" (SKILL.md) | "typescript" (compiled tool) | "existing_tool_reuse" */
  synthesisApproach: "skill" | "typescript" | "existing_tool_reuse";
  /** Existing tools/shell commands the synthesized skill orchestrates */
  toolsItUses: string[];
  /** Path to the created file on disk */
  outputPath?: string;
  /** Why this approach was chosen over alternatives */
  creationReasoning?: string;
  /** What was attempted before the final approach — captures first-try failures */
  whatFailedFirst?: string;
  successCount: number;
  failCount: number;
  /** "active" | "retired" | "superseded" */
  status: string;
  sourceSessionId?: string;
  createdAt: string;
  lastUsedAt?: string;
}

export interface ApproachRecord {
  id: string;
  owlName: string;
  toolName: string;
  taskKeywords: string;
  argsSummary: string;
  outcome: "success" | "failure";
  failureReason?: string;
  createdAt: string;
}

export interface Trajectory {
  id: string;
  sessionId: string;
  owlName: string;
  userId?: string;
  /** The user's message that started this ReAct loop */
  userMessage: string;
  /** Number of tool-call iterations completed */
  totalTurns: number;
  /** Unique tool names invoked */
  toolsUsed: string[];
  outcome: "success" | "failure" | "abandoned";
  /** Scalar reward in [-1.0, 1.0] computed by RewardEngine */
  reward: number;
  /** Per-signal contributions to the reward */
  rewardBreakdown: Record<string, number>;
  createdAt: string;
  completedAt?: string;
}

export interface TrajectoryTurn {
  id: string;
  trajectoryId: string;
  turnIndex: number;
  toolName: string;
  /** Truncated JSON of call arguments */
  argsSnapshot: string;
  /** Truncated result string */
  resultSnapshot: string;
  success: boolean;
  durationMs?: number;
  createdAt: string;
}

export type ParliamentVerdictSignal = "PROCEED" | "HOLD" | "ABORT" | "REVISE";
export type ParliamentValidationSignal = "correct" | "wrong" | "partial";

export interface ParliamentVerdictRecord {
  id: string;
  sessionId: string;
  topic: string;
  verdict: ParliamentVerdictSignal;
  synthesis?: string;
  participants: string[];
  /** 1 when the outcome has been observed via trajectory reward */
  validated: number;
  validationSignal?: ParliamentValidationSignal;
  /** Reward from the trajectory that followed this verdict */
  validationReward?: number;
  createdAt: string;
}

export interface PromptOptimizationRecord {
  id: string;
  owlName: string;
  /** The original system prompt that was optimized */
  originalPrompt: string;
  /** The winning improved system prompt */
  improvedPrompt: string;
  /** Textual gradient — WHY the original prompt failed */
  critique?: string;
  /** Score of the winning candidate from the evaluator */
  winnerScore?: number;
  /** How many bad trajectories triggered this run */
  trajectoriesUsed: number;
  /** 1 when this prompt has been applied to the owl's DNA */
  applied: number;
  createdAt: string;
}

// ─── MemoryDatabase ───────────────────────────────────────────────

export class MemoryDatabase {
  private db: Database.Database;

  readonly messages: MessagesRepo;
  readonly facts: FactsRepo;
  readonly summaries: SummariesRepo;
  readonly episodes: EpisodesRepo;
  readonly digests: DigestsRepo;
  readonly attempts: AttemptsRepo;
  readonly feedback: FeedbackRepo;
  readonly owlPerf: OwlPerfRepo;
  readonly owlLearnings: OwlLearningsRepo;
  readonly approachLibrary: ApproachLibraryRepo;
  readonly taskStates: TaskStatesRepo;
  readonly synthesisMemory: SynthesisMemoryRepo;
  readonly trajectories: TrajectoriesRepo;
  readonly promptOptimization: PromptOptimizationRepo;
  readonly parliamentVerdicts: ParliamentVerdictsRepo;

  constructor(workspacePath: string) {
    const dbDir = join(workspacePath, "memory");
    if (!existsSync(dbDir)) mkdirSync(dbDir, { recursive: true });

    const dbPath = join(dbDir, "stackowl.db");
    this.db = new Database(dbPath);

    // Performance pragmas
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("synchronous = NORMAL");
    this.db.pragma("foreign_keys = ON");

    this.createSchema();
    this.runMigrations();

    this.messages        = new MessagesRepo(this.db);
    this.facts           = new FactsRepo(this.db);
    this.summaries       = new SummariesRepo(this.db);
    this.episodes        = new EpisodesRepo(this.db);
    this.digests         = new DigestsRepo(this.db);
    this.attempts        = new AttemptsRepo(this.db);
    this.feedback        = new FeedbackRepo(this.db);
    this.owlPerf         = new OwlPerfRepo(this.db);
    this.owlLearnings    = new OwlLearningsRepo(this.db);
    this.approachLibrary    = new ApproachLibraryRepo(this.db);
    this.taskStates         = new TaskStatesRepo(this.db);
    this.synthesisMemory    = new SynthesisMemoryRepo(this.db);
    this.trajectories       = new TrajectoriesRepo(this.db);
    this.promptOptimization = new PromptOptimizationRepo(this.db);
    this.parliamentVerdicts = new ParliamentVerdictsRepo(this.db);

    log.engine.info(`[MemoryDatabase] Opened: ${dbPath}`);
  }

  close(): void {
    this.db.close();
  }

  // ── Schema creation ────────────────────────────────────────────

  private createSchema(): void {
    this.db.exec(`
      -- Every message from every conversation, every owl
      CREATE TABLE IF NOT EXISTS messages (
        id           TEXT PRIMARY KEY,
        session_id   TEXT NOT NULL,
        user_id      TEXT NOT NULL,
        owl_name     TEXT NOT NULL DEFAULT 'default',
        role         TEXT NOT NULL,
        content      TEXT,
        tool_calls   TEXT,
        tool_call_id TEXT,
        name         TEXT,
        seq          INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
      CREATE INDEX IF NOT EXISTS idx_msg_user    ON messages(user_id);
      CREATE INDEX IF NOT EXISTS idx_msg_date    ON messages(created_at);

      -- Structured long-term facts
      CREATE TABLE IF NOT EXISTS facts (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL,
        owl_name     TEXT NOT NULL DEFAULT 'default',
        fact         TEXT NOT NULL,
        entity       TEXT,
        category     TEXT NOT NULL DEFAULT 'skill',
        confidence   REAL NOT NULL DEFAULT 0.9,
        source       TEXT NOT NULL DEFAULT 'inferred',
        embedding    TEXT,
        access_count INTEGER NOT NULL DEFAULT 0,
        expires_at   TEXT,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_facts_user     ON facts(user_id);
      CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
      CREATE INDEX IF NOT EXISTS idx_facts_expires  ON facts(expires_at);

      -- FTS5 full-text search over facts
      CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
        fact, entity, category,
        content='facts',
        content_rowid='rowid'
      );

      -- Compressed message batches
      CREATE TABLE IF NOT EXISTS summaries (
        id               TEXT PRIMARY KEY,
        session_id       TEXT NOT NULL,
        user_id          TEXT NOT NULL,
        owl_name         TEXT NOT NULL DEFAULT 'default',
        from_seq         INTEGER NOT NULL,
        to_seq           INTEGER NOT NULL,
        message_count    INTEGER NOT NULL,
        summary_text     TEXT NOT NULL,
        task             TEXT,
        accomplished     TEXT,
        key_facts        TEXT DEFAULT '[]',
        decisions        TEXT DEFAULT '[]',
        failed_approaches TEXT DEFAULT '[]',
        open_questions   TEXT DEFAULT '[]',
        tokens_saved     INTEGER DEFAULT 0,
        created_at       TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_sum_session ON summaries(session_id);
      CREATE INDEX IF NOT EXISTS idx_sum_user    ON summaries(user_id);

      -- Session-level episode memories
      CREATE TABLE IF NOT EXISTS episodes (
        id         TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        owl_name   TEXT NOT NULL DEFAULT 'default',
        summary    TEXT NOT NULL,
        key_facts  TEXT DEFAULT '[]',
        topics     TEXT DEFAULT '[]',
        sentiment  TEXT DEFAULT 'neutral',
        importance REAL DEFAULT 0.5,
        embedding  TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_ep_user ON episodes(user_id);

      -- L1 working memory digest per session
      CREATE TABLE IF NOT EXISTS digests (
        session_id     TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL,
        task           TEXT DEFAULT '',
        artifacts      TEXT DEFAULT '[]',
        decisions      TEXT DEFAULT '[]',
        failed         TEXT DEFAULT '[]',
        open_questions TEXT DEFAULT '[]',
        updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
      );

      -- Tool call attempt log — PERSISTENT (was RAM-only before)
      CREATE TABLE IF NOT EXISTS attempts (
        id             TEXT PRIMARY KEY,
        session_id     TEXT NOT NULL,
        user_id        TEXT NOT NULL,
        owl_name       TEXT NOT NULL DEFAULT 'default',
        turn           INTEGER NOT NULL,
        tool_name      TEXT NOT NULL,
        args_summary   TEXT,
        outcome        TEXT NOT NULL,
        result_summary TEXT,
        created_at     TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_att_session ON attempts(session_id);
      CREATE INDEX IF NOT EXISTS idx_att_outcome ON attempts(outcome);

      -- 👍/👎 feedback
      CREATE TABLE IF NOT EXISTS feedback (
        id                TEXT PRIMARY KEY,
        session_id        TEXT NOT NULL,
        user_id           TEXT NOT NULL,
        owl_name          TEXT NOT NULL DEFAULT 'default',
        signal            TEXT NOT NULL,
        user_message      TEXT,
        assistant_summary TEXT,
        tools_used        TEXT DEFAULT '[]',
        created_at        TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_fb_user ON feedback(user_id);
      CREATE INDEX IF NOT EXISTS idx_fb_owl  ON feedback(owl_name);

      -- Per-owl behavioral performance metrics
      CREATE TABLE IF NOT EXISTS owl_performance (
        id            TEXT PRIMARY KEY,
        owl_name      TEXT NOT NULL,
        session_id    TEXT NOT NULL,
        user_id       TEXT NOT NULL,
        metric        TEXT NOT NULL,
        context_topic TEXT,
        value         REAL DEFAULT 1.0,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_perf_owl  ON owl_performance(owl_name);
      CREATE INDEX IF NOT EXISTS idx_perf_date ON owl_performance(created_at);

      -- What each owl has learned — shared cross-owl knowledge
      CREATE TABLE IF NOT EXISTS owl_learnings (
        id                   TEXT PRIMARY KEY,
        owl_name             TEXT NOT NULL,
        learning             TEXT NOT NULL,
        category             TEXT NOT NULL DEFAULT 'skill',
        confidence           REAL DEFAULT 0.7,
        reinforcement_count  INTEGER DEFAULT 1,
        source_session_id    TEXT,
        created_at           TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_learn_owl      ON owl_learnings(owl_name);
      CREATE INDEX IF NOT EXISTS idx_learn_category ON owl_learnings(category);

      -- FTS5 cross-owl learning search
      CREATE VIRTUAL TABLE IF NOT EXISTS owl_learnings_fts USING fts5(
        learning, category,
        content='owl_learnings',
        content_rowid='rowid'
      );

      -- Synthesis memory: what the owl has built, why, and whether it worked.
      -- Injected at session start as owl self-knowledge — fixes "I can't create tools" responses.
      CREATE TABLE IF NOT EXISTS synthesis_memory (
        id                      TEXT PRIMARY KEY,
        owl_name                TEXT NOT NULL DEFAULT 'default',
        capability_description  TEXT NOT NULL,
        synthesis_approach      TEXT NOT NULL DEFAULT 'skill',
        tools_it_uses           TEXT NOT NULL DEFAULT '[]',
        output_path             TEXT,
        creation_reasoning      TEXT,
        what_failed_first       TEXT,
        success_count           INTEGER NOT NULL DEFAULT 0,
        fail_count              INTEGER NOT NULL DEFAULT 0,
        status                  TEXT NOT NULL DEFAULT 'active',
        source_session_id       TEXT,
        created_at              TEXT NOT NULL DEFAULT (datetime('now')),
        last_used_at            TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_syn_owl    ON synthesis_memory(owl_name);
      CREATE INDEX IF NOT EXISTS idx_syn_status ON synthesis_memory(status);

      -- Per-session task state: goal, planned approaches, eliminated approaches, step log.
      -- Persists across context compression so the model always knows what it tried.
      CREATE TABLE IF NOT EXISTS task_states (
        session_id              TEXT PRIMARY KEY,
        owl_name                TEXT NOT NULL DEFAULT 'default',
        goal                    TEXT NOT NULL DEFAULT '',
        planned_approaches      TEXT NOT NULL DEFAULT '[]',
        eliminated_approaches   TEXT NOT NULL DEFAULT '[]',
        step_log                TEXT NOT NULL DEFAULT '[]',
        status                  TEXT NOT NULL DEFAULT 'active',
        created_at              TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
      );

      -- Approach library: cross-session record of what was tried and what happened.
      -- Written automatically by the ReAct loop on every tool execution.
      -- Read before each tool batch to warn the model about known failure patterns.
      CREATE TABLE IF NOT EXISTS approach_library (
        id              TEXT PRIMARY KEY,
        owl_name        TEXT NOT NULL DEFAULT 'default',
        tool_name       TEXT NOT NULL,
        task_keywords   TEXT NOT NULL DEFAULT '',
        args_summary    TEXT NOT NULL DEFAULT '',
        outcome         TEXT NOT NULL,
        failure_reason  TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_appr_tool    ON approach_library(tool_name);
      CREATE INDEX IF NOT EXISTS idx_appr_outcome ON approach_library(outcome);
      CREATE INDEX IF NOT EXISTS idx_appr_owl     ON approach_library(owl_name);

      -- Trajectory store: turn-by-turn traces of every ReAct loop.
      -- Each trajectory captures what the model tried, the outcome, and
      -- a scalar reward. Used by APO to identify bad runs for critique.
      CREATE TABLE IF NOT EXISTS trajectories (
        id               TEXT PRIMARY KEY,
        session_id       TEXT NOT NULL,
        owl_name         TEXT NOT NULL DEFAULT 'default',
        user_id          TEXT,
        user_message     TEXT NOT NULL DEFAULT '',
        total_turns      INTEGER NOT NULL DEFAULT 0,
        tools_used       TEXT NOT NULL DEFAULT '[]',
        outcome          TEXT NOT NULL DEFAULT 'success',
        reward           REAL NOT NULL DEFAULT 0.0,
        reward_breakdown TEXT NOT NULL DEFAULT '{}',
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at     TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_traj_session ON trajectories(session_id);
      CREATE INDEX IF NOT EXISTS idx_traj_owl     ON trajectories(owl_name);
      CREATE INDEX IF NOT EXISTS idx_traj_reward  ON trajectories(reward);

      -- Individual tool invocations within a trajectory
      CREATE TABLE IF NOT EXISTS trajectory_turns (
        id              TEXT PRIMARY KEY,
        trajectory_id   TEXT NOT NULL REFERENCES trajectories(id),
        turn_index      INTEGER NOT NULL,
        tool_name       TEXT NOT NULL,
        args_snapshot   TEXT NOT NULL DEFAULT '',
        result_snapshot TEXT NOT NULL DEFAULT '',
        success         INTEGER NOT NULL DEFAULT 1,
        duration_ms     INTEGER,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_turn_traj ON trajectory_turns(trajectory_id);

      -- APO-lite prompt optimization log: captures each optimization run outcome.
      -- Applied prompts become the new owl system prompt (via promptSections in DNA).
      CREATE TABLE IF NOT EXISTS prompt_optimization_log (
        id                  TEXT PRIMARY KEY,
        owl_name            TEXT NOT NULL,
        original_prompt     TEXT NOT NULL,
        improved_prompt     TEXT NOT NULL,
        critique            TEXT,
        winner_score        REAL,
        trajectories_used   INTEGER DEFAULT 0,
        applied             INTEGER DEFAULT 0,
        created_at          TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_opt_owl     ON prompt_optimization_log(owl_name);
      CREATE INDEX IF NOT EXISTS idx_opt_applied ON prompt_optimization_log(applied);

      -- Parliament verdict tracking: remembers past verdicts and validates them
      -- against the rewards that followed. Parliament enters debates knowing its
      -- own track record on similar topics.
      CREATE TABLE IF NOT EXISTS parliament_verdicts (
        id                  TEXT PRIMARY KEY,
        session_id          TEXT NOT NULL,
        topic               TEXT NOT NULL,
        verdict             TEXT NOT NULL,
        synthesis           TEXT,
        participants        TEXT NOT NULL DEFAULT '[]',
        validated           INTEGER NOT NULL DEFAULT 0,
        validation_signal   TEXT,
        validation_reward   REAL,
        created_at          TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_pv_topic     ON parliament_verdicts(topic);
      CREATE INDEX IF NOT EXISTS idx_pv_validated ON parliament_verdicts(validated);
    `);
  }

  // ── Schema migrations ──────────────────────────────────────────

  private runMigrations(): void {
    const current = (this.db.pragma("user_version") as { user_version: number }[])[0]?.user_version ?? 0;
    if (current < 2) {
      // v2: approach_library table
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS approach_library (
          id              TEXT PRIMARY KEY,
          owl_name        TEXT NOT NULL DEFAULT 'default',
          tool_name       TEXT NOT NULL,
          task_keywords   TEXT NOT NULL DEFAULT '',
          args_summary    TEXT NOT NULL DEFAULT '',
          outcome         TEXT NOT NULL,
          failure_reason  TEXT,
          created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_appr_tool    ON approach_library(tool_name);
        CREATE INDEX IF NOT EXISTS idx_appr_outcome ON approach_library(outcome);
        CREATE INDEX IF NOT EXISTS idx_appr_owl     ON approach_library(owl_name);
      `);
    }
    if (current < 3) {
      // v3: task_states table
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS task_states (
          session_id              TEXT PRIMARY KEY,
          owl_name                TEXT NOT NULL DEFAULT 'default',
          goal                    TEXT NOT NULL DEFAULT '',
          planned_approaches      TEXT NOT NULL DEFAULT '[]',
          eliminated_approaches   TEXT NOT NULL DEFAULT '[]',
          step_log                TEXT NOT NULL DEFAULT '[]',
          status                  TEXT NOT NULL DEFAULT 'active',
          created_at              TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
        );
      `);
    }
    if (current < 4) {
      // v4: synthesis_memory table
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS synthesis_memory (
          id                      TEXT PRIMARY KEY,
          owl_name                TEXT NOT NULL DEFAULT 'default',
          capability_description  TEXT NOT NULL,
          synthesis_approach      TEXT NOT NULL DEFAULT 'skill',
          tools_it_uses           TEXT NOT NULL DEFAULT '[]',
          output_path             TEXT,
          creation_reasoning      TEXT,
          what_failed_first       TEXT,
          success_count           INTEGER NOT NULL DEFAULT 0,
          fail_count              INTEGER NOT NULL DEFAULT 0,
          status                  TEXT NOT NULL DEFAULT 'active',
          source_session_id       TEXT,
          created_at              TEXT NOT NULL DEFAULT (datetime('now')),
          last_used_at            TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_syn_owl    ON synthesis_memory(owl_name);
        CREATE INDEX IF NOT EXISTS idx_syn_status ON synthesis_memory(status);
      `);
    }
    if (current < 5) {
      // v5: trajectories + trajectory_turns tables
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS trajectories (
          id               TEXT PRIMARY KEY,
          session_id       TEXT NOT NULL,
          owl_name         TEXT NOT NULL DEFAULT 'default',
          user_id          TEXT,
          user_message     TEXT NOT NULL DEFAULT '',
          total_turns      INTEGER NOT NULL DEFAULT 0,
          tools_used       TEXT NOT NULL DEFAULT '[]',
          outcome          TEXT NOT NULL DEFAULT 'success',
          reward           REAL NOT NULL DEFAULT 0.0,
          reward_breakdown TEXT NOT NULL DEFAULT '{}',
          created_at       TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_traj_session ON trajectories(session_id);
        CREATE INDEX IF NOT EXISTS idx_traj_owl     ON trajectories(owl_name);
        CREATE INDEX IF NOT EXISTS idx_traj_reward  ON trajectories(reward);

        CREATE TABLE IF NOT EXISTS trajectory_turns (
          id              TEXT PRIMARY KEY,
          trajectory_id   TEXT NOT NULL REFERENCES trajectories(id),
          turn_index      INTEGER NOT NULL,
          tool_name       TEXT NOT NULL,
          args_snapshot   TEXT NOT NULL DEFAULT '',
          result_snapshot TEXT NOT NULL DEFAULT '',
          success         INTEGER NOT NULL DEFAULT 1,
          duration_ms     INTEGER,
          created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_turn_traj ON trajectory_turns(trajectory_id);
      `);
    }
    if (current < 6) {
      // v6: prompt_optimization_log table
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS prompt_optimization_log (
          id                  TEXT PRIMARY KEY,
          owl_name            TEXT NOT NULL,
          original_prompt     TEXT NOT NULL,
          improved_prompt     TEXT NOT NULL,
          critique            TEXT,
          winner_score        REAL,
          trajectories_used   INTEGER DEFAULT 0,
          applied             INTEGER DEFAULT 0,
          created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_opt_owl     ON prompt_optimization_log(owl_name);
        CREATE INDEX IF NOT EXISTS idx_opt_applied ON prompt_optimization_log(applied);
      `);
    }
    if (current < 7) {
      // v7: parliament_verdicts table
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS parliament_verdicts (
          id                  TEXT PRIMARY KEY,
          session_id          TEXT NOT NULL,
          topic               TEXT NOT NULL,
          verdict             TEXT NOT NULL,
          synthesis           TEXT,
          participants        TEXT NOT NULL DEFAULT '[]',
          validated           INTEGER NOT NULL DEFAULT 0,
          validation_signal   TEXT,
          validation_reward   REAL,
          created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_pv_topic     ON parliament_verdicts(topic);
        CREATE INDEX IF NOT EXISTS idx_pv_validated ON parliament_verdicts(validated);
      `);
    }
    if (current < SCHEMA_VERSION) {
      this.db.pragma(`user_version = ${SCHEMA_VERSION}`);
      log.engine.info(`[MemoryDatabase] Schema migrated to v${SCHEMA_VERSION}`);
    }
  }

  // ── JSON import (one-time migration from existing files) ───────

  async importFromJson(workspacePath: string): Promise<void> {
    const imported: string[] = [];

    // facts.json
    const factsPath = join(workspacePath, "memory", "facts.json");
    if (existsSync(factsPath)) {
      try {
        const data = JSON.parse(readFileSync(factsPath, "utf-8"));
        const facts: any[] = data.facts ?? [];
        const insert = this.db.prepare(`
          INSERT OR IGNORE INTO facts
            (id, user_id, owl_name, fact, entity, category, confidence, source, embedding,
             access_count, expires_at, created_at, updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        `);
        const insertMany = this.db.transaction((rows: any[]) => {
          for (const f of rows) {
            insert.run(
              f.id, f.userId ?? "default", f.owlName ?? "default",
              f.fact, f.entity ?? null, f.category ?? "skill",
              f.confidence ?? 0.9, f.source ?? "inferred",
              f.embedding ? JSON.stringify(f.embedding) : null,
              f.accessCount ?? 0, f.expiresAt ?? null,
              f.createdAt ?? new Date().toISOString(),
              f.updatedAt ?? new Date().toISOString(),
            );
          }
        });
        insertMany(facts);
        this.rebuildFactsFts();
        imported.push(`${facts.length} facts`);
      } catch (err) {
        log.engine.warn(`[MemoryDatabase] facts.json import failed: ${err}`);
      }
    }

    // episodes.json
    const episodesPath = join(workspacePath, "memory", "episodes.json");
    if (existsSync(episodesPath)) {
      try {
        const episodes: any[] = JSON.parse(readFileSync(episodesPath, "utf-8"));
        const insert = this.db.prepare(`
          INSERT OR IGNORE INTO episodes
            (id, session_id, user_id, owl_name, summary, key_facts, topics,
             sentiment, importance, embedding, created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)
        `);
        const insertMany = this.db.transaction((rows: any[]) => {
          for (const e of rows) {
            insert.run(
              e.id, e.sessionId ?? "imported", e.userId ?? "default",
              e.owlName ?? "default", e.summary,
              JSON.stringify(e.keyFacts ?? []), JSON.stringify(e.topics ?? []),
              e.sentiment ?? "neutral", e.importance ?? 0.5,
              e.embedding ? JSON.stringify(e.embedding) : null,
              e.date ?? e.createdAt ?? new Date().toISOString(),
            );
          }
        });
        insertMany(episodes);
        imported.push(`${episodes.length} episodes`);
      } catch (err) {
        log.engine.warn(`[MemoryDatabase] episodes.json import failed: ${err}`);
      }
    }

    if (imported.length > 0) {
      log.engine.info(`[MemoryDatabase] Imported from JSON: ${imported.join(", ")}`);
    }
  }

  rebuildFactsFts(): void {
    this.db.exec(`INSERT INTO facts_fts(facts_fts) VALUES('rebuild')`);
  }

  rebuildLearningsFts(): void {
    this.db.exec(`INSERT INTO owl_learnings_fts(owl_learnings_fts) VALUES('rebuild')`);
  }
}

// ─── Repos ────────────────────────────────────────────────────────

class MessagesRepo {
  constructor(private db: Database.Database) {}

  append(sessionId: string, userId: string, owlName: string, messages: ChatMessage[]): void {
    const maxSeq = (this.db.prepare(
      "SELECT COALESCE(MAX(seq), -1) as m FROM messages WHERE session_id = ?"
    ).get(sessionId) as any)?.m ?? -1;

    const insert = this.db.prepare(`
      INSERT INTO messages (id, session_id, user_id, owl_name, role, content, tool_calls, tool_call_id, name, seq)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    const insertMany = this.db.transaction((msgs: ChatMessage[]) => {
      let seq = maxSeq + 1;
      for (const m of msgs) {
        insert.run(
          uuidv4(), sessionId, userId, owlName,
          m.role,
          typeof m.content === "string" ? m.content : JSON.stringify(m.content),
          m.toolCalls ? JSON.stringify(m.toolCalls) : null,
          (m as any).toolCallId ?? null,
          (m as any).name ?? null,
          seq++,
        );
      }
    });
    insertMany(messages);
  }

  getSession(sessionId: string): ChatMessage[] {
    const rows = this.db.prepare(
      "SELECT * FROM messages WHERE session_id = ? ORDER BY seq ASC"
    ).all(sessionId) as any[];
    return rows.map(rowToMessage);
  }

  getRecent(sessionId: string, limit: number): ChatMessage[] {
    const rows = this.db.prepare(
      "SELECT * FROM messages WHERE session_id = ? ORDER BY seq DESC LIMIT ?"
    ).all(sessionId, limit) as any[];
    return rows.reverse().map(rowToMessage);
  }

  getToday(userId: string): ChatMessage[] {
    const rows = this.db.prepare(
      "SELECT * FROM messages WHERE user_id = ? AND DATE(created_at) = DATE('now') ORDER BY created_at ASC"
    ).all(userId) as any[];
    return rows.map(rowToMessage);
  }

  getForUser(userId: string, limit = 100): ChatMessage[] {
    const rows = this.db.prepare(
      "SELECT * FROM messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?"
    ).all(userId, limit) as any[];
    return rows.reverse().map(rowToMessage);
  }

  countSession(sessionId: string): number {
    const row = this.db.prepare(
      "SELECT COUNT(*) as c FROM messages WHERE session_id = ?"
    ).get(sessionId) as any;
    return row?.c ?? 0;
  }

  getMaxSeq(sessionId: string): number {
    const row = this.db.prepare(
      "SELECT COALESCE(MAX(seq), -1) as m FROM messages WHERE session_id = ?"
    ).get(sessionId) as any;
    return row?.m ?? -1;
  }
}

class FactsRepo {
  constructor(private db: Database.Database) {}

  add(
    fact: Omit<Fact, "id" | "createdAt" | "updatedAt" | "accessCount">,
    _provider?: ModelProvider,
  ): Fact {
    const id = `fact_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const now = new Date().toISOString();

    // Embedding: async not possible in sync SQLite, so store without for now.
    // rememberTool uses addWithEmbedding() which handles embed + store.
    const stored: Fact = {
      ...fact,
      id,
      accessCount: 0,
      createdAt: now,
      updatedAt: now,
    };

    this.db.prepare(`
      INSERT OR REPLACE INTO facts
        (id, user_id, owl_name, fact, entity, category, confidence, source,
         embedding, access_count, expires_at, created_at, updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      id, fact.userId, fact.owlName ?? "default", fact.fact,
      fact.entity ?? null, fact.category, fact.confidence, fact.source,
      fact.embedding ? JSON.stringify(fact.embedding) : null,
      0, fact.expiresAt ?? null, now, now,
    );

    // Update FTS index
    this.db.prepare(
      "INSERT INTO facts_fts(rowid, fact, entity, category) VALUES (last_insert_rowid(), ?, ?, ?)"
    ).run(fact.fact, fact.entity ?? "", fact.category);

    return stored;
  }

  /** Full-text search via FTS5, falls back to LIKE when FTS5 has no results */
  search(query: string, userId?: string, limit = 5): Fact[] {
    const now = new Date().toISOString();
    try {
      // FTS5 search
      const ftsRows = this.db.prepare(`
        SELECT f.* FROM facts f
        JOIN facts_fts ON f.rowid = facts_fts.rowid
        WHERE facts_fts MATCH ?
          AND (? IS NULL OR f.user_id = ?)
          AND f.confidence > 0
          AND (f.expires_at IS NULL OR f.expires_at > ?)
        ORDER BY facts_fts.rank
        LIMIT ?
      `).all(query, userId ?? null, userId ?? null, now, limit) as any[];

      if (ftsRows.length > 0) {
        this.incrementAccess(ftsRows.map((r: any) => r.id));
        return ftsRows.map(rowToFact);
      }
    } catch {
      // FTS5 may fail on special chars — fall through to LIKE
    }

    // LIKE fallback
    const likeRows = this.db.prepare(`
      SELECT * FROM facts
      WHERE fact LIKE ?
        AND (? IS NULL OR user_id = ?)
        AND confidence > 0
        AND (expires_at IS NULL OR expires_at > ?)
      ORDER BY confidence DESC
      LIMIT ?
    `).all(`%${query}%`, userId ?? null, userId ?? null, now, limit) as any[];

    this.incrementAccess(likeRows.map((r: any) => r.id));
    return likeRows.map(rowToFact);
  }

  /** Semantic search using pre-stored embeddings + cosine similarity */
  semanticSearch(queryEmbedding: number[], userId?: string, limit = 5): Fact[] {
    const now = new Date().toISOString();
    const rows = this.db.prepare(`
      SELECT * FROM facts
      WHERE embedding IS NOT NULL
        AND (? IS NULL OR user_id = ?)
        AND confidence > 0
        AND (expires_at IS NULL OR expires_at > ?)
    `).all(userId ?? null, userId ?? null, now) as any[];

    const scored = rows
      .map((r: any) => {
        const emb: number[] = JSON.parse(r.embedding);
        return { row: r, score: cosineSimilarity(queryEmbedding, emb) };
      })
      .filter((s) => s.score > 0.25)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);

    this.incrementAccess(scored.map((s) => s.row.id));
    return scored.map((s) => rowToFact(s.row));
  }

  getByCategory(userId: string, category: FactCategory): Fact[] {
    const now = new Date().toISOString();
    const rows = this.db.prepare(`
      SELECT * FROM facts
      WHERE user_id = ? AND category = ? AND confidence > 0
        AND (expires_at IS NULL OR expires_at > ?)
      ORDER BY confidence DESC
    `).all(userId, category, now) as any[];
    return rows.map(rowToFact);
  }

  /** All non-retired facts (used by FactStore.load to populate in-memory map from DB) */
  getAllForUser(userId?: string): Fact[] {
    const rows = userId
      ? (this.db.prepare("SELECT * FROM facts WHERE user_id = ? AND confidence > 0").all(userId) as any[])
      : (this.db.prepare("SELECT * FROM facts WHERE confidence > 0").all() as any[]);
    return rows.map(rowToFact);
  }

  /** Insert-or-replace preserving the caller's ID (used by FactStore.save) */
  upsert(fact: Omit<Fact, "owlName"> & { owlName?: string }): void {
    this.db.prepare(`
      INSERT OR REPLACE INTO facts
        (id, user_id, owl_name, fact, entity, category, confidence, source,
         embedding, access_count, expires_at, created_at, updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      fact.id, fact.userId, fact.owlName ?? "default", fact.fact,
      fact.entity ?? null, fact.category, fact.confidence, fact.source,
      fact.embedding ? JSON.stringify(fact.embedding) : null,
      fact.accessCount, fact.expiresAt ?? null, fact.createdAt, fact.updatedAt,
    );
  }

  confirm(id: string, _userId?: string): void {
    this.db.prepare(`
      UPDATE facts SET source = 'confirmed', confidence = 0.95,
        updated_at = datetime('now') WHERE id = ?
    `).run(id);
  }

  retire(id: string): void {
    this.db.prepare(
      "UPDATE facts SET confidence = 0, updated_at = datetime('now') WHERE id = ?"
    ).run(id);
  }

  private incrementAccess(ids: string[]): void {
    if (ids.length === 0) return;
    const stmt = this.db.prepare("UPDATE facts SET access_count = access_count + 1 WHERE id = ?");
    const update = this.db.transaction((list: string[]) => {
      for (const id of list) stmt.run(id);
    });
    update(ids);
  }
}

class SummariesRepo {
  constructor(private db: Database.Database) {}

  add(summary: Omit<Summary, "id" | "createdAt">): Summary {
    const id = `sum_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const now = new Date().toISOString();

    this.db.prepare(`
      INSERT INTO summaries
        (id, session_id, user_id, owl_name, from_seq, to_seq, message_count,
         summary_text, task, accomplished, key_facts, decisions,
         failed_approaches, open_questions, tokens_saved, created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      id, summary.sessionId, summary.userId, summary.owlName,
      summary.fromSeq, summary.toSeq, summary.messageCount,
      summary.summaryText, summary.task ?? null, summary.accomplished ?? null,
      JSON.stringify(summary.keyFacts),
      JSON.stringify(summary.decisions),
      JSON.stringify(summary.failedApproaches),
      JSON.stringify(summary.openQuestions),
      summary.tokensSaved,
      now,
    );

    return { ...summary, id, createdAt: now };
  }

  getLatest(sessionId: string): Summary | null {
    const row = this.db.prepare(`
      SELECT * FROM summaries WHERE session_id = ? ORDER BY created_at DESC LIMIT 1
    `).get(sessionId) as any;
    return row ? rowToSummary(row) : null;
  }

  getAll(sessionId: string): Summary[] {
    const rows = this.db.prepare(
      "SELECT * FROM summaries WHERE session_id = ? ORDER BY created_at ASC"
    ).all(sessionId) as any[];
    return rows.map(rowToSummary);
  }

  getForUser(userId: string, limit = 10): Summary[] {
    const rows = this.db.prepare(`
      SELECT * FROM summaries WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
    `).all(userId, limit) as any[];
    return rows.map(rowToSummary);
  }
}

class EpisodesRepo {
  constructor(private db: Database.Database) {}

  add(episode: Omit<Episode, "id" | "createdAt">): Episode {
    const id = `ep_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const now = new Date().toISOString();

    this.db.prepare(`
      INSERT INTO episodes
        (id, session_id, user_id, owl_name, summary, key_facts, topics,
         sentiment, importance, embedding, created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      id, episode.sessionId, episode.userId, episode.owlName,
      episode.summary,
      JSON.stringify(episode.keyFacts ?? []),
      JSON.stringify(episode.topics ?? []),
      episode.sentiment ?? "neutral",
      episode.importance ?? 0.5,
      episode.embedding ? JSON.stringify(episode.embedding) : null,
      now,
    );

    return { ...episode, id, createdAt: now };
  }

  search(query: string, userId?: string, limit = 5): Episode[] {
    const pattern = `%${query}%`;
    const rows = this.db.prepare(`
      SELECT * FROM episodes
      WHERE (summary LIKE ? OR key_facts LIKE ? OR topics LIKE ?)
        AND (? IS NULL OR user_id = ?)
      ORDER BY importance DESC, created_at DESC
      LIMIT ?
    `).all(pattern, pattern, pattern, userId ?? null, userId ?? null, limit) as any[];
    return rows.map(rowToEpisode);
  }

  getForUser(userId: string, limit = 20): Episode[] {
    const rows = this.db.prepare(`
      SELECT * FROM episodes WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
    `).all(userId, limit) as any[];
    return rows.map(rowToEpisode);
  }

  /** All episodes without a row limit (used by EpisodicMemory.load) */
  getAll(userId?: string): Episode[] {
    const rows = userId
      ? (this.db.prepare("SELECT * FROM episodes WHERE user_id = ? ORDER BY created_at ASC").all(userId) as any[])
      : (this.db.prepare("SELECT * FROM episodes ORDER BY created_at ASC").all() as any[]);
    return rows.map(rowToEpisode);
  }

  /** Insert-or-ignore preserving the caller's ID (used by EpisodicMemory.save) */
  upsert(episode: Episode): void {
    this.db.prepare(`
      INSERT OR IGNORE INTO episodes
        (id, session_id, user_id, owl_name, summary, key_facts, topics,
         sentiment, importance, embedding, created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      episode.id, episode.sessionId, episode.userId, episode.owlName,
      episode.summary,
      JSON.stringify(episode.keyFacts ?? []),
      JSON.stringify(episode.topics ?? []),
      episode.sentiment ?? "neutral",
      episode.importance ?? 0.5,
      episode.embedding ? JSON.stringify(episode.embedding) : null,
      episode.createdAt,
    );
  }
}

class DigestsRepo {
  constructor(private db: Database.Database) {}

  get(sessionId: string): Digest | null {
    const row = this.db.prepare(
      "SELECT * FROM digests WHERE session_id = ?"
    ).get(sessionId) as any;
    return row ? rowToDigest(row) : null;
  }

  update(sessionId: string, userId: string, data: Partial<Omit<Digest, "sessionId" | "userId" | "updatedAt">>): void {
    const existing = this.get(sessionId);
    if (!existing) {
      this.db.prepare(`
        INSERT INTO digests (session_id, user_id, task, artifacts, decisions, failed, open_questions, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
      `).run(
        sessionId, userId,
        data.task ?? "",
        JSON.stringify(data.artifacts ?? []),
        JSON.stringify(data.decisions ?? []),
        JSON.stringify(data.failed ?? []),
        JSON.stringify(data.openQuestions ?? []),
      );
    } else {
      this.db.prepare(`
        UPDATE digests SET
          task = ?, artifacts = ?, decisions = ?, failed = ?, open_questions = ?,
          updated_at = datetime('now')
        WHERE session_id = ?
      `).run(
        data.task ?? existing.task,
        JSON.stringify(data.artifacts ?? existing.artifacts),
        JSON.stringify(data.decisions ?? existing.decisions),
        JSON.stringify(data.failed ?? existing.failed),
        JSON.stringify(data.openQuestions ?? existing.openQuestions),
        sessionId,
      );
    }
  }

  clear(sessionId: string): void {
    this.db.prepare("DELETE FROM digests WHERE session_id = ?").run(sessionId);
  }
}

class AttemptsRepo {
  constructor(private db: Database.Database) {}

  record(attempt: Omit<Attempt, "id" | "createdAt">): void {
    this.db.prepare(`
      INSERT INTO attempts
        (id, session_id, user_id, owl_name, turn, tool_name, args_summary, outcome, result_summary)
      VALUES (?,?,?,?,?,?,?,?,?)
    `).run(
      uuidv4(),
      attempt.sessionId, attempt.userId, attempt.owlName,
      attempt.turn, attempt.toolName,
      attempt.argsSummary ?? null,
      attempt.outcome,
      attempt.resultSummary ?? null,
    );
  }

  getForSession(sessionId: string): Attempt[] {
    const rows = this.db.prepare(
      "SELECT * FROM attempts WHERE session_id = ? ORDER BY created_at ASC"
    ).all(sessionId) as any[];
    return rows.map(rowToAttempt);
  }

  getFailures(sessionId: string): Attempt[] {
    const rows = this.db.prepare(`
      SELECT * FROM attempts WHERE session_id = ? AND outcome IN ('hard-fail', 'soft-fail')
      ORDER BY created_at ASC
    `).all(sessionId) as any[];
    return rows.map(rowToAttempt);
  }
}

class FeedbackRepo {
  constructor(private db: Database.Database) {}

  record(entry: Omit<FeedbackRecord, "id" | "createdAt">): void {
    this.db.prepare(`
      INSERT INTO feedback
        (id, session_id, user_id, owl_name, signal, user_message, assistant_summary, tools_used)
      VALUES (?,?,?,?,?,?,?,?)
    `).run(
      uuidv4(),
      entry.sessionId, entry.userId, entry.owlName,
      entry.signal,
      entry.userMessage ?? null,
      entry.assistantSummary ?? null,
      JSON.stringify(entry.toolsUsed ?? []),
    );
  }

  getRatioForOwl(owlName: string): number {
    const row = this.db.prepare(`
      SELECT
        AVG(CASE WHEN signal = 'like' THEN 1.0 ELSE 0.0 END) as ratio
      FROM feedback WHERE owl_name = ?
    `).get(owlName) as any;
    return row?.ratio ?? 0.5;
  }

  getRecentForUser(userId: string, limit = 20): FeedbackRecord[] {
    const rows = this.db.prepare(`
      SELECT * FROM feedback WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
    `).all(userId, limit) as any[];
    return rows.map(rowToFeedback);
  }

  getRecent(limit = 50): FeedbackRecord[] {
    const rows = this.db.prepare(
      "SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?"
    ).all(limit) as any[];
    return rows.map(rowToFeedback);
  }
}

class OwlPerfRepo {
  constructor(private db: Database.Database) {}

  record(owlName: string, sessionId: string, userId: string, metric: PerfMetric, contextTopic?: string, value = 1): void {
    this.db.prepare(`
      INSERT INTO owl_performance (id, owl_name, session_id, user_id, metric, context_topic, value)
      VALUES (?,?,?,?,?,?,?)
    `).run(uuidv4(), owlName, sessionId, userId, metric, contextTopic ?? null, value);
  }

  getSummary(owlName: string, days = 30): OwlPerfSummary {
    const cutoff = new Date(Date.now() - days * 86400_000).toISOString();

    const row = this.db.prepare(`
      SELECT
        COUNT(*) as total,
        AVG(CASE WHEN metric = 'feedback_like'   THEN 1.0 ELSE 0.0 END) as like_ratio,
        AVG(CASE WHEN metric = 'tool_failure'    THEN 1.0 ELSE 0.0 END) as fail_rate,
        AVG(CASE WHEN metric = 'loop_exhausted'  THEN 1.0 ELSE 0.0 END) as exhaust_rate
      FROM owl_performance
      WHERE owl_name = ? AND created_at > ?
    `).get(owlName, cutoff) as any;

    const topTopicsRows = this.db.prepare(`
      SELECT context_topic, COUNT(*) as c FROM owl_performance
      WHERE owl_name = ? AND context_topic IS NOT NULL AND created_at > ?
      GROUP BY context_topic ORDER BY c DESC LIMIT 5
    `).all(owlName, cutoff) as any[];

    return {
      owlName,
      totalInteractions: row?.total ?? 0,
      likeRatio: row?.like_ratio ?? 0.5,
      toolSuccessRate: 1 - (row?.fail_rate ?? 0),
      loopExhaustionRate: row?.exhaust_rate ?? 0,
      topTopics: topTopicsRows.map((r) => r.context_topic),
      days,
    };
  }

  compareOwls(): Array<{ owlName: string; likeRatio: number; total: number }> {
    const rows = this.db.prepare(`
      SELECT
        owl_name,
        AVG(CASE WHEN metric = 'feedback_like' THEN 1.0 ELSE 0.0 END) as like_ratio,
        COUNT(*) as total
      FROM owl_performance
      GROUP BY owl_name ORDER BY like_ratio DESC
    `).all() as any[];
    return rows.map((r) => ({ owlName: r.owl_name, likeRatio: r.like_ratio, total: r.total }));
  }
}

class OwlLearningsRepo {
  constructor(private db: Database.Database) {}

  add(owlName: string, learning: string, category: LearningCategory, sourceSessionId?: string, confidence = 0.7): OwlLearning {
    // Check for existing similar learning to reinforce instead of duplicate
    const existing = this.findSimilar(owlName, learning, category);
    if (existing) {
      this.db.prepare(`
        UPDATE owl_learnings SET
          reinforcement_count = reinforcement_count + 1,
          confidence = MIN(1.0, confidence + 0.05),
          updated_at = datetime('now')
        WHERE id = ?
      `).run(existing.id);
      return { ...existing, reinforcementCount: existing.reinforcementCount + 1 };
    }

    const id = `learn_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const now = new Date().toISOString();

    this.db.prepare(`
      INSERT INTO owl_learnings
        (id, owl_name, learning, category, confidence, reinforcement_count, source_session_id, created_at, updated_at)
      VALUES (?,?,?,?,?,1,?,?,?)
    `).run(id, owlName, learning, category, confidence, sourceSessionId ?? null, now, now);

    // Update FTS index
    this.db.prepare(
      "INSERT INTO owl_learnings_fts(rowid, learning, category) VALUES (last_insert_rowid(), ?, ?)"
    ).run(learning, category);

    return { id, owlName, learning, category, confidence, reinforcementCount: 1, sourceSessionId, createdAt: now, updatedAt: now };
  }

  /** Cross-owl FTS5 search — finds what ANY owl has learned about a topic */
  search(query: string, limit = 5): OwlLearning[] {
    try {
      const rows = this.db.prepare(`
        SELECT l.* FROM owl_learnings l
        JOIN owl_learnings_fts ON l.rowid = owl_learnings_fts.rowid
        WHERE owl_learnings_fts MATCH ?
        ORDER BY owl_learnings_fts.rank, l.confidence DESC
        LIMIT ?
      `).all(query, limit) as any[];
      return rows.map(rowToLearning);
    } catch {
      const rows = this.db.prepare(`
        SELECT * FROM owl_learnings WHERE learning LIKE ? ORDER BY confidence DESC LIMIT ?
      `).all(`%${query}%`, limit) as any[];
      return rows.map(rowToLearning);
    }
  }

  getForOwl(owlName: string, category?: LearningCategory): OwlLearning[] {
    if (category) {
      const rows = this.db.prepare(`
        SELECT * FROM owl_learnings WHERE owl_name = ? AND category = ?
        ORDER BY confidence DESC, reinforcement_count DESC
      `).all(owlName, category) as any[];
      return rows.map(rowToLearning);
    }
    const rows = this.db.prepare(`
      SELECT * FROM owl_learnings WHERE owl_name = ?
      ORDER BY confidence DESC, reinforcement_count DESC
    `).all(owlName) as any[];
    return rows.map(rowToLearning);
  }

  reinforce(id: string): void {
    this.db.prepare(`
      UPDATE owl_learnings SET
        reinforcement_count = reinforcement_count + 1,
        confidence = MIN(1.0, confidence + 0.05),
        updated_at = datetime('now')
      WHERE id = ?
    `).run(id);
  }

  private findSimilar(owlName: string, learning: string, category: string): OwlLearning | null {
    // Simple similarity: first 60 chars match within same category + owl
    const prefix = learning.toLowerCase().slice(0, 60);
    const rows = this.db.prepare(`
      SELECT * FROM owl_learnings WHERE owl_name = ? AND category = ? AND LOWER(SUBSTR(learning, 1, 60)) = ?
    `).all(owlName, category, prefix) as any[];
    return rows.length > 0 ? rowToLearning(rows[0]) : null;
  }
}

class TaskStatesRepo {
  constructor(private db: Database.Database) {}

  /** Get or create a task state for a session */
  get(sessionId: string): TaskState | null {
    const row = this.db.prepare(
      "SELECT * FROM task_states WHERE session_id = ?"
    ).get(sessionId) as any;
    return row ? rowToTaskState(row) : null;
  }

  /** Upsert the full task state */
  save(state: TaskState): void {
    this.db.prepare(`
      INSERT INTO task_states
        (session_id, owl_name, goal, planned_approaches, eliminated_approaches, step_log, status, created_at, updated_at)
      VALUES (?,?,?,?,?,?,?,?,datetime('now'))
      ON CONFLICT(session_id) DO UPDATE SET
        goal                  = excluded.goal,
        planned_approaches    = excluded.planned_approaches,
        eliminated_approaches = excluded.eliminated_approaches,
        step_log              = excluded.step_log,
        status                = excluded.status,
        updated_at            = datetime('now')
    `).run(
      state.sessionId,
      state.owlName,
      state.goal,
      JSON.stringify(state.plannedApproaches),
      JSON.stringify(state.eliminatedApproaches),
      JSON.stringify(state.stepLog.slice(0, 30)),  // keep last 30 steps max
      state.status,
      state.createdAt,
    );
  }

  /** Append a step to the log without rewriting the full state */
  appendStep(sessionId: string, step: string): void {
    const existing = this.get(sessionId);
    if (!existing) return;
    existing.stepLog.unshift(step);
    this.save(existing);
  }

  /** Mark an approach as eliminated (failed — do not retry) */
  eliminateApproach(sessionId: string, approach: string): void {
    const existing = this.get(sessionId);
    if (!existing) return;
    if (!existing.eliminatedApproaches.includes(approach)) {
      existing.eliminatedApproaches.push(approach);
    }
    this.save(existing);
  }
}

class SynthesisMemoryRepo {
  constructor(private db: Database.Database) {}

  /** Record a new synthesis event immediately after a skill/tool is created */
  record(entry: {
    owlName: string;
    capabilityDescription: string;
    synthesisApproach: SynthesisRecord["synthesisApproach"];
    toolsItUses?: string[];
    outputPath?: string;
    creationReasoning?: string;
    whatFailedFirst?: string;
    sourceSessionId?: string;
  }): void {
    const id = `syn_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    this.db.prepare(`
      INSERT INTO synthesis_memory
        (id, owl_name, capability_description, synthesis_approach, tools_it_uses,
         output_path, creation_reasoning, what_failed_first, source_session_id)
      VALUES (?,?,?,?,?,?,?,?,?)
    `).run(
      id,
      entry.owlName,
      entry.capabilityDescription.slice(0, 500),
      entry.synthesisApproach,
      JSON.stringify(entry.toolsItUses ?? []),
      entry.outputPath ?? null,
      entry.creationReasoning ? entry.creationReasoning.slice(0, 600) : null,
      entry.whatFailedFirst ? entry.whatFailedFirst.slice(0, 300) : null,
      entry.sourceSessionId ?? null,
    );
  }

  /** Called each time a synthesized skill/tool is invoked */
  recordUse(outputPath: string, success: boolean): void {
    const col = success ? "success_count" : "fail_count";
    this.db.prepare(`
      UPDATE synthesis_memory SET
        ${col} = ${col} + 1,
        last_used_at = datetime('now')
      WHERE output_path = ? AND status = 'active'
    `).run(outputPath);

    // Auto-retire if fail_count >= 5
    if (!success) {
      this.db.prepare(`
        UPDATE synthesis_memory SET status = 'retired'
        WHERE output_path = ? AND fail_count >= 5
      `).run(outputPath);
    }
  }

  /** Get all active synthesis records for an owl — used for self-knowledge injection */
  getActiveForOwl(owlName: string): SynthesisRecord[] {
    const rows = this.db.prepare(`
      SELECT * FROM synthesis_memory
      WHERE owl_name = ? AND status = 'active'
      ORDER BY success_count DESC, created_at DESC
      LIMIT 20
    `).all(owlName) as any[];
    return rows.map(rowToSynthesis);
  }

  /** Get all active records across all owls — for cross-owl self-knowledge */
  getAllActive(): SynthesisRecord[] {
    const rows = this.db.prepare(`
      SELECT * FROM synthesis_memory WHERE status = 'active'
      ORDER BY success_count DESC, created_at DESC LIMIT 30
    `).all() as any[];
    return rows.map(rowToSynthesis);
  }
}

class PromptOptimizationRepo {
  constructor(private db: Database.Database) {}

  store(
    owlName: string,
    originalPrompt: string,
    improvedPrompt: string,
    critique?: string,
    winnerScore?: number,
    trajectoriesUsed = 0,
  ): string {
    const id = uuidv4();
    this.db.prepare(`
      INSERT INTO prompt_optimization_log
        (id, owl_name, original_prompt, improved_prompt, critique, winner_score, trajectories_used)
      VALUES (?,?,?,?,?,?,?)
    `).run(
      id, owlName,
      originalPrompt.slice(0, 8000),
      improvedPrompt.slice(0, 8000),
      critique ? critique.slice(0, 2000) : null,
      winnerScore ?? null,
      trajectoriesUsed,
    );
    return id;
  }

  markApplied(id: string): void {
    this.db.prepare(`UPDATE prompt_optimization_log SET applied = 1 WHERE id = ?`).run(id);
  }

  /** Get the most recent unapplied optimization for an owl */
  getPendingForOwl(owlName: string): PromptOptimizationRecord | undefined {
    const row = this.db.prepare(`
      SELECT * FROM prompt_optimization_log
      WHERE owl_name = ? AND applied = 0
      ORDER BY created_at DESC LIMIT 1
    `).get(owlName) as any;
    return row ? rowToPromptOptimization(row) : undefined;
  }

  /** Timestamp of the last optimization run for this owl (applied or not) */
  getLastRunAt(owlName: string): string | undefined {
    const row = this.db.prepare(`
      SELECT created_at FROM prompt_optimization_log
      WHERE owl_name = ? ORDER BY created_at DESC LIMIT 1
    `).get(owlName) as any;
    return row?.created_at ?? undefined;
  }

  getRecent(owlName: string, limit = 10): PromptOptimizationRecord[] {
    const rows = this.db.prepare(`
      SELECT * FROM prompt_optimization_log WHERE owl_name = ?
      ORDER BY created_at DESC LIMIT ?
    `).all(owlName, limit) as any[];
    return rows.map(rowToPromptOptimization);
  }
}

class ApproachLibraryRepo {
  constructor(private db: Database.Database) {}

  record(
    owlName: string,
    toolName: string,
    taskKeywords: string,
    argsSummary: string,
    outcome: "success" | "failure",
    failureReason?: string,
  ): void {
    this.db.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, failure_reason)
      VALUES (?,?,?,?,?,?,?)
    `).run(
      uuidv4(), owlName, toolName,
      taskKeywords.slice(0, 200),
      argsSummary.slice(0, 300),
      outcome,
      failureReason ? failureReason.slice(0, 400) : null,
    );
  }

  /** Returns recent failures for a specific tool — used to warn the model before it tries again */
  getRecentFailuresForTool(toolName: string, limit = 5): ApproachRecord[] {
    const rows = this.db.prepare(`
      SELECT * FROM approach_library
      WHERE tool_name = ? AND outcome = 'failure'
      ORDER BY created_at DESC LIMIT ?
    `).all(toolName, limit) as any[];
    return rows.map(rowToApproach);
  }

  /** Returns all recent failures for an owl — used to build a "what not to try" summary */
  getRecentFailures(owlName: string, limit = 10): ApproachRecord[] {
    const rows = this.db.prepare(`
      SELECT * FROM approach_library
      WHERE owl_name = ? AND outcome = 'failure'
      ORDER BY created_at DESC LIMIT ?
    `).all(owlName, limit) as any[];
    return rows.map(rowToApproach);
  }

  /** Returns recent successful approaches — used to inform the PLAN phase */
  getRecentSuccesses(toolName: string, limit = 3): ApproachRecord[] {
    const rows = this.db.prepare(`
      SELECT * FROM approach_library
      WHERE tool_name = ? AND outcome = 'success'
      ORDER BY created_at DESC LIMIT ?
    `).all(toolName, limit) as any[];
    return rows.map(rowToApproach);
  }
}

class ParliamentVerdictsRepo {
  constructor(private db: Database.Database) {}

  /** Record a new Parliament verdict */
  record(
    sessionId: string,
    topic: string,
    verdict: ParliamentVerdictSignal,
    participants: string[],
    synthesis?: string,
  ): string {
    const id = uuidv4();
    this.db.prepare(`
      INSERT INTO parliament_verdicts (id, session_id, topic, verdict, participants, synthesis)
      VALUES (?,?,?,?,?,?)
    `).run(
      id, sessionId,
      topic.slice(0, 400),
      verdict,
      JSON.stringify(participants),
      synthesis ? synthesis.slice(0, 1000) : null,
    );
    return id;
  }

  /** Validate a verdict when the trajectory outcome is known */
  validate(
    id: string,
    validationSignal: ParliamentValidationSignal,
    validationReward: number,
  ): void {
    this.db.prepare(`
      UPDATE parliament_verdicts
      SET validated = 1, validation_signal = ?, validation_reward = ?
      WHERE id = ?
    `).run(validationSignal, validationReward, id);
  }

  /**
   * Find past verdicts on topics similar to the given query.
   * Simple keyword overlap — no embeddings needed for MVP.
   */
  findRelated(topic: string, limit = 5): ParliamentVerdictRecord[] {
    // Tokenize topic into meaningful words
    const words = topic
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((w) => w.length >= 4)
      .slice(0, 10);

    if (words.length === 0) {
      const rows = this.db.prepare(
        `SELECT * FROM parliament_verdicts ORDER BY created_at DESC LIMIT ?`
      ).all(limit) as any[];
      return rows.map(rowToParliamentVerdict);
    }

    // SQLite LIKE query per keyword — combine with OR
    const conditions = words.map(() => `topic LIKE ?`).join(" OR ");
    const params = words.map((w) => `%${w}%`);
    const rows = this.db.prepare(
      `SELECT * FROM parliament_verdicts WHERE (${conditions}) ORDER BY created_at DESC LIMIT ?`
    ).all(...params, limit) as any[];
    return rows.map(rowToParliamentVerdict);
  }

  /** Get all unvalidated verdicts that need outcome tracking */
  getPendingValidation(limit = 20): ParliamentVerdictRecord[] {
    const rows = this.db.prepare(
      `SELECT * FROM parliament_verdicts WHERE validated = 0 ORDER BY created_at ASC LIMIT ?`
    ).all(limit) as any[];
    return rows.map(rowToParliamentVerdict);
  }

  getRecent(limit = 20): ParliamentVerdictRecord[] {
    const rows = this.db.prepare(
      `SELECT * FROM parliament_verdicts ORDER BY created_at DESC LIMIT ?`
    ).all(limit) as any[];
    return rows.map(rowToParliamentVerdict);
  }
}

class TrajectoriesRepo {
  constructor(private db: Database.Database) {}

  /** Start a new trajectory — returns the trajectory id */
  begin(
    sessionId: string,
    owlName: string,
    userMessage: string,
    userId?: string,
  ): string {
    const id = uuidv4();
    this.db.prepare(`
      INSERT INTO trajectories (id, session_id, owl_name, user_id, user_message)
      VALUES (?,?,?,?,?)
    `).run(id, sessionId, owlName, userId ?? null, userMessage.slice(0, 500));
    return id;
  }

  /** Record one tool invocation turn */
  recordTurn(
    trajectoryId: string,
    turnIndex: number,
    toolName: string,
    argsSnapshot: string,
    resultSnapshot: string,
    success: boolean,
    durationMs?: number,
  ): void {
    this.db.prepare(`
      INSERT INTO trajectory_turns
        (id, trajectory_id, turn_index, tool_name, args_snapshot, result_snapshot, success, duration_ms)
      VALUES (?,?,?,?,?,?,?,?)
    `).run(
      uuidv4(), trajectoryId, turnIndex, toolName,
      argsSnapshot.slice(0, 300),
      resultSnapshot.slice(0, 400),
      success ? 1 : 0,
      durationMs ?? null,
    );
  }

  /** Finalize the trajectory with outcome + reward after the ReAct loop ends */
  complete(
    trajectoryId: string,
    outcome: Trajectory["outcome"],
    reward: number,
    rewardBreakdown: Record<string, number>,
    toolsUsed: string[],
    totalTurns: number,
  ): void {
    this.db.prepare(`
      UPDATE trajectories
      SET outcome = ?, reward = ?, reward_breakdown = ?,
          tools_used = ?, total_turns = ?, completed_at = datetime('now')
      WHERE id = ?
    `).run(
      outcome,
      Math.max(-1, Math.min(1, reward)), // clamp to [-1, 1]
      JSON.stringify(rewardBreakdown),
      JSON.stringify([...new Set(toolsUsed)]),
      totalTurns,
      trajectoryId,
    );
  }

  /**
   * Update reward when a 👍/👎 signal arrives after the response was sent.
   * Applies the delta on top of whatever reward was computed at loop end.
   */
  applyFeedback(sessionId: string, signal: "like" | "dislike"): void {
    const delta = signal === "like" ? 0.5 : -0.5;
    const row = this.db.prepare(`
      SELECT id, reward, reward_breakdown FROM trajectories
      WHERE session_id = ? ORDER BY created_at DESC LIMIT 1
    `).get(sessionId) as any;
    if (!row) return;

    const breakdown: Record<string, number> = JSON.parse(row.reward_breakdown ?? "{}");
    breakdown["feedback"] = delta;
    const newReward = Math.max(-1, Math.min(1, (row.reward as number) + delta));
    this.db.prepare(`
      UPDATE trajectories SET reward = ?, reward_breakdown = ? WHERE id = ?
    `).run(newReward, JSON.stringify(breakdown), row.id);
  }

  /** Recent trajectories for this owl — used by APO to find bad runs */
  getRecent(owlName: string, limit = 20): Trajectory[] {
    const rows = this.db.prepare(`
      SELECT * FROM trajectories WHERE owl_name = ?
      ORDER BY created_at DESC LIMIT ?
    `).all(owlName, limit) as any[];
    return rows.map(rowToTrajectory);
  }

  /** Trajectories with reward below threshold — APO critique targets */
  getLowReward(owlName: string, limit = 10, threshold = -0.1): Trajectory[] {
    const rows = this.db.prepare(`
      SELECT * FROM trajectories WHERE owl_name = ? AND reward < ?
      ORDER BY reward ASC LIMIT ?
    `).all(owlName, threshold, limit) as any[];
    return rows.map(rowToTrajectory);
  }

  /** All turns for a given trajectory — for detailed APO critique */
  getTurns(trajectoryId: string): TrajectoryTurn[] {
    const rows = this.db.prepare(`
      SELECT * FROM trajectory_turns WHERE trajectory_id = ?
      ORDER BY turn_index ASC
    `).all(trajectoryId) as any[];
    return rows.map(rowToTrajectoryTurn);
  }
}

// ─── Row mappers ──────────────────────────────────────────────────

function rowToMessage(r: any): ChatMessage {
  return {
    role: r.role,
    content: r.content ?? "",
    ...(r.tool_calls ? { toolCalls: JSON.parse(r.tool_calls) } : {}),
    ...(r.tool_call_id ? { toolCallId: r.tool_call_id } : {}),
    ...(r.name ? { name: r.name } : {}),
  };
}

function rowToFact(r: any): Fact {
  return {
    id: r.id,
    userId: r.user_id,
    owlName: r.owl_name,
    fact: r.fact,
    entity: r.entity ?? undefined,
    category: r.category,
    confidence: r.confidence,
    source: r.source,
    embedding: r.embedding ? JSON.parse(r.embedding) : undefined,
    accessCount: r.access_count,
    expiresAt: r.expires_at ?? undefined,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

function rowToSummary(r: any): Summary {
  return {
    id: r.id,
    sessionId: r.session_id,
    userId: r.user_id,
    owlName: r.owl_name,
    fromSeq: r.from_seq,
    toSeq: r.to_seq,
    messageCount: r.message_count,
    summaryText: r.summary_text,
    task: r.task ?? undefined,
    accomplished: r.accomplished ?? undefined,
    keyFacts: JSON.parse(r.key_facts ?? "[]"),
    decisions: JSON.parse(r.decisions ?? "[]"),
    failedApproaches: JSON.parse(r.failed_approaches ?? "[]"),
    openQuestions: JSON.parse(r.open_questions ?? "[]"),
    tokensSaved: r.tokens_saved,
    createdAt: r.created_at,
  };
}

function rowToEpisode(r: any): Episode {
  return {
    id: r.id,
    sessionId: r.session_id,
    userId: r.user_id,
    owlName: r.owl_name,
    summary: r.summary,
    keyFacts: JSON.parse(r.key_facts ?? "[]"),
    topics: JSON.parse(r.topics ?? "[]"),
    sentiment: r.sentiment,
    importance: r.importance,
    embedding: r.embedding ? JSON.parse(r.embedding) : undefined,
    createdAt: r.created_at,
  };
}

function rowToDigest(r: any): Digest {
  return {
    sessionId: r.session_id,
    userId: r.user_id,
    task: r.task ?? "",
    artifacts: JSON.parse(r.artifacts ?? "[]"),
    decisions: JSON.parse(r.decisions ?? "[]"),
    failed: JSON.parse(r.failed ?? "[]"),
    openQuestions: JSON.parse(r.open_questions ?? "[]"),
    updatedAt: r.updated_at,
  };
}

function rowToAttempt(r: any): Attempt {
  return {
    id: r.id,
    sessionId: r.session_id,
    userId: r.user_id,
    owlName: r.owl_name,
    turn: r.turn,
    toolName: r.tool_name,
    argsSummary: r.args_summary ?? undefined,
    outcome: r.outcome,
    resultSummary: r.result_summary ?? undefined,
    createdAt: r.created_at,
  };
}

function rowToFeedback(r: any): FeedbackRecord {
  return {
    id: r.id,
    sessionId: r.session_id,
    userId: r.user_id,
    owlName: r.owl_name,
    signal: r.signal,
    userMessage: r.user_message ?? undefined,
    assistantSummary: r.assistant_summary ?? undefined,
    toolsUsed: JSON.parse(r.tools_used ?? "[]"),
    createdAt: r.created_at,
  };
}

function rowToLearning(r: any): OwlLearning {
  return {
    id: r.id,
    owlName: r.owl_name,
    learning: r.learning,
    category: r.category,
    confidence: r.confidence,
    reinforcementCount: r.reinforcement_count,
    sourceSessionId: r.source_session_id ?? undefined,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

function rowToTaskState(r: any): TaskState {
  return {
    sessionId: r.session_id,
    owlName: r.owl_name,
    goal: r.goal,
    plannedApproaches: JSON.parse(r.planned_approaches ?? "[]"),
    eliminatedApproaches: JSON.parse(r.eliminated_approaches ?? "[]"),
    stepLog: JSON.parse(r.step_log ?? "[]"),
    status: r.status,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

function rowToSynthesis(r: any): SynthesisRecord {
  return {
    id: r.id,
    owlName: r.owl_name,
    capabilityDescription: r.capability_description,
    synthesisApproach: r.synthesis_approach,
    toolsItUses: JSON.parse(r.tools_it_uses ?? "[]"),
    outputPath: r.output_path ?? undefined,
    creationReasoning: r.creation_reasoning ?? undefined,
    whatFailedFirst: r.what_failed_first ?? undefined,
    successCount: r.success_count,
    failCount: r.fail_count,
    status: r.status,
    sourceSessionId: r.source_session_id ?? undefined,
    createdAt: r.created_at,
    lastUsedAt: r.last_used_at ?? undefined,
  };
}

function rowToApproach(r: any): ApproachRecord {
  return {
    id: r.id,
    owlName: r.owl_name,
    toolName: r.tool_name,
    taskKeywords: r.task_keywords,
    argsSummary: r.args_summary,
    outcome: r.outcome,
    failureReason: r.failure_reason ?? undefined,
    createdAt: r.created_at,
  };
}

function rowToParliamentVerdict(r: any): ParliamentVerdictRecord {
  return {
    id: r.id,
    sessionId: r.session_id,
    topic: r.topic,
    verdict: r.verdict,
    synthesis: r.synthesis ?? undefined,
    participants: JSON.parse(r.participants ?? "[]"),
    validated: r.validated,
    validationSignal: r.validation_signal ?? undefined,
    validationReward: r.validation_reward ?? undefined,
    createdAt: r.created_at,
  };
}

function rowToPromptOptimization(r: any): PromptOptimizationRecord {
  return {
    id: r.id,
    owlName: r.owl_name,
    originalPrompt: r.original_prompt,
    improvedPrompt: r.improved_prompt,
    critique: r.critique ?? undefined,
    winnerScore: r.winner_score ?? undefined,
    trajectoriesUsed: r.trajectories_used,
    applied: r.applied,
    createdAt: r.created_at,
  };
}

function rowToTrajectory(r: any): Trajectory {
  return {
    id: r.id,
    sessionId: r.session_id,
    owlName: r.owl_name,
    userId: r.user_id ?? undefined,
    userMessage: r.user_message,
    totalTurns: r.total_turns,
    toolsUsed: JSON.parse(r.tools_used ?? "[]"),
    outcome: r.outcome,
    reward: r.reward,
    rewardBreakdown: JSON.parse(r.reward_breakdown ?? "{}"),
    createdAt: r.created_at,
    completedAt: r.completed_at ?? undefined,
  };
}

function rowToTrajectoryTurn(r: any): TrajectoryTurn {
  return {
    id: r.id,
    trajectoryId: r.trajectory_id,
    turnIndex: r.turn_index,
    toolName: r.tool_name,
    argsSnapshot: r.args_snapshot,
    resultSnapshot: r.result_snapshot,
    success: r.success === 1,
    durationMs: r.duration_ms ?? undefined,
    createdAt: r.created_at,
  };
}

// ─── Helpers ──────────────────────────────────────────────────────

function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length || a.length === 0) return 0;
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  const denom = Math.sqrt(na) * Math.sqrt(nb);
  return denom === 0 ? 0 : dot / denom;
}

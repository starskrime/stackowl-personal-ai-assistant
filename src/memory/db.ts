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
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";
import type { ChatMessage } from "../providers/base.js";
import type { ModelProvider } from "../providers/base.js";

// ─── Schema version — bump when adding columns/tables ───────────
const SCHEMA_VERSION = 34;

// ─── Types ───────────────────────────────────────────────────────

export type FactCategory =
  | "skill" | "preference" | "project_detail" | "personal"
  | "context" | "goal" | "habit" | "relationship" | "decision"
  | "open_question" | "active_goal" | "sub_goal";

export const TIER0_CATEGORIES: FactCategory[] = [
  "preference", "personal", "active_goal", "goal",
  "relationship", "habit", "decision",
];

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

// ─── Routing Persistence Types ────────────────────────────────────

export interface RoutingHistoryEntry {
  ts: string;
  owl: string;
  reason: string;
}

export interface UserProfile {
  userId: string;
  activePin: string | null;
  pinnedAt: string | null;
  trustLevel: "standard" | "elevated" | "restricted";
  stylePref: string | null;
  routingHistory: RoutingHistoryEntry[];
  createdAt: string;
  updatedAt: string;
}

export type OwlTaskStatus = "pending" | "active" | "blocked" | "done" | "abandoned";
export type OwlTaskPriority = "low" | "normal" | "high" | "urgent";

export interface OwlTask {
  id: string;
  userId: string;
  owlName: string;
  title: string;
  description?: string;
  status: OwlTaskStatus;
  priority: OwlTaskPriority;
  sessionId?: string;
  createdAt: string;
  updatedAt: string;
  dueAt?: string;
  result?: string;
}

export type OwlJobType = "proactive" | "monitor" | "research" | "followup";
export type OwlJobStatus = "queued" | "running" | "done" | "failed";

export interface OwlJob {
  id: string;
  taskId?: string;
  userId: string;
  owlName: string;
  type: OwlJobType;
  payload: Record<string, unknown>;
  status: OwlJobStatus;
  scheduledAt: string;
  startedAt?: string;
  completedAt?: string;
  error?: string;
  result?: string;
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

export interface SpecializedOwl {
  id: string;
  ownerId: string;
  name: string;
  specialization: string;
  personalityPrompt: string;
  routingRules: string[];
  dna: OwlDNA;
  isMainOwl: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface OwlDNA {
  challengeLevel: number;
  verbosity: number;
  expertiseDomains: string[];
  routingQuality: number;
  evolutionSpeed: number;
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

export type ParliamentVerdictSignal = "PROCEED" | "HOLD" | "ABORT" | "REVISE" | "REJECT" | "PARLIAMENT_INCONCLUSIVE";
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
  /** Confidence score 0.0–1.0. Warm start 0.6, updated by validator and user signal. */
  confidenceScore: number;
  /** "tactical" | "architectural" — controls decay rate */
  topicClass: string;
  /** Unix timestamp after which this verdict is excluded from recall. null = never. */
  expiresAt?: number;
  /** One-sentence reason from the adversarial validator. */
  validatorReasoning?: string;
  /** JSON: array of {agentName, claim} cited by the synthesizer. */
  agentCitations?: string;
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

// ─── Agent Goals & Tasks ─────────────────────────────────────────

export type GoalStatus = "pending" | "active" | "blocked" | "complete" | "abandoned";
export type TaskStatus = "pending" | "running" | "complete" | "failed" | "blocked";
export type TaskType   = "research" | "search" | "shell" | "synthesize" | "notify" | "analyze";
export type RiskLevel  = "low" | "medium" | "high";

export interface AgentGoal {
  id: string;
  title: string;
  description: string;
  status: GoalStatus;
  priority: number;       // 1-10
  createdBy: "user" | "agent" | "event";
  userId: string;
  deadline?: string;
  progress: number;       // 0-100
  parentId?: string;
  sourceSessionId?: string;
  createdAt: string;
  updatedAt: string;
}

export interface AgentTask {
  id: string;
  goalId: string;
  description: string;
  type: TaskType;
  status: TaskStatus;
  result?: string;
  attempts: number;
  riskLevel: RiskLevel;
  requiresApproval: boolean;
  createdAt: string;
  updatedAt: string;
  completedAt?: string;
}

// ─── OwlQualityRepo ───────────────────────────────────────────────

export class OwlQualityRepo {
  constructor(private db: Database.Database) {}

  get(owlName: string, ownerId: string): { ewmaReward: number; turnCount: number } | null {
    const row = this.db.prepare(
      `SELECT ewma_reward, turn_count FROM owl_quality_metrics WHERE owl_name = ? AND owner_id = ?`
    ).get(owlName, ownerId) as { ewma_reward: number; turn_count: number } | undefined
    return row ? { ewmaReward: row.ewma_reward, turnCount: row.turn_count } : null
  }

  update(owlName: string, ownerId: string, reward: number): void {
    const clampedReward = Math.max(0, Math.min(1, reward))
    const existing = this.get(owlName, ownerId)
    const oldEwma = existing?.ewmaReward ?? 0.7
    const newEwma = 0.15 * clampedReward + 0.85 * oldEwma
    const newCount = (existing?.turnCount ?? 0) + 1
    this.db.prepare(`
      INSERT INTO owl_quality_metrics (owl_name, owner_id, ewma_reward, turn_count, last_updated)
      VALUES (?, ?, ?, ?, datetime('now'))
      ON CONFLICT (owl_name, owner_id) DO UPDATE SET
        ewma_reward  = excluded.ewma_reward,
        turn_count   = excluded.turn_count,
        last_updated = excluded.last_updated
    `).run(owlName, ownerId, newEwma, newCount)
  }
}

// ─── OwlPinsRepo ──────────────────────────────────────────────────

export class OwlPinsRepo {
  constructor(private db: Database.Database) {}

  get(userId: string, channelId: string): string | null {
    const row = this.db.prepare(
      `SELECT owl_name FROM owl_pins WHERE user_id = ? AND channel_id = ?`
    ).get(userId, channelId) as { owl_name: string } | undefined
    if (row) return row.owl_name
    // Fall back to global pin (legacy / cross-channel)
    const global = this.db.prepare(
      `SELECT owl_name FROM owl_pins WHERE user_id = ? AND channel_id = 'global'`
    ).get(userId) as { owl_name: string } | undefined
    return global?.owl_name ?? null
  }

  set(userId: string, channelId: string, owlName: string | null, pinnedAt: string): void {
    if (owlName === null) {
      this.db.prepare(
        `DELETE FROM owl_pins WHERE user_id = ? AND channel_id = ?`
      ).run(userId, channelId)
    } else {
      this.db.prepare(`
        INSERT INTO owl_pins (user_id, channel_id, owl_name, pinned_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (user_id, channel_id) DO UPDATE SET
          owl_name  = excluded.owl_name,
          pinned_at = excluded.pinned_at
      `).run(userId, channelId, owlName, pinnedAt)
    }
  }
}

// ─── OwlRecurringJobsRepo ─────────────────────────────────────────

export interface RecurringJobRow {
  id: string
  helper_name: string
  owner_id: string
  schedule: string
  task_description: string
  channel_id: string
}

export class OwlRecurringJobsRepo {
  constructor(private db: Database.Database) {}

  insert(job: RecurringJobRow): void {
    this.db.prepare(`
      INSERT INTO owl_recurring_jobs (id, helper_name, owner_id, schedule, task_description, channel_id)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run(job.id, job.helper_name, job.owner_id, job.schedule, job.task_description, job.channel_id)
  }

  listByOwner(ownerId: string): RecurringJobRow[] {
    return this.db.prepare(
      `SELECT id, helper_name, owner_id, schedule, task_description, channel_id FROM owl_recurring_jobs WHERE owner_id = ? ORDER BY helper_name`
    ).all(ownerId) as RecurringJobRow[]
  }

  deleteByHelper(helperName: string, ownerId: string): void {
    this.db.prepare(
      `DELETE FROM owl_recurring_jobs WHERE helper_name = ? AND owner_id = ?`
    ).run(helperName, ownerId)
  }
}

// ─── Skill Usage Repo ────────────────────────────────────────────

export interface SkillUsageRow {
  skill_name: string;
  selection_count: number;
  success_count: number;
  failure_count: number;
  avg_duration_ms: number;
  last_used_at: string | null;
}

export class SkillUsageRepo {
  constructor(private db: Database.Database) {}

  upsertSelection(name: string): void {
    this.db.prepare(`
      INSERT INTO skill_usage (skill_name, selection_count, last_used_at)
      VALUES (?, 1, datetime('now'))
      ON CONFLICT(skill_name) DO UPDATE SET
        selection_count = selection_count + 1,
        last_used_at = datetime('now')
    `).run(name);
  }

  recordSuccess(name: string, durationMs: number): void {
    // Ensure the row exists first (selection may not have been called in DB mode).
    // In the ON CONFLICT branch, expressions use pre-update column values, so the
    // running-average denominator is (success_count + 1) — the value *after* increment.
    this.db.prepare(`
      INSERT INTO skill_usage (skill_name, success_count, avg_duration_ms)
      VALUES (?, 1, ?)
      ON CONFLICT(skill_name) DO UPDATE SET
        success_count   = success_count + 1,
        avg_duration_ms = (avg_duration_ms * success_count + ?) / (success_count + 1)
    `).run(name, durationMs, durationMs);
  }

  recordFailure(name: string): void {
    this.db.prepare(`
      INSERT INTO skill_usage (skill_name, failure_count)
      VALUES (?, 1)
      ON CONFLICT(skill_name) DO UPDATE SET
        failure_count = failure_count + 1
    `).run(name);
  }

  getStats(name: string): SkillUsageRow | null {
    return this.db.prepare(
      `SELECT * FROM skill_usage WHERE skill_name = ?`
    ).get(name) as SkillUsageRow | null;
  }
}

export class ActivityGateRepo {
  constructor(private db: Database.Database) {}

  getHash(jobId: string): string | null {
    const row = this.db.prepare(
      "SELECT last_seen_hash FROM activity_gate WHERE job_id = ?"
    ).get(jobId) as { last_seen_hash: string | null } | undefined;
    return row?.last_seen_hash ?? null;
  }

  setHash(jobId: string, hash: string): void {
    this.db.prepare(
      "INSERT INTO activity_gate (job_id, last_seen_hash) VALUES (?, ?) " +
      "ON CONFLICT(job_id) DO UPDATE SET last_seen_hash = excluded.last_seen_hash"
    ).run(jobId, hash);
  }
}

// ─── MemoryDatabase ───────────────────────────────────────────────

export class MemoryDatabase {
  private db: Database.Database;
  private dbPath: string | null;
  get rawDb(): Database.Database { return this.db }

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
  readonly agentGoals: AgentGoalsRepo;
  readonly agentTasks: AgentTasksRepo;
  readonly userProfiles: UserProfilesRepo;
  readonly owlTasks: TasksRepo;
  readonly owlJobs: JobsRepo;
  readonly owlQualityMetrics: OwlQualityRepo;
  readonly owlPins: OwlPinsRepo;
  readonly owlRecurringJobs: OwlRecurringJobsRepo;
  readonly skillUsage: SkillUsageRepo;
  readonly activityGate: ActivityGateRepo;

  constructor(workspacePath: string) {
    const dbDir = join(workspacePath, "memory");
    if (!existsSync(dbDir)) mkdirSync(dbDir, { recursive: true });

    const dbPath = join(dbDir, "stackowl.db");
    this.dbPath = dbPath;
    this.db = new Database(dbPath);

    // Performance pragmas
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("synchronous = NORMAL");
    this.db.pragma("foreign_keys = ON");

    // Pre-flight backup before v25 — recoverable sidecar of pre-Element-15 state.
    const currentVersion =
      (this.db.pragma("user_version") as { user_version: number }[])[0]?.user_version ?? 0;
    if (currentVersion < 25 && this.dbPath) {
      backupBeforeV25(this.dbPath);
    }

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
    this.agentGoals         = new AgentGoalsRepo(this.db);
    this.agentTasks         = new AgentTasksRepo(this.db);
    this.userProfiles      = new UserProfilesRepo(this.db);
    this.owlTasks          = new TasksRepo(this.db);
    this.owlJobs           = new JobsRepo(this.db);
    this.owlQualityMetrics = new OwlQualityRepo(this.db);
    this.owlPins           = new OwlPinsRepo(this.db);
    this.owlRecurringJobs  = new OwlRecurringJobsRepo(this.db);
    this.skillUsage        = new SkillUsageRepo(this.db);
    this.activityGate      = new ActivityGateRepo(this.db);

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
        id                  TEXT PRIMARY KEY,
        trajectory_id       TEXT NOT NULL REFERENCES trajectories(id),
        turn_index          INTEGER NOT NULL,
        tool_name           TEXT NOT NULL,
        args_snapshot       TEXT NOT NULL DEFAULT '',
        result_snapshot     TEXT NOT NULL DEFAULT '',
        success             INTEGER NOT NULL DEFAULT 1,
        duration_ms         INTEGER,
        verification_result TEXT,
        verifier_reason     TEXT,
        subgoal_id          TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now'))
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
        confidence_score    REAL NOT NULL DEFAULT 0.6,
        topic_class         TEXT NOT NULL DEFAULT 'tactical',
        expires_at          INTEGER,
        validator_reasoning TEXT,
        agent_citations     TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_pv_topic       ON parliament_verdicts(topic);
      CREATE INDEX IF NOT EXISTS idx_pv_validated   ON parliament_verdicts(validated);

      CREATE TABLE IF NOT EXISTS agent_goals (
        id                TEXT PRIMARY KEY,
        title             TEXT NOT NULL,
        description       TEXT NOT NULL,
        status            TEXT NOT NULL DEFAULT 'pending',
        priority          INTEGER NOT NULL DEFAULT 5,
        created_by        TEXT NOT NULL DEFAULT 'user',
        user_id           TEXT NOT NULL DEFAULT 'default',
        deadline          TEXT,
        progress          INTEGER NOT NULL DEFAULT 0,
        parent_id         TEXT,
        source_session_id TEXT,
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_ag_status   ON agent_goals(status);
      CREATE INDEX IF NOT EXISTS idx_ag_priority ON agent_goals(priority DESC);
      CREATE INDEX IF NOT EXISTS idx_ag_user     ON agent_goals(user_id);

      CREATE TABLE IF NOT EXISTS agent_tasks (
        id                TEXT PRIMARY KEY,
        goal_id           TEXT NOT NULL REFERENCES agent_goals(id),
        description       TEXT NOT NULL,
        type              TEXT NOT NULL DEFAULT 'research',
        status            TEXT NOT NULL DEFAULT 'pending',
        result            TEXT,
        attempts          INTEGER NOT NULL DEFAULT 0,
        risk_level        TEXT NOT NULL DEFAULT 'low',
        requires_approval INTEGER NOT NULL DEFAULT 0,
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at      TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_at_goal    ON agent_tasks(goal_id);
      CREATE INDEX IF NOT EXISTS idx_at_status  ON agent_tasks(status);

      -- Tool Cortex: workspace-scoped synthesized tools with lifecycle management
      CREATE TABLE IF NOT EXISTS workspace_tools (
        tool_name     TEXT PRIMARY KEY,
        state         TEXT NOT NULL DEFAULT 'SHADOW',
        source_code   TEXT NOT NULL,
        promoted_at   TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
      );

      -- Scheduled jobs for durable notification + reminders
      CREATE TABLE IF NOT EXISTS scheduled_jobs (
        id           TEXT PRIMARY KEY,
        type         TEXT NOT NULL CHECK(type IN ('remind', 'repeat')),
        message      TEXT NOT NULL,
        schedule_at  TEXT,
        interval_ms  INTEGER,
        next_fire_at TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        status       TEXT NOT NULL DEFAULT 'active'
                       CHECK(status IN ('active', 'fired', 'cancelled', 'expired')),
        metadata     TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due ON scheduled_jobs(next_fire_at, status);

      CREATE TABLE IF NOT EXISTS sessions (
        id              TEXT PRIMARY KEY,
        parent_id       TEXT,
        status          TEXT NOT NULL CHECK(status IN ('pending', 'running', 'awaiting_input', 'completed', 'terminated', 'failed')),
        prompt          TEXT NOT NULL,
        history_json    TEXT,
        result          TEXT,
        error           TEXT,
        metadata        TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        terminated_at   TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, updated_at);
      CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);

      CREATE TABLE IF NOT EXISTS session_messages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   TEXT NOT NULL,
        direction    TEXT NOT NULL CHECK(direction IN ('to_session', 'from_session')),
        content      TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        consumed_at  TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
      );
      CREATE INDEX IF NOT EXISTS idx_session_messages_pending ON session_messages(session_id, consumed_at);
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
    if (current < 8) {
      // v8: persistent agent goals + tasks (agentic loop foundation)
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS agent_goals (
          id                TEXT PRIMARY KEY,
          title             TEXT NOT NULL,
          description       TEXT NOT NULL,
          status            TEXT NOT NULL DEFAULT 'pending',
          priority          INTEGER NOT NULL DEFAULT 5,
          created_by        TEXT NOT NULL DEFAULT 'user',
          user_id           TEXT NOT NULL DEFAULT 'default',
          deadline          TEXT,
          progress          INTEGER NOT NULL DEFAULT 0,
          parent_id         TEXT,
          source_session_id TEXT,
          created_at        TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ag_status   ON agent_goals(status);
        CREATE INDEX IF NOT EXISTS idx_ag_priority ON agent_goals(priority DESC);
        CREATE INDEX IF NOT EXISTS idx_ag_user     ON agent_goals(user_id);

        CREATE TABLE IF NOT EXISTS agent_tasks (
          id                TEXT PRIMARY KEY,
          goal_id           TEXT NOT NULL REFERENCES agent_goals(id),
          description       TEXT NOT NULL,
          type              TEXT NOT NULL DEFAULT 'research',
          status            TEXT NOT NULL DEFAULT 'pending',
          result            TEXT,
          attempts          INTEGER NOT NULL DEFAULT 0,
          risk_level        TEXT NOT NULL DEFAULT 'low',
          requires_approval INTEGER NOT NULL DEFAULT 0,
          created_at        TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
          completed_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_at_goal    ON agent_tasks(goal_id);
        CREATE INDEX IF NOT EXISTS idx_at_status  ON agent_tasks(status);
      `);
    }
    if (current < 9) {
      // v9: (reserved - was agent goals/tasks)
    }
    if (current < 10) {
      // v10: user-created specialized owls (tenant-isolated)
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS owls (
          id                  TEXT PRIMARY KEY,
          owner_id            TEXT NOT NULL,
          name                TEXT NOT NULL,
          specialization      TEXT NOT NULL,
          personality_prompt TEXT NOT NULL,
          routing_rules       TEXT NOT NULL DEFAULT '[]',
          dna                 TEXT NOT NULL DEFAULT '{}',
          is_main_owl         INTEGER NOT NULL DEFAULT 0,
          created_at          TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_owls_owner ON owls(owner_id);
        CREATE INDEX IF NOT EXISTS idx_owls_name  ON owls(owner_id, name);
      `);
    }
    if (current < 11) {
      // v11: delivery log — every outbound message attempt via DeliveryRouter
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS delivery_log (
          id           TEXT PRIMARY KEY,
          envelope_id  TEXT NOT NULL,
          user_id      TEXT NOT NULL,
          channel_id   TEXT NOT NULL,
          urgency      TEXT NOT NULL,
          trigger      TEXT NOT NULL,
          status       TEXT NOT NULL,
          attempt      INTEGER NOT NULL,
          error        TEXT,
          delivered_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_dl_user    ON delivery_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_dl_channel ON delivery_log(channel_id);
        CREATE INDEX IF NOT EXISTS idx_dl_status  ON delivery_log(status);
      `);
    }
    if (current < 12) {
      // v12: OwlBrain routing persistence — user profiles, task ownership, job queue
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS user_profiles (
          user_id      TEXT PRIMARY KEY,
          active_pin   TEXT,
          pinned_at    TEXT,
          trust_level  TEXT NOT NULL DEFAULT 'standard',
          style_pref   TEXT,
          routing_json TEXT NOT NULL DEFAULT '[]',
          created_at   TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS owl_tasks (
          id           TEXT PRIMARY KEY,
          user_id      TEXT NOT NULL,
          owl_name     TEXT NOT NULL,
          title        TEXT NOT NULL,
          description  TEXT,
          status       TEXT NOT NULL DEFAULT 'pending',
          priority     TEXT NOT NULL DEFAULT 'normal',
          session_id   TEXT,
          due_at       TEXT,
          result       TEXT,
          created_at   TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_owl_tasks_user ON owl_tasks(user_id, status);

        CREATE TABLE IF NOT EXISTS owl_jobs (
          id           TEXT PRIMARY KEY,
          task_id      TEXT REFERENCES owl_tasks(id),
          user_id      TEXT NOT NULL,
          owl_name     TEXT NOT NULL,
          type         TEXT NOT NULL,
          payload      TEXT NOT NULL DEFAULT '{}',
          status       TEXT NOT NULL DEFAULT 'queued',
          scheduled_at TEXT NOT NULL,
          started_at   TEXT,
          completed_at TEXT,
          error        TEXT,
          result       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_owl_jobs_status ON owl_jobs(status, scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_owl_jobs_user   ON owl_jobs(user_id, status);
      `);
    }
    if (current < 13) {
      // v13: ContextPipeline — user persona cache + pellets tag index
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS user_personas (
          user_id        TEXT PRIMARY KEY,
          persona_json   TEXT NOT NULL,
          synthesized_at TEXT NOT NULL,
          expires_at     INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pellets (
          id            TEXT PRIMARY KEY,
          tag           TEXT NOT NULL DEFAULT '',
          title         TEXT NOT NULL DEFAULT '',
          content       TEXT NOT NULL DEFAULT '',
          created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_pellets_tag ON pellets(tag);
      `);
    }
    if (current < 14) {
      // v14: OwlEngine v2 — task ledgers, HITL checkpoints, approach patterns,
      // extended trajectory quality fields
      this.db.exec(`
        ALTER TABLE trajectories ADD COLUMN quality_score REAL DEFAULT NULL;
        ALTER TABLE trajectories ADD COLUMN quality_flags TEXT DEFAULT '[]';
        ALTER TABLE trajectories ADD COLUMN task_category TEXT DEFAULT NULL;
        ALTER TABLE trajectories ADD COLUMN task_complexity TEXT DEFAULT NULL;
        ALTER TABLE trajectories ADD COLUMN degradation_tier INTEGER DEFAULT 1;
        ALTER TABLE trajectories ADD COLUMN recovery_actions TEXT DEFAULT '[]';
        ALTER TABLE trajectories ADD COLUMN follow_up_sentiment TEXT DEFAULT NULL;
        ALTER TABLE trajectories ADD COLUMN follow_up_updated_at TEXT DEFAULT NULL;

        CREATE TABLE IF NOT EXISTS task_ledgers (
          id             TEXT PRIMARY KEY,
          session_id     TEXT NOT NULL,
          user_id        TEXT NOT NULL DEFAULT 'default',
          goal           TEXT NOT NULL,
          sub_goals      TEXT NOT NULL DEFAULT '[]',
          expected_output TEXT NOT NULL DEFAULT '',
          complexity     TEXT NOT NULL DEFAULT 'medium',
          status         TEXT NOT NULL DEFAULT 'active',
          revisions      TEXT NOT NULL DEFAULT '[]',
          created_at     TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ledgers_session
          ON task_ledgers(session_id);
        CREATE INDEX IF NOT EXISTS idx_ledgers_user_status
          ON task_ledgers(user_id, status);

        CREATE TABLE IF NOT EXISTS hitl_checkpoints (
          id             TEXT PRIMARY KEY,
          session_id     TEXT NOT NULL,
          ledger_id      TEXT NOT NULL,
          pending_action TEXT NOT NULL,
          request_kind   TEXT NOT NULL,
          memo_json      TEXT NOT NULL,
          status         TEXT NOT NULL DEFAULT 'waiting',
          response_json  TEXT DEFAULT NULL,
          created_at     TEXT NOT NULL DEFAULT (datetime('now')),
          resolved_at    TEXT DEFAULT NULL,
          expires_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hitl_session
          ON hitl_checkpoints(session_id, status);

        CREATE TABLE IF NOT EXISTS approach_patterns (
          id                   TEXT PRIMARY KEY,
          task_category        TEXT NOT NULL,
          lesson               TEXT NOT NULL,
          successful_sequences TEXT NOT NULL DEFAULT '[]',
          conditions           TEXT NOT NULL DEFAULT '[]',
          observation_count    INTEGER NOT NULL DEFAULT 0,
          success_rate         REAL NOT NULL DEFAULT 0.0,
          status               TEXT NOT NULL DEFAULT 'tentative',
          last_used_at         TEXT DEFAULT NULL,
          created_at           TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_patterns_category_status
          ON approach_patterns(task_category, status);
      `);
    }
    if (current < 15) {
      // v15: persist TaskLedger extras (estimatedTurns, behavioralConstraints,
      // approachPatterns, parliamentContext, reflexionContext)
      this.db.exec(`
        ALTER TABLE task_ledgers ADD COLUMN extras TEXT DEFAULT '{}';
      `);
    }
    if (current < 16) {
      // v16: GAV verifier columns on trajectory_turns + workspace_tools table.
      // Fresh DBs already have these columns via createSchema(); the ALTER TABLE
      // statements are only needed for existing pre-v16 databases.
      try { this.db.exec(`ALTER TABLE trajectory_turns ADD COLUMN verification_result TEXT`); } catch (err) { log.memory.warn("db migration: ALTER TABLE trajectory_turns verification_result (may already exist)", err); }
      try { this.db.exec(`ALTER TABLE trajectory_turns ADD COLUMN verifier_reason TEXT`); } catch (err) { log.memory.warn("db migration: ALTER TABLE trajectory_turns verifier_reason (may already exist)", err); }
      try { this.db.exec(`ALTER TABLE trajectory_turns ADD COLUMN subgoal_id TEXT`); } catch (err) { log.memory.warn("db migration: ALTER TABLE trajectory_turns subgoal_id (may already exist)", err); }
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS workspace_tools (
          tool_name     TEXT PRIMARY KEY,
          state         TEXT NOT NULL DEFAULT 'SHADOW',
          source_code   TEXT NOT NULL,
          promoted_at   TEXT,
          created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
      `);
    }
    if (current < 17) {
      // v17: owl intelligence tables — task ledger, reflexion critiques,
      // skill templates, outcome journal; plus new columns on facts.
      applyV17Migration(this.db);
      this.db.pragma('user_version = 17');
    }
    if (current < 18) {
      applyV18Migration(this.db);
      this.db.pragma('user_version = 18');
    }
    if (current < 19) {
      applyV19Migration(this.db);
      this.db.pragma(`user_version = 19`);
    }
    if (current < 20) {
      applyV20Migration(this.db);
      this.db.pragma(`user_version = 20`);
    }
    if (current < 21) {
      applyV21Migration(this.db);
      this.db.pragma(`user_version = 21`);
    }
    if (current < 22) {
      applyV22Migration(this.db);
      this.db.pragma(`user_version = 22`);
    }
    if (current < 23) {
      applyV23Migration(this.db);
      this.db.pragma(`user_version = 23`);
    }
    if (current < 24) {
      applyV24Migration(this.db);
      this.db.pragma(`user_version = 24`);
    }
    if (current < 25) {
      applyV25Migration(this.db);
      this.db.pragma(`user_version = 25`);
    }
    if (current < 26) {
      applyV26WebAttemptMetadataMigration(this.db);
      this.db.pragma(`user_version = 26`);
    }
    if (current < 27) {
      applyV27HostRootMigration(this.db);
      this.db.pragma(`user_version = 27`);
    }
    if (current < 28) {
      applyV28Element17Migration(this.db);
      this.db.pragma(`user_version = 28`);
    }
    if (current < 29) {
      applyV29SkillUsageMigration(this.db);
      this.db.pragma(`user_version = 29`);
    }
    if (current < 30) {
      applyV30UnifiedMemoryColumnsMigration(this.db);
      this.db.pragma(`user_version = 30`);
    }
    if (current < 32) {
      // v32: parliament_verdicts — add confidence_score, topic_class, expires_at,
      //      validator_reasoning, agent_citations; add supporting indexes
      const pvCols = this.db.prepare("PRAGMA table_info(parliament_verdicts)").all() as { name: string }[];
      const pvColNames = pvCols.map(c => c.name);
      if (!pvColNames.includes("confidence_score")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.6");
      }
      if (!pvColNames.includes("topic_class")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN topic_class TEXT NOT NULL DEFAULT 'tactical'");
      }
      if (!pvColNames.includes("expires_at")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN expires_at INTEGER");
      }
      if (!pvColNames.includes("validator_reasoning")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN validator_reasoning TEXT");
      }
      if (!pvColNames.includes("agent_citations")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN agent_citations TEXT");
      }
      this.db.exec(`
        CREATE INDEX IF NOT EXISTS idx_pv_confidence ON parliament_verdicts(confidence_score DESC);
        CREATE INDEX IF NOT EXISTS idx_pv_expires    ON parliament_verdicts(expires_at);
      `);
      this.db.pragma(`user_version = 32`);
    }
    if (current < 33) {
      // v33: idempotent safety net — databases that had user_version=32 set before
      //      the ALTER TABLE statements ran (e.g. from a stale dev build) are missing
      //      these columns. Re-check via PRAGMA and add any that are absent.
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
      const pvCols33 = this.db.prepare("PRAGMA table_info(parliament_verdicts)").all() as { name: string }[];
      const pvColNames33 = pvCols33.map(c => c.name);
      if (!pvColNames33.includes("confidence_score")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.6");
      }
      if (!pvColNames33.includes("topic_class")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN topic_class TEXT NOT NULL DEFAULT 'tactical'");
      }
      if (!pvColNames33.includes("expires_at")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN expires_at INTEGER");
      }
      if (!pvColNames33.includes("validator_reasoning")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN validator_reasoning TEXT");
      }
      if (!pvColNames33.includes("agent_citations")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN agent_citations TEXT");
      }
      this.db.exec(`
        CREATE INDEX IF NOT EXISTS idx_pv_confidence ON parliament_verdicts(confidence_score DESC);
        CREATE INDEX IF NOT EXISTS idx_pv_expires    ON parliament_verdicts(expires_at);
      `);
      this.db.pragma(`user_version = 33`);
    }
    if (current < 34) {
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS activity_gate (
          job_id         TEXT PRIMARY KEY,
          last_seen_hash TEXT
        )
      `);
      this.db.pragma(`user_version = 34`);
    }
    // Update log if schema was upgraded
    if (current < SCHEMA_VERSION) {
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

  // ── UserPersonas ────────────────────────────────────────────────

  getUserPersonaRaw(userId: string): { personaJson: string; expiresAt: number } | null {
    const row = this.db
      .prepare("SELECT persona_json, expires_at FROM user_personas WHERE user_id = ?")
      .get(userId) as { persona_json: string; expires_at: number } | undefined;
    return row ? { personaJson: row.persona_json, expiresAt: row.expires_at } : null;
  }

  setUserPersona(userId: string, personaJson: string, ttlMs: number): void {
    const expiresAt = Date.now() + ttlMs;
    this.db.prepare(`
      INSERT INTO user_personas (user_id, persona_json, synthesized_at, expires_at)
      VALUES (?, ?, datetime('now'), ?)
      ON CONFLICT(user_id) DO UPDATE SET
        persona_json   = excluded.persona_json,
        synthesized_at = excluded.synthesized_at,
        expires_at     = excluded.expires_at
    `).run(userId, personaJson, expiresAt);
  }

  // ── Pellet generation run timestamps ───────────────────────────

  // better-sqlite3 is synchronous; async declared for caller API consistency
  async getPelletGenRun(key: string): Promise<Date | null> {
    const row = this.db.prepare(
      "SELECT last_run_at FROM pellet_generation_runs WHERE key = ?"
    ).get(key) as { last_run_at: string } | undefined;
    return row ? new Date(row.last_run_at) : null;
  }

  async setPelletGenRun(key: string, date: Date): Promise<void> {
    this.db.prepare(
      "INSERT INTO pellet_generation_runs (key, last_run_at) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET last_run_at = excluded.last_run_at"
    ).run(key, date.toISOString());
  }

  // ── Proactive engagement ────────────────────────────────────────

  getEngagementStats(
    jobType: string,
    opts: { days: number; minSamples: number },
  ): { replyRate: number; sampleCount: number } | null {
    const cutoff = new Date(
      Date.now() - opts.days * 24 * 60 * 60 * 1000,
    ).toISOString();
    const row = this.db
      .prepare(
        `SELECT
           COUNT(*) AS total,
           SUM(replied) AS replies
         FROM proactive_engagement
         WHERE job_type = ? AND created_at >= ?`,
      )
      .get(jobType, cutoff) as { total: number; replies: number | null } | undefined;

    if (!row || row.total < opts.minSamples) return null;
    return {
      replyRate: row.total > 0 ? (row.replies ?? 0) / row.total : 0,
      sampleCount: row.total,
    };
  }

  writeProactiveDelivery(params: {
    id: string;
    jobId: string;
    channel: string;
    userId: string;
    messagePreview?: string;
    verdict: string;
    deliveredAt?: string;
    status: string;
  }): void {
    // ON CONFLICT update — re-deliveries refresh status/verdict/delivery_at
    // but preserve user_replied_at and created_at, which are only ever
    // written by the original delivery / a later UPDATE when the user
    // engages. Using INSERT OR REPLACE here would silently destroy them.
    this.db
      .prepare(
        `INSERT INTO proactive_deliveries
         (id, job_id, channel, user_id, message_preview, verdict, delivered_at, status, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET
           job_id = excluded.job_id,
           channel = excluded.channel,
           user_id = excluded.user_id,
           message_preview = excluded.message_preview,
           verdict = excluded.verdict,
           delivered_at = excluded.delivered_at,
           status = excluded.status`,
      )
      .run(
        params.id,
        params.jobId,
        params.channel,
        params.userId,
        params.messagePreview?.slice(0, 100) ?? null,
        params.verdict,
        params.deliveredAt ?? null,
        params.status,
        new Date().toISOString(),
      );
  }

  writeProactiveEngagement(params: {
    id: string;
    deliveryId: string;
    jobType: string;
    goalId?: string;
    replied: boolean;
    replyLatencySeconds?: number;
  }): void {
    this.db
      .prepare(
        `INSERT OR IGNORE INTO proactive_engagement
         (id, delivery_id, job_type, goal_id, replied, reply_latency_seconds, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        params.id,
        params.deliveryId,
        params.jobType,
        params.goalId ?? null,
        params.replied ? 1 : 0,
        params.replyLatencySeconds ?? null,
        new Date().toISOString(),
      );
  }

  // ── Tool Cortex (v23) ──────────────────────────────────────────

  /**
   * Append a single tool execution row. Used by the Tool Cortex telemetry
   * pipeline to record per-call success, latency, and optional error/context
   * metadata for later aggregation.
   */
  recordToolExecution(args: {
    toolName: string;
    success: boolean;
    durationMs: number;
    errorCode?: string;
    errorMessage?: string;
    subgoalId?: string;
    sessionId?: string;
    attemptMetadata?: string;
  }): void {
    this.db
      .prepare(
        `INSERT INTO tool_executions
           (tool_name, success, duration_ms, error_code, error_message, subgoal_id, session_id, attempt_metadata)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        args.toolName,
        args.success ? 1 : 0,
        args.durationMs,
        args.errorCode ?? null,
        args.errorMessage ?? null,
        args.subgoalId ?? null,
        args.sessionId ?? null,
        args.attemptMetadata ?? null,
      );
  }

  /**
   * Aggregate tool execution stats over the last `opts.days` days
   * (default 30). Returns null when no executions match.
   */
  getToolStats(
    toolName: string,
    opts: { days?: number } = {},
  ): {
    selectionCount: number;
    successCount: number;
    failureCount: number;
    avgDurationMs: number;
    lastUsedAt: string | null;
  } | null {
    const days = opts.days ?? 30;
    const row = this.db
      .prepare(
        `SELECT COUNT(*) AS selection_count,
                SUM(success) AS success_count,
                SUM(1 - success) AS failure_count,
                AVG(duration_ms) AS avg_duration_ms,
                MAX(created_at) AS last_used_at
           FROM tool_executions
           WHERE tool_name = ?
             AND created_at > datetime('now', '-' || ? || ' days')`,
      )
      .get(toolName, days) as {
      selection_count: number;
      success_count: number | null;
      failure_count: number | null;
      avg_duration_ms: number | null;
      last_used_at: string | null;
    };
    if (!row || row.selection_count === 0) return null;
    return {
      selectionCount: row.selection_count,
      successCount: Number(row.success_count ?? 0),
      failureCount: Number(row.failure_count ?? 0),
      avgDurationMs: Math.round(row.avg_duration_ms ?? 0),
      lastUsedAt: row.last_used_at,
    };
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

  getOldestN(sessionId: string, n: number): Array<{ id: string; seq: number }> {
    const rows = this.db
      .prepare("SELECT id, seq FROM messages WHERE session_id = ? ORDER BY seq ASC LIMIT ?")
      .all(sessionId, n) as Array<{ id: string; seq: number }>;
    return rows;
  }

  deleteByIds(ids: string[]): void {
    if (ids.length === 0) return;
    const placeholders = ids.map(() => "?").join(",");
    this.db.prepare(`DELETE FROM messages WHERE id IN (${placeholders})`).run(ids);
  }

  deleteSession(sessionId: string): void {
    this.db.prepare("DELETE FROM messages WHERE session_id = ?").run(sessionId);
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
          AND f.invalidated_at IS NULL
          AND (f.expires_at IS NULL OR f.expires_at > ?)
        ORDER BY facts_fts.rank
        LIMIT ?
      `).all(query, userId ?? null, userId ?? null, now, limit) as any[];

      if (ftsRows.length > 0) {
        this.incrementAccess(ftsRows.map((r: any) => r.id));
        return ftsRows.map(rowToFact);
      }
    } catch (err) {
      // FTS5 may fail on special chars — fall through to LIKE
      log.memory.warn("facts FTS5 query failed, falling back to LIKE", err);
    }

    // LIKE fallback
    const likeRows = this.db.prepare(`
      SELECT * FROM facts
      WHERE fact LIKE ?
        AND (? IS NULL OR user_id = ?)
        AND confidence > 0
        AND invalidated_at IS NULL
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
        AND invalidated_at IS NULL
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
        AND invalidated_at IS NULL
        AND (expires_at IS NULL OR expires_at > ?)
      ORDER BY confidence DESC
    `).all(userId, category, now) as any[];
    return rows.map(rowToFact);
  }

  /** All non-retired facts (used by FactStore.load to populate in-memory map from DB) */
  getAllForUser(userId?: string): Fact[] {
    const rows = userId
      ? (this.db.prepare("SELECT * FROM facts WHERE user_id = ? AND confidence > 0 AND invalidated_at IS NULL").all(userId) as any[])
      : (this.db.prepare("SELECT * FROM facts WHERE confidence > 0 AND invalidated_at IS NULL").all() as any[]);
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

  /**
   * Promote frequently-accessed facts toward Tier-0 by bumping their confidence.
   * Facts in tier-0 categories with confidence in [minConfidence, 0.85) and
   * access_count >= minAccess get their confidence raised by `boost` (capped at 0.95).
   * Returns count of facts promoted.
   *
   * Intended for nightly cron — implements OpenClaw-style "dreaming" where
   * repeatedly-accessed facts graduate to always-injected status.
   */
  promoteFrequentlyAccessed(opts: {
    minAccess?: number;
    minConfidence?: number;
    boost?: number;
  } = {}): number {
    const minAccess = opts.minAccess ?? 3;
    const minConfidence = opts.minConfidence ?? 0.7;
    const boost = opts.boost ?? 0.1;
    if (TIER0_CATEGORIES.length === 0) return 0;
    const placeholders = TIER0_CATEGORIES.map(() => "?").join(",");
    const result = this.db.prepare(`
      UPDATE facts
      SET confidence = MIN(0.95, confidence + ?),
          updated_at = datetime('now')
      WHERE access_count >= ?
        AND confidence >= ?
        AND confidence < 0.85
        AND category IN (${placeholders})
        AND invalidated_at IS NULL
    `).run(boost, minAccess, minConfidence, ...TIER0_CATEGORIES);
    return result.changes;
  }

  getHighConfidenceFacts(userId?: string, limit = 30): Fact[] {
    if (TIER0_CATEGORIES.length === 0) return [];
    const placeholders = TIER0_CATEGORIES.map(() => "?").join(",");
    const now = new Date().toISOString();
    const rows = this.db.prepare(`
      SELECT * FROM facts
      WHERE confidence >= 0.8
        AND category IN (${placeholders})
        AND (invalidated_at IS NULL)
        AND (expires_at IS NULL OR expires_at > ?)
        ${userId ? "AND user_id = ?" : ""}
      ORDER BY confidence DESC, updated_at DESC
      LIMIT ?
    `).all(
      ...TIER0_CATEGORIES,
      now,
      ...(userId ? [userId] : []),
      limit,
    ) as any[];
    // Passive injection — do not increment access_count; these reads are automated, not user-driven.
    return rows.map(rowToFact);
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

// ─── Jaccard similarity helpers (ported from mistake-detector.ts) ─

function computeSimilarity(setA: string[], setB: string[]): number {
  const a = new Set(setA.map((w) => w.toLowerCase()));
  const b = new Set(setB.map((w) => w.toLowerCase()));
  const intersection = [...a].filter((w) => b.has(w)).length;
  const union = new Set([...a, ...b]).size;
  return union === 0 ? 0 : intersection / union;
}

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/\W+/)
    .filter((w) => w.length > 2);
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
    } catch (err) {
      log.memory.warn("owl_learnings FTS5 query failed, falling back to LIKE", err);
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

  admitIfWorthy(
    owlName: string,
    learning: string,
    category: LearningCategory,
    confidence: number,
  ): { id: string } | null {
    const recent = this.db.prepare(`
      SELECT learning FROM owl_learnings
      WHERE owl_name = ? AND created_at > datetime('now', '-30 days')
    `).all(owlName) as Array<{ learning: string }>;

    const newTokens = tokenize(learning);
    for (const row of recent) {
      const existingTokens = tokenize(row.learning);
      if (computeSimilarity(newTokens, existingTokens) >= 0.6) {
        return null;
      }
    }

    const result = this.add(owlName, learning, category, undefined, confidence);
    return { id: result.id };
  }

  evictStale(): number {
    const result = this.db.prepare(`
      DELETE FROM owl_learnings
      WHERE confidence < 0.3
        AND reinforcement_count <= 1
        AND created_at < datetime('now', '-14 days')
    `).run();
    return result.changes;
  }

  getForOwlSorted(owlName: string): string[] {
    const rows = this.db.prepare(`
      SELECT learning FROM owl_learnings
      WHERE owl_name = ?
      ORDER BY
        CASE category WHEN 'failure' THEN 0 ELSE 1 END,
        confidence DESC,
        reinforcement_count DESC
      LIMIT 6
    `).all(owlName) as Array<{ learning: string }>;
    return rows.map((r) => r.learning);
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
  private readonly cooldown = new Map<string, number>();

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

  getEffectivenessScore(owlName: string, toolName: string): number {
    const row = this.db.prepare(`
      SELECT
        COUNT(*) FILTER (WHERE outcome = 'success') AS success_count,
        COUNT(*) FILTER (WHERE outcome = 'failure') AS failure_count,
        MAX(created_at) FILTER (WHERE outcome = 'success') AS last_success
      FROM approach_library
      WHERE owl_name = ? AND tool_name = ?
    `).get(owlName, toolName) as {
      success_count: number;
      failure_count: number;
      last_success: string | null;
    } | undefined;

    if (!row || row.success_count + row.failure_count === 0) return 0.5;

    const baseScore = row.success_count / (row.success_count + row.failure_count);
    const ageMs = Date.now() - new Date(row.last_success ?? 0).getTime();
    const decayFactor = Math.pow(0.5, ageMs / (14 * 24 * 60 * 60 * 1000));
    return baseScore * decayFactor + (1 - decayFactor) * 0.5;
  }

  getRepeatFailureWarning(toolName: string, taskKeywords: string[]): string | null {
    const last = this.cooldown.get(toolName);
    if (last !== undefined && Date.now() - last < 3_600_000) return null;

    const rows = this.db.prepare(`
      SELECT task_keywords, failure_reason
      FROM approach_library
      WHERE tool_name = ? AND outcome = 'failure'
      ORDER BY created_at DESC
      LIMIT 20
    `).all(toolName) as Array<{ task_keywords: string; failure_reason: string | null }>;

    for (const row of rows) {
      const similarity = computeSimilarity(
        taskKeywords,
        tokenize(row.task_keywords),
      );
      if (similarity >= 0.6) {
        this.cooldown.set(toolName, Date.now());
        return (
          `Warning: similar task failed previously with ${toolName}. ` +
          `Past failure: ${row.failure_reason ?? "unknown reason"}. ` +
          `Consider an alternative approach.`
        );
      }
    }
    return null;
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
    options?: {
      confidenceScore?: number;
      topicClass?: string;
      expiresAt?: number;
      agentCitations?: string;
    },
  ): string {
    const id = uuidv4();
    const confidenceScore = options?.confidenceScore ?? 0.6;
    const topicClass = options?.topicClass ?? "tactical";
    const expiresAt = options?.expiresAt ?? null;
    const agentCitations = options?.agentCitations ?? null;
    this.db.prepare(`
      INSERT INTO parliament_verdicts
        (id, session_id, topic, verdict, participants, synthesis,
         confidence_score, topic_class, expires_at, agent_citations)
      VALUES (?,?,?,?,?,?,?,?,?,?)
    `).run(
      id, sessionId,
      topic.slice(0, 400),
      verdict,
      JSON.stringify(participants),
      synthesis ? synthesis.slice(0, 1000) : null,
      confidenceScore,
      topicClass,
      expiresAt,
      agentCitations,
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

  /** Update confidence score and validator reasoning after adversarial validation. */
  updateConfidence(id: string, confidenceScore: number, validatorReasoning?: string): void {
    this.db.prepare(`
      UPDATE parliament_verdicts
      SET confidence_score = ?, validator_reasoning = ?
      WHERE id = ?
    `).run(Math.min(0.95, Math.max(0.0, confidenceScore)), validatorReasoning ?? null, id);
  }

  /**
   * Find past verdicts on topics similar to the given query.
   * Simple keyword overlap — no embeddings needed for MVP.
   * Excludes expired verdicts; orders by confidence_score DESC.
   */
  findRelated(topic: string, limit = 2): ParliamentVerdictRecord[] {
    const nowSec = Math.floor(Date.now() / 1000);
    // Tokenize topic into meaningful words
    const words = topic
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((w) => w.length >= 4)
      .slice(0, 10);

    const expireFilter = `(expires_at IS NULL OR expires_at > ?)`;

    if (words.length === 0) {
      const rows = this.db.prepare(
        `SELECT * FROM parliament_verdicts WHERE ${expireFilter}
         ORDER BY confidence_score DESC, created_at DESC LIMIT ?`
      ).all(nowSec, limit) as any[];
      return rows.map(rowToParliamentVerdict);
    }

    // SQLite LIKE query per keyword — combine with OR
    const conditions = words.map(() => `topic LIKE ?`).join(" OR ");
    const params = words.map((w) => `%${w}%`);
    const rows = this.db.prepare(
      `SELECT * FROM parliament_verdicts
       WHERE (${conditions}) AND ${expireFilter}
       ORDER BY confidence_score DESC, created_at DESC LIMIT ?`
    ).all(...params, nowSec, limit) as any[];
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
    verificationResult?: string,
    verifierReason?: string,
  ): void {
    this.db.prepare(`
      INSERT INTO trajectory_turns
        (id, trajectory_id, turn_index, tool_name, args_snapshot, result_snapshot, success, duration_ms, verification_result, verifier_reason)
      VALUES (?,?,?,?,?,?,?,?,?,?)
    `).run(
      uuidv4(), trajectoryId, turnIndex, toolName,
      argsSnapshot.slice(0, 300),
      resultSnapshot.slice(0, 400),
      success ? 1 : 0,
      durationMs ?? null,
      verificationResult ?? null,
      verifierReason ?? null,
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

  markClarificationAsked(trajectoryId: string): void {
    this.db.prepare(
      `UPDATE trajectories SET clarification_asked = 1 WHERE id = ?`
    ).run(trajectoryId);
  }

  getRecentWithClarification(owlName: string, limit = 50): Array<Trajectory & { clarification_asked: number }> {
    const rows = this.db.prepare(`
      SELECT * FROM trajectories
      WHERE owl_name = ? AND completed_at IS NOT NULL
      ORDER BY created_at DESC LIMIT ?
    `).all(owlName, limit) as any[];
    return rows.map(r => ({ ...rowToTrajectory(r), clarification_asked: r.clarification_asked ?? 0 }));
  }

  /** All turns for a given trajectory — for detailed APO critique */
  getTurns(trajectoryId: string): TrajectoryTurn[] {
    const rows = this.db.prepare(`
      SELECT * FROM trajectory_turns WHERE trajectory_id = ?
      ORDER BY turn_index ASC
    `).all(trajectoryId) as any[];
    return rows.map(rowToTrajectoryTurn);
  }

  getFailureDensityTopics(daysBack: number, minOccurrences: number): string[] {
    try {
      const rows = this.db.prepare(`
        SELECT tool_name
        FROM trajectory_turns
        WHERE verification_result IN ('BLOCKED', 'PARTIAL')
          AND created_at > datetime('now', '-' || ? || ' days')
          AND tool_name IS NOT NULL
        GROUP BY tool_name
        HAVING COUNT(*) >= ?
        ORDER BY COUNT(*) DESC
        LIMIT 10
      `).all(daysBack, minOccurrences) as Array<{ tool_name: string }>;
      return rows.map((r) => r.tool_name);
    } catch (err) {
      log.memory.warn("getFailureDensityTopics db query failed", err);
      return [];
    }
  }

  getSessionFailures(sessionId: string): Array<{
    tool_name: string | null;
    verification_result: string;
    verifier_reason: string | null;
  }> {
    const rows = this.db.prepare(`
      SELECT tt.tool_name, tt.verification_result, tt.verifier_reason
      FROM trajectory_turns tt
      JOIN trajectories t ON t.id = tt.trajectory_id
      WHERE t.session_id = ?
        AND tt.verification_result IN ('BLOCKED', 'PARTIAL')
    `).all(sessionId) as any[];
    return rows;
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
    confidenceScore: r.confidence_score ?? 0.6,
    topicClass: r.topic_class ?? "tactical",
    expiresAt: r.expires_at ?? undefined,
    validatorReasoning: r.validator_reasoning ?? undefined,
    agentCitations: r.agent_citations ?? undefined,
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

// ─── AgentGoalsRepo ───────────────────────────────────────────────

class AgentGoalsRepo {
  constructor(private db: Database.Database) {}

  create(
    title: string,
    description: string,
    opts: {
      userId?: string;
      priority?: number;
      createdBy?: AgentGoal["createdBy"];
      deadline?: string;
      parentId?: string;
      sourceSessionId?: string;
    } = {},
  ): AgentGoal {
    const id = uuidv4();
    const now = new Date().toISOString();
    this.db
      .prepare(`INSERT INTO agent_goals
        (id, title, description, status, priority, created_by, user_id, deadline, progress, parent_id, source_session_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?)`)
      .run(
        id, title, description, "pending",
        opts.priority ?? 5,
        opts.createdBy ?? "user",
        opts.userId ?? "default",
        opts.deadline ?? null,
        opts.parentId ?? null,
        opts.sourceSessionId ?? null,
        now, now,
      );
    return this.get(id)!;
  }

  get(id: string): AgentGoal | undefined {
    const r = this.db.prepare(`SELECT * FROM agent_goals WHERE id = ?`).get(id) as Record<string, unknown> | undefined;
    return r ? rowToGoal(r) : undefined;
  }

  getActive(userId = "default"): AgentGoal[] {
    return (this.db.prepare(`SELECT * FROM agent_goals WHERE user_id = ? AND status IN ('pending','active') ORDER BY priority DESC, created_at ASC`).all(userId) as Record<string, unknown>[]).map(rowToGoal);
  }

  getAll(userId = "default"): AgentGoal[] {
    return (this.db.prepare(`SELECT * FROM agent_goals WHERE user_id = ? ORDER BY created_at DESC`).all(userId) as Record<string, unknown>[]).map(rowToGoal);
  }

  updateStatus(id: string, status: GoalStatus, progress?: number): void {
    const now = new Date().toISOString();
    if (progress !== undefined) {
      this.db.prepare(`UPDATE agent_goals SET status = ?, progress = ?, updated_at = ? WHERE id = ?`).run(status, progress, now, id);
    } else {
      this.db.prepare(`UPDATE agent_goals SET status = ?, updated_at = ? WHERE id = ?`).run(status, now, id);
    }
  }

  updateProgress(id: string, progress: number): void {
    this.db.prepare(`UPDATE agent_goals SET progress = ?, updated_at = ? WHERE id = ?`).run(progress, new Date().toISOString(), id);
  }
}

// ─── AgentTasksRepo ───────────────────────────────────────────────

class AgentTasksRepo {
  constructor(private db: Database.Database) {}

  create(
    goalId: string,
    description: string,
    opts: {
      type?: TaskType;
      riskLevel?: RiskLevel;
      requiresApproval?: boolean;
    } = {},
  ): AgentTask {
    const id = uuidv4();
    const now = new Date().toISOString();
    this.db
      .prepare(`INSERT INTO agent_tasks
        (id, goal_id, description, type, status, attempts, risk_level, requires_approval, created_at, updated_at)
        VALUES (?,?,?,?,?,0,?,?,?,?)`)
      .run(
        id, goalId, description,
        opts.type ?? "research",
        "pending",
        opts.riskLevel ?? "low",
        opts.requiresApproval ? 1 : 0,
        now, now,
      );
    return this.get(id)!;
  }

  get(id: string): AgentTask | undefined {
    const r = this.db.prepare(`SELECT * FROM agent_tasks WHERE id = ?`).get(id) as Record<string, unknown> | undefined;
    return r ? rowToTask(r) : undefined;
  }

  /** Next pending low/medium-risk task, ordered by goal priority then creation time */
  nextPending(): AgentTask | undefined {
    const r = this.db.prepare(`
      SELECT t.* FROM agent_tasks t
      JOIN agent_goals g ON t.goal_id = g.id
      WHERE t.status = 'pending'
        AND t.risk_level IN ('low','medium')
        AND t.requires_approval = 0
        AND g.status IN ('pending','active')
      ORDER BY g.priority DESC, t.created_at ASC
      LIMIT 1
    `).get() as Record<string, unknown> | undefined;
    return r ? rowToTask(r) : undefined;
  }

  /** Tasks awaiting user approval */
  pendingApproval(): AgentTask[] {
    return (this.db.prepare(`SELECT * FROM agent_tasks WHERE status = 'pending' AND requires_approval = 1`).all() as Record<string, unknown>[]).map(rowToTask);
  }

  forGoal(goalId: string): AgentTask[] {
    return (this.db.prepare(`SELECT * FROM agent_tasks WHERE goal_id = ? ORDER BY created_at ASC`).all(goalId) as Record<string, unknown>[]).map(rowToTask);
  }

  markRunning(id: string): void {
    this.db.prepare(`UPDATE agent_tasks SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?`).run(new Date().toISOString(), id);
  }

  markComplete(id: string, result: string): void {
    const now = new Date().toISOString();
    this.db.prepare(`UPDATE agent_tasks SET status = 'complete', result = ?, updated_at = ?, completed_at = ? WHERE id = ?`).run(result, now, now, id);
  }

  markFailed(id: string, reason: string): void {
    this.db.prepare(`UPDATE agent_tasks SET status = 'failed', result = ?, updated_at = ? WHERE id = ?`).run(reason, new Date().toISOString(), id);
  }

  approve(id: string): void {
    this.db.prepare(`UPDATE agent_tasks SET requires_approval = 0, updated_at = ? WHERE id = ?`).run(new Date().toISOString(), id);
  }
}

function rowToGoal(r: Record<string, unknown>): AgentGoal {
  return {
    id: r["id"] as string,
    title: r["title"] as string,
    description: r["description"] as string,
    status: r["status"] as GoalStatus,
    priority: r["priority"] as number,
    createdBy: r["created_by"] as AgentGoal["createdBy"],
    userId: r["user_id"] as string,
    deadline: (r["deadline"] as string) || undefined,
    progress: r["progress"] as number,
    parentId: (r["parent_id"] as string) || undefined,
    sourceSessionId: (r["source_session_id"] as string) || undefined,
    createdAt: r["created_at"] as string,
    updatedAt: r["updated_at"] as string,
  };
}

function rowToTask(r: Record<string, unknown>): AgentTask {
  return {
    id: r["id"] as string,
    goalId: r["goal_id"] as string,
    description: r["description"] as string,
    type: r["type"] as TaskType,
    status: r["status"] as TaskStatus,
    result: (r["result"] as string) || undefined,
    attempts: r["attempts"] as number,
    riskLevel: r["risk_level"] as RiskLevel,
    requiresApproval: (r["requires_approval"] as number) === 1,
    createdAt: r["created_at"] as string,
    updatedAt: r["updated_at"] as string,
    completedAt: (r["completed_at"] as string) || undefined,
  };
}

class UserProfilesRepo {
  constructor(private db: Database.Database) {}

  getPin(userId: string): string | null {
    const row = this.db.prepare(
      "SELECT active_pin FROM user_profiles WHERE user_id = ?"
    ).get(userId) as any;
    return row?.active_pin ?? null;
  }

  setPin(userId: string, owlName: string | null): void {
    if (owlName === null) {
      this.db.prepare(`INSERT INTO user_profiles (user_id, active_pin, pinned_at, updated_at)
        VALUES (?, NULL, NULL, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
          active_pin = NULL, pinned_at = NULL, updated_at = datetime('now')`).run(userId);
    } else {
      this.db.prepare(`INSERT INTO user_profiles (user_id, active_pin, pinned_at, updated_at)
        VALUES (?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
          active_pin = excluded.active_pin, pinned_at = excluded.pinned_at, updated_at = excluded.updated_at`).run(userId, owlName);
    }
  }

  appendRoutingHistory(userId: string, entry: RoutingHistoryEntry): void {
    const row = this.db.prepare(
      "SELECT routing_json FROM user_profiles WHERE user_id = ?"
    ).get(userId) as any;
    const history: RoutingHistoryEntry[] = row?.routing_json
      ? JSON.parse(row.routing_json) : [];
    history.push(entry);
    if (history.length > 10) history.splice(0, history.length - 10);
    this.db.prepare(`
      INSERT INTO user_profiles (user_id, routing_json, updated_at)
      VALUES (?, ?, datetime('now'))
      ON CONFLICT(user_id) DO UPDATE SET
        routing_json = excluded.routing_json,
        updated_at = excluded.updated_at
    `).run(userId, JSON.stringify(history));
  }

  getRoutingHistory(userId: string): RoutingHistoryEntry[] {
    const row = this.db.prepare(
      "SELECT routing_json FROM user_profiles WHERE user_id = ?"
    ).get(userId) as any;
    return row?.routing_json ? JSON.parse(row.routing_json) : [];
  }
}

class TasksRepo {
  constructor(private db: Database.Database) {}

  create(task: Omit<OwlTask, "createdAt" | "updatedAt">): void {
    this.db.prepare(`
      INSERT INTO owl_tasks
        (id, user_id, owl_name, title, description, status, priority, session_id, due_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      task.id, task.userId, task.owlName, task.title,
      task.description ?? null, task.status, task.priority,
      task.sessionId ?? null, task.dueAt ?? null,
    );
  }

  updateStatus(taskId: string, status: OwlTaskStatus, result?: string): void {
    this.db.prepare(`
      UPDATE owl_tasks
      SET status = ?, result = COALESCE(?, result), updated_at = datetime('now')
      WHERE id = ?
    `).run(status, result ?? null, taskId);
  }

  getActive(userId: string): OwlTask[] {
    return (this.db.prepare(`
      SELECT * FROM owl_tasks
      WHERE user_id = ? AND status IN ('pending','active','blocked')
      ORDER BY updated_at ASC LIMIT 5
    `).all(userId) as any[]).map(rowToOwlTask);
  }

  get(taskId: string): OwlTask | null {
    const row = this.db.prepare(
      "SELECT * FROM owl_tasks WHERE id = ?"
    ).get(taskId) as any;
    return row ? rowToOwlTask(row) : null;
  }
}

class JobsRepo {
  constructor(private db: Database.Database) {}

  enqueue(job: Omit<OwlJob, "status" | "startedAt" | "completedAt" | "error" | "result">): void {
    this.db.prepare(`
      INSERT INTO owl_jobs (id, task_id, user_id, owl_name, type, payload, status, scheduled_at)
      VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
    `).run(
      job.id, job.taskId ?? null, job.userId, job.owlName,
      job.type, JSON.stringify(job.payload), job.scheduledAt,
    );
  }

  dequeueNext(): OwlJob | null {
    const row = this.db.prepare(
      `SELECT * FROM owl_jobs WHERE status = 'queued' AND scheduled_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now') ORDER BY scheduled_at ASC LIMIT 1`
    ).get() as any;
    if (!row) return null;
    const startedAt = new Date().toISOString();
    this.db.prepare("UPDATE owl_jobs SET status = 'running', started_at = ? WHERE id = ?").run(startedAt, row.id);
    return rowToOwlJob({ ...row, status: "running", started_at: startedAt });
  }

  markDone(jobId: string, result: string): void {
    this.db.prepare(
      "UPDATE owl_jobs SET status = 'done', result = ?, completed_at = datetime('now') WHERE id = ?"
    ).run(result, jobId);
  }

  markFailed(jobId: string, error: string): void {
    this.db.prepare(
      "UPDATE owl_jobs SET status = 'failed', error = ?, completed_at = datetime('now') WHERE id = ?"
    ).run(error, jobId);
  }

  get(jobId: string): OwlJob | null {
    const row = this.db.prepare("SELECT * FROM owl_jobs WHERE id = ?").get(jobId) as any;
    return row ? rowToOwlJob(row) : null;
  }

  getQueued(userId: string): OwlJob[] {
    return (this.db.prepare(
      "SELECT * FROM owl_jobs WHERE user_id = ? AND status IN ('queued','running') ORDER BY scheduled_at ASC"
    ).all(userId) as any[]).map(rowToOwlJob);
  }
}

function rowToOwlTask(row: any): OwlTask {
  return {
    id: row.id,
    userId: row.user_id,
    owlName: row.owl_name,
    title: row.title,
    description: row.description ?? undefined,
    status: row.status as OwlTaskStatus,
    priority: row.priority as OwlTaskPriority,
    sessionId: row.session_id ?? undefined,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    dueAt: row.due_at ?? undefined,
    result: row.result ?? undefined,
  };
}

function rowToOwlJob(row: any): OwlJob {
  return {
    id: row.id,
    taskId: row.task_id ?? undefined,
    userId: row.user_id,
    owlName: row.owl_name,
    type: row.type as OwlJobType,
    payload: typeof row.payload === "string" ? JSON.parse(row.payload) : row.payload,
    status: row.status as OwlJobStatus,
    scheduledAt: row.scheduled_at,
    startedAt: row.started_at ?? undefined,
    completedAt: row.completed_at ?? undefined,
    error: row.error ?? undefined,
    result: row.result ?? undefined,
  };
}

// ─── StackOwlDB ───────────────────────────────────────────────────
// Thin wrapper around a raw SQLite file path. Used by tests and any
// caller that manages the db path directly (vs. a workspace directory).

export class StackOwlDB {
  readonly db: Database.Database;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);

    // Performance pragmas
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("synchronous = NORMAL");
    this.db.pragma("foreign_keys = ON");

    this.createSchema();
    this.runMigrations();
  }

  close(): void {
    this.db.close();
  }

  private createSchema(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS trajectory_turns (
        id                  TEXT PRIMARY KEY,
        trajectory_id       TEXT NOT NULL,
        turn_index          INTEGER NOT NULL,
        tool_name           TEXT NOT NULL,
        args_snapshot       TEXT NOT NULL DEFAULT '',
        result_snapshot     TEXT NOT NULL DEFAULT '',
        success             INTEGER NOT NULL DEFAULT 1,
        duration_ms         INTEGER,
        verification_result TEXT,
        verifier_reason     TEXT,
        subgoal_id          TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS workspace_tools (
        tool_name     TEXT PRIMARY KEY,
        state         TEXT NOT NULL DEFAULT 'SHADOW',
        source_code   TEXT NOT NULL,
        promoted_at   TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
      );
    `);
  }

  private runMigrations(): void {
    const current = (this.db.pragma("user_version") as { user_version: number }[])[0]?.user_version ?? 0;
    if (current < 16) {
      // v16: GAV verifier columns on trajectory_turns + workspace_tools table
      // Safe to skip: createSchema() already creates tables with all columns
      // for fresh databases. The ALTER TABLE statements only apply to existing
      // databases upgrading from an earlier schema version.
      try { this.db.exec(`ALTER TABLE trajectory_turns ADD COLUMN verification_result TEXT`); } catch (err) { log.memory.warn("db migration: ALTER TABLE trajectory_turns verification_result (may already exist)", err); }
      try { this.db.exec(`ALTER TABLE trajectory_turns ADD COLUMN verifier_reason TEXT`); } catch (err) { log.memory.warn("db migration: ALTER TABLE trajectory_turns verifier_reason (may already exist)", err); }
      try { this.db.exec(`ALTER TABLE trajectory_turns ADD COLUMN subgoal_id TEXT`); } catch (err) { log.memory.warn("db migration: ALTER TABLE trajectory_turns subgoal_id (may already exist)", err); }
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS workspace_tools (
          tool_name     TEXT PRIMARY KEY,
          state         TEXT NOT NULL DEFAULT 'SHADOW',
          source_code   TEXT NOT NULL,
          promoted_at   TEXT,
          created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
      `);
    }
    if (current < 17) {
      // v17: owl intelligence tables — task ledger, reflexion critiques,
      // skill templates, outcome journal; plus new columns on facts (if present).
      applyV17Migration(this.db);
      this.db.pragma(`user_version = 17`);
    }
    if (current < 18) {
      applyV18Migration(this.db);
      this.db.pragma(`user_version = 18`);
    }
    if (current < 19) {
      applyV19Migration(this.db);
      this.db.pragma(`user_version = 19`);
    }
    if (current < 20) {
      applyV20Migration(this.db);
      this.db.pragma(`user_version = 20`);
    }
    if (current < 21) {
      applyV21Migration(this.db);
      this.db.pragma(`user_version = 21`);
    }
    if (current < 22) {
      applyV22Migration(this.db);
      this.db.pragma(`user_version = 22`);
    }
    if (current < 23) {
      applyV23Migration(this.db);
      this.db.pragma(`user_version = 23`);
    }
    if (current < 24) {
      applyV24Migration(this.db);
      this.db.pragma(`user_version = 24`);
    }
    if (current < 25) {
      applyV25Migration(this.db);
      this.db.pragma(`user_version = 25`);
    }
    if (current < 26) {
      applyV26WebAttemptMetadataMigration(this.db);
      this.db.pragma(`user_version = 26`);
    }
    if (current < 27) {
      applyV27HostRootMigration(this.db);
      this.db.pragma(`user_version = 27`);
    }
    if (current < 28) {
      applyV28Element17Migration(this.db);
      this.db.pragma(`user_version = 28`);
    }
    if (current < 30) {
      applyV30UnifiedMemoryColumnsMigration(this.db);
      this.db.pragma(`user_version = 30`);
    }
    if (current < 31) {
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS sessions (
          id              TEXT PRIMARY KEY,
          parent_id       TEXT,
          status          TEXT NOT NULL CHECK(status IN ('pending', 'running', 'awaiting_input', 'completed', 'terminated', 'failed')),
          prompt          TEXT NOT NULL,
          history_json    TEXT,
          result          TEXT,
          error           TEXT,
          metadata        TEXT,
          created_at      TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
          terminated_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);

        CREATE TABLE IF NOT EXISTS session_messages (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id   TEXT NOT NULL,
          direction    TEXT NOT NULL CHECK(direction IN ('to_session', 'from_session')),
          content      TEXT NOT NULL,
          created_at   TEXT NOT NULL DEFAULT (datetime('now')),
          consumed_at  TEXT,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_session_messages_pending ON session_messages(session_id, consumed_at);
      `);
      this.db.pragma(`user_version = 31`);
    }
    if (current < 32) {
      // v32: parliament_verdicts — ensure table exists (StackOwlDB schema may not
      //      include it), then add confidence_score, topic_class, expires_at,
      //      validator_reasoning, agent_citations columns.
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
      const pvCols = this.db.prepare("PRAGMA table_info(parliament_verdicts)").all() as { name: string }[];
      const pvColNames = pvCols.map(c => c.name);
      if (!pvColNames.includes("confidence_score")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.6");
      }
      if (!pvColNames.includes("topic_class")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN topic_class TEXT NOT NULL DEFAULT 'tactical'");
      }
      if (!pvColNames.includes("expires_at")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN expires_at INTEGER");
      }
      if (!pvColNames.includes("validator_reasoning")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN validator_reasoning TEXT");
      }
      if (!pvColNames.includes("agent_citations")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN agent_citations TEXT");
      }
      this.db.exec(`
        CREATE INDEX IF NOT EXISTS idx_pv_confidence ON parliament_verdicts(confidence_score DESC);
        CREATE INDEX IF NOT EXISTS idx_pv_expires    ON parliament_verdicts(expires_at);
      `);
      this.db.pragma(`user_version = 32`);
    }
    if (current < 33) {
      // v33: idempotent safety net for parliament_verdicts columns
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
      const pvCols33 = this.db.prepare("PRAGMA table_info(parliament_verdicts)").all() as { name: string }[];
      const pvColNames33 = pvCols33.map(c => c.name);
      if (!pvColNames33.includes("confidence_score")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.6");
      }
      if (!pvColNames33.includes("topic_class")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN topic_class TEXT NOT NULL DEFAULT 'tactical'");
      }
      if (!pvColNames33.includes("expires_at")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN expires_at INTEGER");
      }
      if (!pvColNames33.includes("validator_reasoning")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN validator_reasoning TEXT");
      }
      if (!pvColNames33.includes("agent_citations")) {
        this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN agent_citations TEXT");
      }
      this.db.exec(`
        CREATE INDEX IF NOT EXISTS idx_pv_confidence ON parliament_verdicts(confidence_score DESC);
        CREATE INDEX IF NOT EXISTS idx_pv_expires    ON parliament_verdicts(expires_at);
      `);
      this.db.pragma(`user_version = 33`);
    }
  }
}

// ─── Standalone migration helper (used in tests & tooling) ───────

/**
 * Shared v17 DDL — called by MemoryDatabase.runMigrations(), StackOwlDB.runMigrations(),
 * and the standalone applyMigrations() export. Not exported.
 */
function applyV17Migration(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS owl_task_ledger (
      id            TEXT PRIMARY KEY,
      session_id    TEXT NOT NULL,
      user_id       TEXT NOT NULL,
      task_id       TEXT NOT NULL,
      subgoal_index INTEGER NOT NULL,
      subgoal_text  TEXT NOT NULL,
      state_json    TEXT NOT NULL,
      status        TEXT NOT NULL DEFAULT 'in_progress',
      attempt_count INTEGER NOT NULL DEFAULT 0,
      created_at    TEXT NOT NULL,
      resumed_at    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_task_ledger_user
      ON owl_task_ledger(user_id, status);

    CREATE TABLE IF NOT EXISTS reflexion_critiques (
      id               TEXT PRIMARY KEY,
      task_category    TEXT NOT NULL,
      complexity_tier  TEXT NOT NULL,
      tool_sequence    TEXT NOT NULL,
      critique_text    TEXT NOT NULL,
      embedding        BLOB NOT NULL,
      used_count       INTEGER NOT NULL DEFAULT 0,
      created_at       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_critiques_category
      ON reflexion_critiques(task_category, complexity_tier);

    CREATE TABLE IF NOT EXISTS skill_templates (
      id            TEXT PRIMARY KEY,
      name          TEXT UNIQUE NOT NULL,
      source        TEXT NOT NULL DEFAULT 'auto',
      template_text TEXT NOT NULL,
      trigger_desc  TEXT NOT NULL,
      embedding     BLOB NOT NULL,
      success_count INTEGER NOT NULL DEFAULT 0,
      installed_at  TEXT NOT NULL,
      last_used_at  TEXT
    );

    CREATE TABLE IF NOT EXISTS outcome_journal (
      id                  TEXT PRIMARY KEY,
      session_id          TEXT NOT NULL,
      owl_name            TEXT NOT NULL DEFAULT 'default',
      user_id             TEXT NOT NULL,
      outcome             TEXT NOT NULL,
      reward              REAL NOT NULL DEFAULT 0.0,
      quality_score       REAL NOT NULL DEFAULT 0.0,
      task_category       TEXT NOT NULL DEFAULT 'general',
      task_complexity     TEXT NOT NULL DEFAULT 'medium',
      challenge_instances INTEGER NOT NULL DEFAULT 0,
      created_at          TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_outcome_journal_user
      ON outcome_journal(user_id, created_at);
  `);

  // Only ALTER facts if the table exists (StackOwlDB does not have a facts table)
  const factsExists = (db.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
  ).get() as { name: string } | undefined) !== undefined;
  if (factsExists) {
    const factsColumns = (db.prepare("PRAGMA table_info(facts)").all() as { name: string }[]).map(c => c.name);
    if (!factsColumns.includes("invalidated_at")) {
      db.exec("ALTER TABLE facts ADD COLUMN invalidated_at TEXT;");
    }
  }
}

function applyV18Migration(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS post_processor_job_runs (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      job_name     TEXT    NOT NULL,
      tier         TEXT    NOT NULL,
      success      INTEGER NOT NULL,
      error_code   TEXT,
      duration_ms  INTEGER,
      user_id      TEXT,
      session_id   TEXT,
      ts           TEXT    NOT NULL  DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_ppjr_job_ts
      ON post_processor_job_runs(job_name, ts);
    CREATE INDEX IF NOT EXISTS idx_ppjr_success
      ON post_processor_job_runs(success, ts);
  `);
}

function applyV19Migration(db: Database.Database): void {
  // Guard: trajectories table exists in MemoryDatabase but not in StackOwlDB or minimal test DBs
  const trajExists = (db.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='trajectories'"
  ).get() as { name: string } | undefined) !== undefined;
  if (trajExists) {
    const cols = (db.prepare("PRAGMA table_info(trajectories)").all() as { name: string }[]).map(c => c.name);
    if (!cols.includes("clarification_asked")) {
      db.exec(`ALTER TABLE trajectories ADD COLUMN clarification_asked INTEGER NOT NULL DEFAULT 0;`);
    }
  }
}

function applyV20Migration(db: Database.Database): void {
  // Ensure trajectory_turns exists (may not exist in minimal test DBs or StackOwlDB)
  db.exec(`
    CREATE TABLE IF NOT EXISTS trajectory_turns (
      id                  TEXT PRIMARY KEY,
      trajectory_id       TEXT NOT NULL,
      turn_index          INTEGER NOT NULL,
      role                TEXT NOT NULL,
      content             TEXT NOT NULL,
      tool_name           TEXT,
      tool_input          TEXT,
      tool_output         TEXT,
      created_at          TEXT NOT NULL DEFAULT (datetime('now')),
      parliament_session_id TEXT
    );
  `);
  // Guard: only create the index if trajectory_id column exists (table may have pre-existed without it)
  const turnCols = (db.prepare("PRAGMA table_info(trajectory_turns)").all() as { name: string }[]).map(c => c.name);
  if (turnCols.includes("trajectory_id")) {
    db.exec(`CREATE INDEX IF NOT EXISTS idx_turn_traj ON trajectory_turns(trajectory_id);`);
  }
  // Guard: add parliament_session_id column if table already existed without it
  if (!turnCols.includes("parliament_session_id")) {
    db.exec(`ALTER TABLE trajectory_turns ADD COLUMN parliament_session_id TEXT;`);
  }
}

function applyV21Migration(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS pellet_generation_runs (
      key         TEXT PRIMARY KEY,
      last_run_at TEXT NOT NULL
    );
  `);
}

export function applyV22Migration(db: Database.Database): void {
  // Wrap the whole migration in a single transaction so concurrent connections
  // (heartbeat worker, telegram bot, main engine) cannot race the
  // table_info-then-ALTER check-and-act sequence and produce
  // "duplicate column name" errors on the loser. better-sqlite3's
  // db.transaction() upgrades to BEGIN IMMEDIATE on the first write,
  // serializing all writers; the inner steps remain idempotent so a
  // SQLITE_BUSY rollback just retries cleanly on the next call.
  const run = db.transaction(() => {
    // proactive_jobs may not exist yet on this DB if ProactiveJobQueue
    // has never been instantiated against it (it lives in proactive-jobs.db today).
    // Create the canonical schema first, then ALTER for v22 columns.
    db.exec(`
      CREATE TABLE IF NOT EXISTS proactive_jobs (
        id          TEXT PRIMARY KEY,
        type        TEXT NOT NULL,
        user_id     TEXT NOT NULL,
        scheduled_at TEXT NOT NULL,
        payload     TEXT NOT NULL DEFAULT '{}',
        status      TEXT NOT NULL DEFAULT 'pending',
        priority    INTEGER NOT NULL DEFAULT 5,
        attempts    INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TEXT,
        error       TEXT,
        created_at  TEXT NOT NULL
      );

      CREATE INDEX IF NOT EXISTS idx_pj_status_scheduled
        ON proactive_jobs (status, scheduled_at);

      CREATE INDEX IF NOT EXISTS idx_pj_user
        ON proactive_jobs (user_id, status);
    `);

    // Add v22 columns idempotently. Each ALTER guarded by table_info check.
    const jobCols = (db.pragma("table_info(proactive_jobs)") as { name: string }[]).map(c => c.name);
    if (!jobCols.includes("retry_count")) {
      db.exec(`ALTER TABLE proactive_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0`);
    }
    if (!jobCols.includes("suppress_count")) {
      db.exec(`ALTER TABLE proactive_jobs ADD COLUMN suppress_count INTEGER NOT NULL DEFAULT 0`);
    }
    if (!jobCols.includes("goal_id")) {
      db.exec(`ALTER TABLE proactive_jobs ADD COLUMN goal_id TEXT`);
    }
    if (!jobCols.includes("error")) {
      db.exec(`ALTER TABLE proactive_jobs ADD COLUMN error TEXT`);
    }

    // goal_id index — added after the ALTER guards because the column
    // does not exist on legacy v21 DBs until the ALTER above runs.
    db.exec(`CREATE INDEX IF NOT EXISTS idx_pj_goal ON proactive_jobs (goal_id, status)`);

    // Delivery outcomes table
    db.exec(`
      CREATE TABLE IF NOT EXISTS proactive_deliveries (
        id              TEXT PRIMARY KEY,
        job_id          TEXT NOT NULL,
        channel         TEXT NOT NULL,
        user_id         TEXT NOT NULL,
        message_preview TEXT,
        verdict         TEXT NOT NULL,
        delivered_at    TEXT,
        status          TEXT NOT NULL,
        user_replied_at TEXT,
        created_at      TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_pd_job ON proactive_deliveries(job_id);
      CREATE INDEX IF NOT EXISTS idx_pd_user ON proactive_deliveries(user_id, created_at);
    `);

    // Engagement signal table
    db.exec(`
      CREATE TABLE IF NOT EXISTS proactive_engagement (
        id                    TEXT PRIMARY KEY,
        delivery_id           TEXT NOT NULL,
        job_type              TEXT NOT NULL,
        goal_id               TEXT,
        replied               INTEGER NOT NULL DEFAULT 0 CHECK (replied IN (0, 1)),
        reply_latency_seconds INTEGER,
        created_at            TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_pe_job_type ON proactive_engagement(job_type, created_at);
      CREATE INDEX IF NOT EXISTS idx_pe_goal ON proactive_engagement(goal_id);
    `);
  });
  run();
}

export function applyV23Migration(db: Database.Database): void {
  // v23: Tool Cortex foundation — per-execution telemetry (tool_executions)
  // and capability-tagged tool transition graph (tool_edges) used by the
  // Tool Cortex selector/learning loop.
  db.exec(`
    CREATE TABLE IF NOT EXISTS tool_executions (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      tool_name     TEXT NOT NULL,
      success       INTEGER NOT NULL,
      duration_ms   INTEGER NOT NULL,
      error_code    TEXT,
      error_message TEXT,
      subgoal_id    TEXT,
      session_id    TEXT,
      created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_tool_exec_name_time
      ON tool_executions(tool_name, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tool_exec_subgoal
      ON tool_executions(subgoal_id) WHERE subgoal_id IS NOT NULL;

    CREATE TABLE IF NOT EXISTS tool_edges (
      from_tool       TEXT NOT NULL,
      to_tool         TEXT NOT NULL,
      capability_tag  TEXT NOT NULL,
      success_rate    REAL NOT NULL DEFAULT 0,
      avg_duration_ms INTEGER NOT NULL DEFAULT 0,
      sample_count    INTEGER NOT NULL DEFAULT 0,
      updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (from_tool, to_tool, capability_tag)
    );
    CREATE INDEX IF NOT EXISTS idx_tool_edges_capability
      ON tool_edges(capability_tag, from_tool);
  `);
}

export function applyV24Migration(db: Database.Database): void {
  // v24: Tool Cortex SET (Self-Evolving Tools) — shadow-execution run state.
  // Tracks baseline vs. candidate success/total counters across process
  // boundaries so a shadow run survives restarts and can be evaluated by
  // any process holding the same DB.
  db.exec(`
    CREATE TABLE IF NOT EXISTS tool_evolution_runs (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      baseline_tool       TEXT NOT NULL,
      candidate_tool      TEXT NOT NULL,
      baseline_path       TEXT NOT NULL,
      candidate_path      TEXT NOT NULL,
      baseline_successes  INTEGER NOT NULL DEFAULT 0,
      baseline_total      INTEGER NOT NULL DEFAULT 0,
      candidate_successes INTEGER NOT NULL DEFAULT 0,
      candidate_total     INTEGER NOT NULL DEFAULT 0,
      status              TEXT NOT NULL DEFAULT 'running',
      started_at          TEXT NOT NULL DEFAULT (datetime('now')),
      finished_at         TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_evol_runs_status
      ON tool_evolution_runs(status);
  `);
}

/**
 * Apply all MemoryDatabase migrations to the given SQLite connection.
 * Accepts an in-memory or on-disk Database instance; idempotent.
 */
export function applyMigrations(db: Database.Database): void {
  // Ensure the base facts table exists (createSchema equivalent for migration tests)
  db.exec(`
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
  `);

  const current = (db.pragma("user_version") as { user_version: number }[])[0]?.user_version ?? 0;

  if (current < 17) {
    applyV17Migration(db);
  }
  if (current < 18) {
    applyV18Migration(db);
  }
  if (current < 19) {
    applyV19Migration(db);
  }
  if (current < 20) {
    applyV20Migration(db);
  }
  if (current < 21) {
    applyV21Migration(db);
  }
  if (current < 22) {
    applyV22Migration(db);
  }
  if (current < 23) {
    applyV23Migration(db);
  }
  if (current < 24) {
    applyV24Migration(db);
  }
  if (current < 25) {
    applyV25Migration(db);
  }
  if (current < 26) {
    applyV26WebAttemptMetadataMigration(db);
  }
  if (current < 27) {
    applyV27HostRootMigration(db);
  }
  if (current < 28) {
    applyV28Element17Migration(db);
  }
  if (current < 30) {
    applyV30UnifiedMemoryColumnsMigration(db);
  }
  if (current < 31) {
    db.exec(`
      CREATE TABLE IF NOT EXISTS sessions (
        id              TEXT PRIMARY KEY,
        parent_id       TEXT,
        status          TEXT NOT NULL CHECK(status IN ('pending', 'running', 'awaiting_input', 'completed', 'terminated', 'failed')),
        prompt          TEXT NOT NULL,
        history_json    TEXT,
        result          TEXT,
        error           TEXT,
        metadata        TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        terminated_at   TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, updated_at);
      CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);

      CREATE TABLE IF NOT EXISTS session_messages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   TEXT NOT NULL,
        direction    TEXT NOT NULL CHECK(direction IN ('to_session', 'from_session')),
        content      TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        consumed_at  TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
      );
      CREATE INDEX IF NOT EXISTS idx_session_messages_pending ON session_messages(session_id, consumed_at);
    `);
  }
  if (current < 32) {
    // v32: parliament_verdicts — ensure table exists (may be fresh DB that never
    //      ran the class-based v7 migration), then add confidence_score,
    //      topic_class, expires_at, validator_reasoning, agent_citations columns.
    db.exec(`
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
    const pvCols = db.prepare("PRAGMA table_info(parliament_verdicts)").all() as { name: string }[];
    const pvColNames = pvCols.map(c => c.name);
    if (!pvColNames.includes("confidence_score")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.6");
    }
    if (!pvColNames.includes("topic_class")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN topic_class TEXT NOT NULL DEFAULT 'tactical'");
    }
    if (!pvColNames.includes("expires_at")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN expires_at INTEGER");
    }
    if (!pvColNames.includes("validator_reasoning")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN validator_reasoning TEXT");
    }
    if (!pvColNames.includes("agent_citations")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN agent_citations TEXT");
    }
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_pv_confidence ON parliament_verdicts(confidence_score DESC);
      CREATE INDEX IF NOT EXISTS idx_pv_expires    ON parliament_verdicts(expires_at);
    `);
  }
  if (current < 33) {
    // v33: idempotent safety net for parliament_verdicts columns
    db.exec(`
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
    const pvCols33 = db.prepare("PRAGMA table_info(parliament_verdicts)").all() as { name: string }[];
    const pvColNames33 = pvCols33.map(c => c.name);
    if (!pvColNames33.includes("confidence_score")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.6");
    }
    if (!pvColNames33.includes("topic_class")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN topic_class TEXT NOT NULL DEFAULT 'tactical'");
    }
    if (!pvColNames33.includes("expires_at")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN expires_at INTEGER");
    }
    if (!pvColNames33.includes("validator_reasoning")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN validator_reasoning TEXT");
    }
    if (!pvColNames33.includes("agent_citations")) {
      db.exec("ALTER TABLE parliament_verdicts ADD COLUMN agent_citations TEXT");
    }
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_pv_confidence ON parliament_verdicts(confidence_score DESC);
      CREATE INDEX IF NOT EXISTS idx_pv_expires    ON parliament_verdicts(expires_at);
    `);
  }
  db.pragma(`user_version = ${SCHEMA_VERSION}`);
}

/** Alias for applyMigrations — used by tests that import this name. */
export const applyAllMigrationsToRawDb = applyMigrations;

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

/**
 * Pre-flight backup before v25 — copies a file-backed SQLite DB to a
 * timestamped sidecar so the prior state is recoverable if the migration
 * goes wrong. Returns the backup path on success, or null when no copy
 * was made (in-memory db, or source file missing).
 */
export function backupBeforeV25(dbPath: string | null): string | null {
  if (!dbPath) return null;
  if (!existsSync(dbPath)) return null;
  const backupPath = `${dbPath}.v24-backup-${Date.now()}`;
  copyFileSync(dbPath, backupPath);
  return backupPath;
}

/**
 * Schema v25 migration — Element 15 memory architecture (minimal v1 schema).
 *
 * v1 ships the typed surface against this minimal schema. Task 4 expands
 * to legacy-data migration + full column set + bitemporal CHECK constraints.
 */
export function applyV25Migration(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS memories (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL CHECK (kind IN ('semantic','episodic','working','procedural','reflexive')),
      content TEXT NOT NULL,
      embedding BLOB,
      importance REAL NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
      goal_id TEXT,
      subgoal_id TEXT,
      verdict TEXT CHECK (verdict IS NULL OR verdict IN ('ADVANCES','PARTIAL','BLOCKED','NEUTRAL')),
      source_turn_id TEXT,
      source_channel TEXT,
      valid_at TEXT NOT NULL,
      invalid_at TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      access_count INTEGER NOT NULL DEFAULT 0,
      last_accessed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
    CREATE INDEX IF NOT EXISTS idx_memories_valid ON memories(invalid_at);
    CREATE INDEX IF NOT EXISTS idx_memories_goal ON memories(goal_id);
    CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);

    CREATE TABLE IF NOT EXISTS memory_invalidations (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      reason TEXT NOT NULL,
      invalidated_by TEXT NOT NULL,
      invalidated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inv_memory ON memory_invalidations(memory_id);

    CREATE TABLE IF NOT EXISTS memory_contradictions (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      contradicts_id TEXT NOT NULL REFERENCES memories(id),
      detected_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_contra_memory ON memory_contradictions(memory_id);

    CREATE TABLE IF NOT EXISTS memory_access_log (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      accessed_at TEXT NOT NULL,
      context TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_access_memory ON memory_access_log(memory_id);
  `);

  mergeLegacyIntoMemories(db);
}

function tableHasColumns(db: Database.Database, table: string, required: string[]): boolean {
  const row = db
    .prepare(`SELECT name FROM sqlite_master WHERE type='table' AND name=?`)
    .get(table) as { name: string } | undefined;
  if (!row) return false;
  const cols = (db.prepare(`PRAGMA table_info(${table})`).all() as { name: string }[]).map(
    (c) => c.name,
  );
  return required.every((c) => cols.includes(c));
}

function mergeLegacyIntoMemories(db: Database.Database): void {
  if (tableHasColumns(db, "facts", ["id", "fact", "confidence", "created_at", "invalidated_at"])) {
    db.exec(`
      INSERT OR IGNORE INTO memories
        (id, kind, content, importance, valid_at, invalid_at, created_at, updated_at)
      SELECT
        id,
        'semantic',
        fact,
        COALESCE(MIN(MAX(confidence, 0), 1), 0.5),
        COALESCE(created_at, '1970-01-01T00:00:00Z'),
        invalidated_at,
        COALESCE(created_at, '1970-01-01T00:00:00Z'),
        COALESCE(updated_at, created_at, '1970-01-01T00:00:00Z')
      FROM facts;
    `);
  }
  if (tableHasColumns(db, "episodes", ["id", "summary", "importance", "created_at"])) {
    db.exec(`
      INSERT OR IGNORE INTO memories
        (id, kind, content, importance, valid_at, created_at, updated_at)
      SELECT
        id,
        'episodic',
        summary,
        COALESCE(MIN(MAX(importance, 0), 1), 0.5),
        COALESCE(created_at, '1970-01-01T00:00:00Z'),
        COALESCE(created_at, '1970-01-01T00:00:00Z'),
        COALESCE(created_at, '1970-01-01T00:00:00Z')
      FROM episodes;
    `);
  }
  if (tableHasColumns(db, "pellets", ["id", "content", "created_at"])) {
    db.exec(`
      INSERT OR IGNORE INTO memories
        (id, kind, content, importance, valid_at, created_at, updated_at)
      SELECT
        id,
        'semantic',
        content,
        0.5,
        COALESCE(created_at, '1970-01-01T00:00:00Z'),
        COALESCE(created_at, '1970-01-01T00:00:00Z'),
        COALESCE(created_at, '1970-01-01T00:00:00Z')
      FROM pellets;
    `);
  }
  if (tableHasColumns(db, "summaries", ["id", "summary_text", "created_at"])) {
    db.exec(`
      INSERT OR IGNORE INTO memories
        (id, kind, content, importance, valid_at, created_at, updated_at)
      SELECT
        id,
        'episodic',
        summary_text,
        0.5,
        COALESCE(created_at, '1970-01-01T00:00:00Z'),
        COALESCE(created_at, '1970-01-01T00:00:00Z'),
        COALESCE(created_at, '1970-01-01T00:00:00Z')
      FROM summaries;
    `);
  }
}

export function applyV26WebAttemptMetadataMigration(db: Database.Database): void {
  const cols = db.prepare(`PRAGMA table_info(tool_executions)`).all() as Array<{ name: string }>;
  if (!cols.some((c) => c.name === "attempt_metadata")) {
    db.exec(`ALTER TABLE tool_executions ADD COLUMN attempt_metadata TEXT;`);
  }
}

/**
 * Schema v27 — Element 16c: host-aware learned tool routing.
 *
 * Adds `host_root TEXT NOT NULL DEFAULT ''` to `tool_edges` and extends the
 * primary key to (from_tool, to_tool, capability_tag, host_root) so global
 * (host_root='') and per-host learned edges coexist as distinct rows.
 * SQLite cannot extend a PK via ALTER TABLE — we rebuild with table-swap.
 *
 * Idempotent: if the column already exists, only ensure the secondary index.
 */
export function applyV27HostRootMigration(db: Database.Database): void {
  const cols = db.prepare(`PRAGMA table_info(tool_edges)`).all() as Array<{
    name: string;
  }>;
  if (cols.some((c) => c.name === "host_root")) {
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_tool_edges_host_capability
        ON tool_edges(host_root, capability_tag, from_tool);
    `);
    return;
  }
  db.exec(`
    BEGIN;
    CREATE TABLE tool_edges_new (
      from_tool       TEXT NOT NULL,
      to_tool         TEXT NOT NULL,
      capability_tag  TEXT NOT NULL,
      host_root       TEXT NOT NULL DEFAULT '',
      success_rate    REAL NOT NULL DEFAULT 0,
      avg_duration_ms INTEGER NOT NULL DEFAULT 0,
      sample_count    INTEGER NOT NULL DEFAULT 0,
      updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (from_tool, to_tool, capability_tag, host_root)
    );
    INSERT INTO tool_edges_new (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count, updated_at)
      SELECT from_tool, to_tool, capability_tag, '', success_rate, avg_duration_ms, sample_count, updated_at FROM tool_edges;
    DROP TABLE tool_edges;
    ALTER TABLE tool_edges_new RENAME TO tool_edges;
    CREATE INDEX idx_tool_edges_capability ON tool_edges(capability_tag, from_tool);
    CREATE INDEX idx_tool_edges_host_capability ON tool_edges(host_root, capability_tag, from_tool);
    COMMIT;
  `);
}

function applyV29SkillUsageMigration(db: Database.Database): void {
  db.prepare(`
    CREATE TABLE IF NOT EXISTS skill_usage (
      skill_name       TEXT    PRIMARY KEY,
      selection_count  INTEGER NOT NULL DEFAULT 0,
      success_count    INTEGER NOT NULL DEFAULT 0,
      failure_count    INTEGER NOT NULL DEFAULT 0,
      avg_duration_ms  REAL    NOT NULL DEFAULT 0,
      last_used_at     TEXT
    )
  `).run();
}

export function applyV28Element17Migration(db: Database.Database): void {
  // Drop legacy owls table (created v10, never used by live routing)
  db.exec(`DROP TABLE IF EXISTS owls;`)

  // Per-owl EWMA reward signal — feeds SecretaryRouter quality weighting
  db.exec(`
    CREATE TABLE IF NOT EXISTS owl_quality_metrics (
      owl_name     TEXT NOT NULL,
      owner_id     TEXT NOT NULL,
      turn_count   INTEGER NOT NULL DEFAULT 0,
      ewma_reward  REAL    NOT NULL DEFAULT 0.7,
      last_updated TEXT,
      PRIMARY KEY (owl_name, owner_id)
    );
    CREATE INDEX IF NOT EXISTS idx_owl_quality_metrics_owner ON owl_quality_metrics(owner_id);
  `)

  // Per-channel pin isolation (replaces single active_pin column on user_profiles)
  db.exec(`
    CREATE TABLE IF NOT EXISTS owl_pins (
      user_id    TEXT NOT NULL,
      channel_id TEXT NOT NULL,
      owl_name   TEXT NOT NULL,
      pinned_at  TEXT NOT NULL,
      PRIMARY KEY (user_id, channel_id)
    );
    CREATE INDEX IF NOT EXISTS idx_owl_pins_user ON owl_pins(user_id);
  `)

  // Recurring autonomous tasks assigned to helpers at creation time
  db.exec(`
    CREATE TABLE IF NOT EXISTS owl_recurring_jobs (
      id               TEXT PRIMARY KEY,
      helper_name      TEXT NOT NULL,
      owner_id         TEXT NOT NULL,
      schedule         TEXT NOT NULL,
      task_description TEXT NOT NULL,
      channel_id       TEXT NOT NULL,
      created_at       TEXT NOT NULL DEFAULT (datetime('now')),
      last_run_at      TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_owl_recurring_jobs_owner ON owl_recurring_jobs(owner_id);
  `)
}

export function applyV30UnifiedMemoryColumnsMigration(db: Database.Database): void {
  const existingCols = (db.prepare(`PRAGMA table_info(memories)`).all() as Array<{ name: string }>).map((c) => c.name);

  if (!existingCols.includes("domain")) {
    db.exec(`ALTER TABLE memories ADD COLUMN domain TEXT`);
  }
  if (!existingCols.includes("scope")) {
    db.exec(`ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'user'`);
  }
  if (!existingCols.includes("source")) {
    db.exec(`ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'inferred'`);
  }
  if (!existingCols.includes("confidence")) {
    db.exec(`ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5`);
  }
  if (!existingCols.includes("evidence_ids")) {
    db.exec(`ALTER TABLE memories ADD COLUMN evidence_ids TEXT NOT NULL DEFAULT '[]'`);
  }
  if (!existingCols.includes("pinned")) {
    db.exec(`ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0`);
  }
  if (!existingCols.includes("suppressed")) {
    db.exec(`ALTER TABLE memories ADD COLUMN suppressed INTEGER NOT NULL DEFAULT 0`);
  }
  if (!existingCols.includes("superseded_by")) {
    db.exec(`ALTER TABLE memories ADD COLUMN superseded_by TEXT`);
  }

  db.exec(`CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories(domain)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_memories_pinned ON memories(pinned)`);
}

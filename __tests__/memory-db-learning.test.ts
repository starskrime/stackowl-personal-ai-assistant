import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { MemoryDatabase } from "../src/memory/db.js";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-learning-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ─── TrajectoriesRepo.getFailureDensityTopics ─────────────────────

describe("TrajectoriesRepo.getFailureDensityTopics", () => {
  function insertTurn(
    trajectoryId: string,
    toolName: string,
    verificationResult: string,
    createdAt?: string,
  ) {
    (db as any).rawDb.prepare(`
      INSERT INTO trajectories (id, session_id, owl_name, user_message)
      VALUES (?, 'sess1', 'owl1', 'test')
    `).run(trajectoryId);
    (db as any).rawDb.prepare(`
      INSERT INTO trajectory_turns
        (id, trajectory_id, turn_index, tool_name, args_snapshot, result_snapshot, success, verification_result, created_at)
      VALUES (?, ?, 0, ?, '', '', 0, ?, ?)
    `).run(
      `turn_${Math.random()}`,
      trajectoryId,
      toolName,
      verificationResult,
      createdAt ?? new Date().toISOString(),
    );
  }

  it("returns tools meeting threshold", () => {
    insertTurn("t1", "web_fetch", "BLOCKED");
    insertTurn("t2", "web_fetch", "BLOCKED");
    insertTurn("t3", "web_fetch", "BLOCKED");
    const result = db.trajectories.getFailureDensityTopics(7, 2);
    expect(result).toContain("web_fetch");
  });

  it("excludes tools below min occurrences", () => {
    insertTurn("t4", "rare_tool", "BLOCKED");
    const result = db.trajectories.getFailureDensityTopics(7, 2);
    expect(result).not.toContain("rare_tool");
  });

  it("respects daysBack window", () => {
    const old = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString();
    insertTurn("t5", "old_tool", "BLOCKED", old);
    insertTurn("t6", "old_tool", "BLOCKED", old);
    const result = db.trajectories.getFailureDensityTopics(7, 2);
    expect(result).not.toContain("old_tool");
  });

  it("returns [] gracefully on missing table", () => {
    (db as any).rawDb.prepare("DROP TABLE IF EXISTS trajectory_turns").run();
    const result = db.trajectories.getFailureDensityTopics(7, 2);
    expect(result).toEqual([]);
  });
});

// ─── TrajectoriesRepo.getSessionFailures ─────────────────────────

describe("TrajectoriesRepo.getSessionFailures", () => {
  function insertTurnForSession(
    sessionId: string,
    toolName: string,
    verificationResult: string,
  ) {
    const tId = `traj_${Math.random()}`;
    (db as any).rawDb.prepare(`
      INSERT INTO trajectories (id, session_id, owl_name, user_message)
      VALUES (?, ?, 'owl1', 'test')
    `).run(tId, sessionId);
    (db as any).rawDb.prepare(`
      INSERT INTO trajectory_turns
        (id, trajectory_id, turn_index, tool_name, args_snapshot, result_snapshot, success, verification_result)
      VALUES (?, ?, 0, ?, '', '', 0, ?)
    `).run(`turn_${Math.random()}`, tId, toolName, verificationResult);
  }

  it("returns only BLOCKED and PARTIAL turns for the given session", () => {
    insertTurnForSession("sess_a", "web_fetch", "BLOCKED");
    insertTurnForSession("sess_a", "shell", "PARTIAL");
    insertTurnForSession("sess_a", "read", "ADVANCES");
    insertTurnForSession("sess_b", "web_fetch", "BLOCKED");

    const result = db.trajectories.getSessionFailures("sess_a");
    expect(result).toHaveLength(2);
    expect(result.map((r) => r.tool_name)).toEqual(
      expect.arrayContaining(["web_fetch", "shell"]),
    );
  });

  it("returns [] when no failures for session", () => {
    const result = db.trajectories.getSessionFailures("nonexistent_sess");
    expect(result).toEqual([]);
  });
});

// ─── OwlLearningsRepo.admitIfWorthy ───────────────────────────────

describe("OwlLearningsRepo.admitIfWorthy", () => {
  it("admits a novel entry", () => {
    const result = db.owlLearnings.admitIfWorthy(
      "owl1",
      "never use web_fetch for large files",
      "failure",
      0.6,
    );
    expect(result).not.toBeNull();
    expect(result?.id).toBeTruthy();
  });

  it("rejects near-duplicate within 30 days (Jaccard >= 0.6)", () => {
    db.owlLearnings.admitIfWorthy(
      "owl1",
      "avoid using web fetch for large downloads",
      "failure",
      0.6,
    );
    const result = db.owlLearnings.admitIfWorthy(
      "owl1",
      "avoid using web fetch for large downloads",
      "failure",
      0.6,
    );
    expect(result).toBeNull();
  });

  it("admits the same text if the prior entry is older than 30 days", () => {
    (db as any).rawDb.prepare(`
      INSERT INTO owl_learnings (id, owl_name, learning, category, confidence, reinforcement_count, created_at, updated_at)
      VALUES ('old1', 'owl1', 'web fetch fails on large files', 'failure', 0.6, 1,
              datetime('now', '-31 days'), datetime('now', '-31 days'))
    `).run();
    const result = db.owlLearnings.admitIfWorthy(
      "owl1",
      "web fetch fails on large files",
      "failure",
      0.6,
    );
    expect(result).not.toBeNull();
  });
});

// ─── OwlLearningsRepo.evictStale ──────────────────────────────────

describe("OwlLearningsRepo.evictStale", () => {
  function insertLearning(
    id: string,
    confidence: number,
    reinforcement: number,
    createdAt: string,
  ) {
    (db as any).rawDb.prepare(`
      INSERT INTO owl_learnings
        (id, owl_name, learning, category, confidence, reinforcement_count, created_at, updated_at)
      VALUES (?, 'owl1', 'test learning', 'insight', ?, ?, ?, ?)
    `).run(id, confidence, reinforcement, createdAt, createdAt);
  }

  it("deletes entries meeting all 3 stale criteria", () => {
    const old = new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString();
    insertLearning("stale1", 0.2, 1, old);
    const count = db.owlLearnings.evictStale();
    expect(count).toBe(1);
  });

  it("keeps entries failing any single criterion", () => {
    const old = new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString();
    insertLearning("keep1", 0.5, 1, old);
    insertLearning("keep2", 0.2, 5, old);
    insertLearning("keep3", 0.2, 1, new Date().toISOString());
    const count = db.owlLearnings.evictStale();
    expect(count).toBe(0);
  });

  it("is idempotent — second call returns 0", () => {
    const old = new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString();
    insertLearning("stale2", 0.2, 1, old);
    db.owlLearnings.evictStale();
    const second = db.owlLearnings.evictStale();
    expect(second).toBe(0);
  });
});

// ─── ApproachLibraryRepo.getEffectivenessScore ────────────────────

describe("ApproachLibraryRepo.getEffectivenessScore", () => {
  it("returns 0.5 on no history", () => {
    const score = db.approachLibrary.getEffectivenessScore("owl1", "unknown_tool");
    expect(score).toBe(0.5);
  });

  it("returns > 0.5 for 100% success history", () => {
    db.approachLibrary.record("owl1", "web_fetch", "fetch pdf", "url=x", "success");
    db.approachLibrary.record("owl1", "web_fetch", "fetch html", "url=y", "success");
    const score = db.approachLibrary.getEffectivenessScore("owl1", "web_fetch");
    expect(score).toBeGreaterThan(0.5);
  });

  it("applies recency decay — older successes score lower than fresh ones", () => {
    const old = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
    (db as any).rawDb.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, created_at)
      VALUES ('old_s', 'owl1', 'old_tool', 'kw', 'args', 'success', ?)
    `).run(old);
    const oldScore = db.approachLibrary.getEffectivenessScore("owl1", "old_tool");

    db.approachLibrary.record("owl1", "new_tool", "kw", "args", "success");
    const freshScore = db.approachLibrary.getEffectivenessScore("owl1", "new_tool");

    expect(freshScore).toBeGreaterThan(oldScore);
  });
});

// ─── ApproachLibraryRepo.getRepeatFailureWarning ─────────────────

describe("ApproachLibraryRepo.getRepeatFailureWarning", () => {
  it("returns null when no similar failures exist", () => {
    const result = db.approachLibrary.getRepeatFailureWarning("web_fetch", [
      "download", "pdf",
    ]);
    expect(result).toBeNull();
  });

  it("returns warning string when Jaccard >= 0.6", () => {
    (db as any).rawDb.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, failure_reason)
      VALUES ('f1', 'owl1', 'web_fetch', 'download large file pdf', 'url=x', 'failure', 'timeout')
    `).run();
    const result = db.approachLibrary.getRepeatFailureWarning("web_fetch", [
      "download", "large", "file",
    ]);
    expect(result).not.toBeNull();
    expect(result).toContain("web_fetch");
    expect(result).toContain("timeout");
  });

  it("returns null on second call within 1 hour (cooldown)", () => {
    (db as any).rawDb.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, failure_reason)
      VALUES ('f2', 'owl1', 'shell', 'run bash script', 'cmd=x', 'failure', 'permission denied')
    `).run();
    db.approachLibrary.getRepeatFailureWarning("shell", ["run", "bash", "script"]);
    const result = db.approachLibrary.getRepeatFailureWarning("shell", [
      "run", "bash", "script",
    ]);
    expect(result).toBeNull();
  });

  it("new db instance resets cooldown", () => {
    (db as any).rawDb.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, failure_reason)
      VALUES ('f3', 'owl1', 'write_file', 'write config file', 'path=x', 'failure', 'disk full')
    `).run();
    db.approachLibrary.getRepeatFailureWarning("write_file", ["write", "config", "file"]);

    const db2 = new MemoryDatabase(tmpDir);
    const result = db2.approachLibrary.getRepeatFailureWarning("write_file", [
      "write", "config", "file",
    ]);
    expect(result).not.toBeNull();
  });
});

/**
 * StackOwl — Element 7 T11 — PersonalizedRouter
 *
 * KNN over historical successful trajectories: embed the user message,
 * cosine-rank past trajectory user_messages, aggregate the tool_name from
 * each top-K trajectory's turns, return as a deduped suggestion list.
 *
 * Tests inject a deterministic bag-of-tokens embedder so we don't pull in
 * the fastembed model. Cosine alignment correlates with shared tokens —
 * sufficient to verify the KNN logic without a real embedding model.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { MemoryDatabase } from "../../src/memory/db.js";
import { PersonalizedRouter } from "../../src/tools/cortex/personalized-router.js";

/** Bag-of-tokens hash embedder — same words land in same buckets. */
function stubEmbed(text: string): number[] {
  const v = new Array(64).fill(0) as number[];
  for (const w of text.toLowerCase().split(/\s+/).filter(Boolean)) {
    let h = 0;
    for (const c of w) h = (h * 31 + c.charCodeAt(0)) % 64;
    v[h] += 1;
  }
  return v;
}

function seedTrajectory(
  db: MemoryDatabase,
  id: string,
  userMessage: string,
  tools: string[],
): void {
  db.rawDb
    .prepare(
      "INSERT INTO trajectories (id, session_id, owl_name, user_message, outcome, created_at) VALUES (?, ?, ?, ?, 'success', datetime('now'))",
    )
    .run(id, "s", "default", userMessage);
  let idx = 0;
  for (const t of tools) {
    db.rawDb
      .prepare(
        "INSERT INTO trajectory_turns (id, trajectory_id, turn_index, tool_name, success) VALUES (?, ?, ?, ?, 1)",
      )
      .run(randomUUID(), id, idx++, t);
  }
}

describe("PersonalizedRouter — KNN over trajectories", () => {
  let db: MemoryDatabase;

  beforeEach(() => {
    const dir = mkdtempSync(join(tmpdir(), "ptr-"));
    db = new MemoryDatabase(dir);
  });

  it("returns empty for cold-start (< 50 successful trajectories)", async () => {
    const router = new PersonalizedRouter(db, async (t) => stubEmbed(t));
    seedTrajectory(db, "t1", "research the latest typescript release", ["web"]);
    const out = await router.suggestTools("anything");
    expect(out).toEqual([]);
  });

  it("returns tool sequences from semantically similar past successes", async () => {
    // 50 noise trajectories with no shared tokens to the query
    for (let i = 0; i < 50; i++) {
      seedTrajectory(
        db,
        `noise-${i}`,
        `buy groceries item ${i} from market`,
        ["memory"],
      );
    }
    // Two typescript-related successes — should rank highest for a TS query
    seedTrajectory(db, "ts-1", "research the latest typescript release", [
      "web",
      "document",
    ]);
    seedTrajectory(db, "ts-2", "summarize typescript release notes", [
      "web",
    ]);

    const router = new PersonalizedRouter(db, async (t) => stubEmbed(t));
    const out = await router.suggestTools(
      "look up the typescript 5.5 release notes",
      { topK: 2 },
    );
    expect(out).toContain("web");
    expect(out).toContain("document");
    expect(out).not.toContain("memory");
  });

  it("respects windowDays — old trajectories are excluded", async () => {
    // Seed 100 successful trajectories but mark them all as 90 days old
    for (let i = 0; i < 100; i++) {
      db.rawDb
        .prepare(
          "INSERT INTO trajectories (id, session_id, owl_name, user_message, outcome, created_at) VALUES (?, 's', 'default', ?, 'success', datetime('now', '-90 days'))",
        )
        .run(`old-${i}`, `old query ${i}`);
    }
    const router = new PersonalizedRouter(db, async (t) => stubEmbed(t));
    const out = await router.suggestTools("any query", { windowDays: 30 });
    expect(out).toEqual([]);
  });
});

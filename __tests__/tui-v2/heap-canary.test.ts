import { describe, it, expect, beforeEach } from "vitest";
import {
  applyToStore,
  getStore,
  resetStore,
} from "../../src/cli/v2/state/store.js";
import { reduce } from "../../src/cli/v2/events/reducer.js";

// 1000-turn synthetic replay: each turn = turn.started + 50 token deltas + turn.committed
// After all turns: assert heap is under budget
describe("TUI v2 heap canary — 1000 turns", () => {
  beforeEach(() => {
    resetStore();
  });

  it("stays under 50MB heap growth over 1000 turns", async () => {
    // Force GC if available (run with --expose-gc via test:heap script)
    const gc = (global as unknown as { gc?: () => void }).gc;
    if (gc) gc();

    const heapBefore = process.memoryUsage().heapUsed;

    for (let i = 0; i < 1000; i++) {
      const turnId = `turn-${i}`;

      // Turn started
      applyToStore((state) =>
        reduce(state, {
          kind: "turn.started",
          turnId,
          owlId: "sage",
          owlName: "Sage",
          owlEmoji: "🦉",
        })
      );

      // 50 token deltas
      for (let t = 0; t < 50; t++) {
        applyToStore((state) =>
          reduce(state, {
            kind: "token.delta",
            turnId,
            text: "word ",
          })
        );
      }

      // Turn committed
      applyToStore((state) =>
        reduce(state, {
          kind: "turn.committed",
          turnId,
          text: "word ".repeat(50),
          usage: { promptTokens: 100, completionTokens: 50, costUsd: 0.001 },
        })
      );
    }

    if (gc) gc();
    const heapAfter = process.memoryUsage().heapUsed;
    const growthMB = (heapAfter - heapBefore) / 1024 / 1024;

    // Budget: 50MB for 1000 turns. Fail if ring buffer isn't working.
    expect(growthMB).toBeLessThan(50);

    // Ring buffer invariant: committed turns in store must be ≤ 200 (not 1000)
    const turns = getStore().turns;
    expect(turns.length).toBeLessThanOrEqual(200);
  });

  it("ring buffer trims to exactly 200 turns after 1000 committed turns", () => {
    for (let i = 0; i < 1000; i++) {
      const turnId = `turn-${i}`;
      applyToStore((state) =>
        reduce(state, {
          kind: "turn.started",
          turnId,
          owlId: "sage",
          owlName: "Sage",
          owlEmoji: "🦉",
        })
      );
      applyToStore((state) =>
        reduce(state, {
          kind: "turn.committed",
          turnId,
          text: `Turn ${i} text`,
        })
      );
    }

    const turns = getStore().turns;
    // Must be exactly 200 (the ring buffer max), not 1000
    expect(turns.length).toBe(200);
    // Must hold the LAST 200 turns (turn-800 through turn-999)
    expect(turns[0].turnId).toBe("turn-800");
    expect(turns[199].turnId).toBe("turn-999");
  });

  it("liveTurn is null after turn.committed (no streaming state leak)", () => {
    const turnId = "turn-leak-check";

    applyToStore((state) =>
      reduce(state, {
        kind: "turn.started",
        turnId,
        owlId: "sage",
        owlName: "Sage",
        owlEmoji: "🦉",
      })
    );

    // Stream some tokens
    for (let t = 0; t < 10; t++) {
      applyToStore((state) =>
        reduce(state, { kind: "token.delta", turnId, text: "tok " })
      );
    }

    // Verify liveTurn is populated before commit
    expect(getStore().liveTurn).not.toBeNull();
    expect(getStore().liveTurn?.turnId).toBe(turnId);

    applyToStore((state) =>
      reduce(state, {
        kind: "turn.committed",
        turnId,
        text: "tok ".repeat(10),
      })
    );

    // After commit liveTurn must be null — no dangling streaming state
    expect(getStore().liveTurn).toBeNull();
  });
});

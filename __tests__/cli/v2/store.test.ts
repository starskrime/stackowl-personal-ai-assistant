/**
 * store.test.ts — Zustand UiState contract tests.
 *
 * Verifies: initial state shape, slice isolation, and reset idempotency.
 * No Ink rendering — pure state-machine tests.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { freshStore, getStore, applyToStore } from "./fixtures/store.js";
import { reduce } from "../../../src/cli/v2/events/reducer.js";
import { resetStore } from "../../../src/cli/v2/state/store.js";

// ─── Initial state ────────────────────────────────────────────────────────────

describe("freshStore() — initial state", () => {
  it("has an empty turns array", () => {
    const state = freshStore();
    expect(state.turns).toEqual([]);
  });

  it("has generating set to false", () => {
    const state = freshStore();
    expect(state.generating).toBe(false);
  });

  it("has an empty panelStack", () => {
    const state = freshStore();
    expect(state.panelStack).toEqual([]);
  });

  it("has activePanel set to null", () => {
    const state = freshStore();
    expect(state.activePanel).toBeNull();
  });

  it("has liveTurn set to null", () => {
    const state = freshStore();
    expect(state.liveTurn).toBeNull();
  });

  it("has mode set to 'chat'", () => {
    const state = freshStore();
    expect(state.mode).toBe("chat");
  });

  it("has activeSessionId set to null", () => {
    const state = freshStore();
    expect(state.activeSessionId).toBeNull();
  });

  it("has recentSessions as empty array", () => {
    const state = freshStore();
    expect(state.recentSessions).toEqual([]);
  });
});

// ─── Slice isolation ──────────────────────────────────────────────────────────

describe("slice isolation — session.changed does not corrupt turns or panel", () => {
  beforeEach(() => {
    resetStore();
  });

  it("applying session.changed updates activeSessionId but leaves turns intact", () => {
    // First put a turn in the store via the reducer
    applyToStore((state) =>
      reduce(state, {
        kind: "user.message",
        turnId: "t1",
        text: "hello",
      }),
    );

    // Apply session.changed directly through the reducer (no Ink render needed)
    applyToStore((state) =>
      reduce(state, { kind: "session.changed", sessionId: "s-99" }),
    );

    const state = getStore();
    // The session slice updated
    expect(state.activeSessionId).toBe("s-99");
    // The turns slice is intact — still has the user message we put in
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0].text).toBe("hello");
    // Panel slice untouched
    expect(state.panelStack).toEqual([]);
  });

  it("opening a panel does not affect turns", () => {
    applyToStore((state) =>
      reduce(state, {
        kind: "user.message",
        turnId: "t2",
        text: "world",
      }),
    );

    applyToStore((state) =>
      reduce(state, {
        kind: "panel.opened",
        id: "skills",
        props: { title: "Skills", items: [] },
      }),
    );

    const state = getStore();
    expect(state.panelStack).toHaveLength(1);
    expect(state.panelStack[0].id).toBe("skills");
    // turns untouched
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0].text).toBe("world");
  });
});

// ─── Reset idempotency ────────────────────────────────────────────────────────

describe("store reset — no bleed between test cases", () => {
  it("first call: adds a turn", () => {
    freshStore();
    applyToStore((state) =>
      reduce(state, {
        kind: "user.message",
        turnId: "bleed-1",
        text: "should not bleed",
      }),
    );
    expect(getStore().turns).toHaveLength(1);
  });

  it("second call: freshStore sees empty turns — no bleed from previous test", () => {
    const state = freshStore();
    expect(state.turns).toHaveLength(0);
  });

  it("calling freshStore() twice returns equivalent initial snapshots", () => {
    const a = freshStore();
    const b = freshStore();
    expect(a).toEqual(b);
  });

  it("generating flag is false after reset even if a turn was started", () => {
    freshStore();
    applyToStore((state) =>
      reduce(state, {
        kind: "turn.started",
        turnId: "gen-1",
        owlId: "owl-a",
        owlName: "Athena",
        owlEmoji: "🦉",
      }),
    );
    expect(getStore().generating).toBe(true);

    // Reset — flag must clear
    freshStore();
    expect(getStore().generating).toBe(false);
  });
});

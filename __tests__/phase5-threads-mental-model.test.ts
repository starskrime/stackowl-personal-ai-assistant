/**
 * Tests for Phase 5: Narrative Threads + User Mental Model
 *
 * Tests:
 *   - IntentStateMachine thread promotion, matching, resume, decay
 *   - UserMentalModel calibration, state inference, context injection
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { IntentStateMachine } from "../src/intent/state-machine.js";
import { UserMentalModel } from "../src/cognition/user-mental-model.js";
import type { Intent } from "../src/intent/types.js";

// ─── IntentStateMachine Thread Tests ────────────────────────────

describe("IntentStateMachine — Narrative Threads", () => {
  let sm: IntentStateMachine;

  beforeEach(() => {
    // Create with a temp path — we won't persist
    sm = new IntentStateMachine("/tmp/test-workspace-threads");
    (sm as any).loaded = true; // skip file I/O
  });

  it("promotes an intent to a thread", () => {
    const intent = sm.create({
      rawQuery: "set up email automation",
      description: "Set up email automation",
      type: "task",
      sessionId: "sess1",
    });

    sm.promoteToThread(intent.id, "Email automation setup across sessions");

    const promoted = sm.getActiveThreads();
    expect(promoted).toHaveLength(1);
    expect(promoted[0].isThread).toBe(true);
    expect(promoted[0].summary).toBe("Email automation setup across sessions");
    expect(promoted[0].sessions).toEqual(["sess1"]);
    expect(promoted[0].resumeCount).toBe(0);
  });

  it("does not double-promote a thread", () => {
    const intent = sm.create({
      rawQuery: "test",
      description: "Test task",
      type: "task",
      sessionId: "sess1",
    });
    sm.promoteToThread(intent.id, "First summary");
    sm.promoteToThread(intent.id, "Second summary");

    const threads = sm.getActiveThreads();
    expect(threads).toHaveLength(1);
    expect(threads[0].summary).toBe("First summary"); // unchanged
  });

  it("matches thread by topic keyword overlap", () => {
    const intent = sm.create({
      rawQuery: "configure email templates",
      description: "Configure email templates for marketing",
      type: "task",
      sessionId: "sess1",
    });
    sm.promoteToThread(intent.id, "Configure email templates for marketing campaigns");

    // Should match — shares "email" and "templates"
    const match = sm.getThreadForTopic("I want to update the email templates");
    expect(match).not.toBeNull();
    expect(match!.id).toBe(intent.id);

    // Should NOT match — unrelated topic
    const noMatch = sm.getThreadForTopic("deploy kubernetes cluster");
    expect(noMatch).toBeNull();
  });

  it("resumes a thread and increments counters", () => {
    const intent = sm.create({
      rawQuery: "database migration",
      description: "Database migration project",
      type: "task",
      sessionId: "sess1",
    });
    sm.promoteToThread(intent.id, "Database migration project");

    sm.resumeThread(intent.id, "sess2");

    const thread = sm.getActiveThreads()[0];
    expect(thread.resumeCount).toBe(1);
    expect(thread.sessions).toEqual(["sess1", "sess2"]);

    // Resume again from same session — shouldn't duplicate
    sm.resumeThread(intent.id, "sess2");
    expect(thread.sessions).toEqual(["sess1", "sess2"]);
    expect(thread.resumeCount).toBe(2);
  });

  it("resumes abandoned thread back to in_progress", () => {
    const intent = sm.create({
      rawQuery: "test",
      description: "Test",
      type: "task",
      sessionId: "sess1",
    });
    sm.promoteToThread(intent.id, "Test thread");
    sm.transition(intent.id, "abandoned");

    sm.resumeThread(intent.id, "sess2");
    expect(sm.getActiveThreads()).toHaveLength(1);
    expect(sm.getActiveThreads()[0].status).toBe("in_progress");
  });

  it("decays stale threads (>14 days)", () => {
    const intent = sm.create({
      rawQuery: "old project",
      description: "Old project thread",
      type: "task",
      sessionId: "sess1",
    });
    sm.promoteToThread(intent.id, "Old project");

    // Artificially age the thread
    const intentObj = (sm as any).intents.get(intent.id) as Intent;
    intentObj.lastActiveAt = Date.now() - 15 * 24 * 60 * 60 * 1000; // 15 days ago

    const decayed = sm.decayThreads();
    expect(decayed).toBe(1);
    expect(sm.getActiveThreads()).toHaveLength(0);
  });

  it("includes threads in toContextString()", () => {
    const intent = sm.create({
      rawQuery: "test",
      description: "Test task",
      type: "task",
      sessionId: "sess1",
    });
    sm.promoteToThread(intent.id, "Test project thread");

    const ctx = sm.toContextString();
    expect(ctx).toContain("<narrative_threads>");
    expect(ctx).toContain("Test project thread");
    expect(ctx).toContain("</narrative_threads>");
  });
});

// ─── UserMentalModel Tests ──────────────────────────────────────

describe("UserMentalModel", () => {
  it("returns null when not calibrated", () => {
    const model = new UserMentalModel();
    model.update("hello");
    model.update("test");
    model.update("another message");
    expect(model.getState()).toBeNull();
    expect(model.toContextString()).toBe("");
  });

  it("calibrates after 10 sessions", () => {
    const model = new UserMentalModel({
      avgMessageLength: 50,
      avgResponseLatency: 5000,
      sessionCount: 10,
    });
    expect(model.isCalibrated()).toBe(true);
  });

  it("returns null with fewer than 3 messages in session", () => {
    const model = new UserMentalModel({
      avgMessageLength: 50,
      avgResponseLatency: 5000,
      sessionCount: 10,
    });
    model.update("hi");
    model.update("there");
    expect(model.getState()).toBeNull(); // only 2 messages
  });

  it("detects focused state with steady messages and no switches", () => {
    const model = new UserMentalModel({
      avgMessageLength: 50,
      avgResponseLatency: 5000,
      sessionCount: 15,
    });

    // Send 6 messages of typical length — no topic switches
    const now = Date.now();
    for (let i = 0; i < 6; i++) {
      model.update(
        "This is a typical message about the current topic we are discussing",
        now + i * 5000,
      );
    }

    const state = model.getState();
    expect(state).not.toBeNull();
    expect(state!.likelyState).toBe("focused");
  });

  it("detects browsing state with many topic switches", () => {
    const model = new UserMentalModel({
      avgMessageLength: 50,
      avgResponseLatency: 5000,
      sessionCount: 15,
    });

    const now = Date.now();
    // Need at least 3 messages for getState to work
    for (let i = 0; i < 5; i++) {
      model.update("test message " + i, now + i * 5000);
    }
    // 4 topic switches → browsing score 0.5
    model.recordTopicSwitch();
    model.recordTopicSwitch();
    model.recordTopicSwitch();
    model.recordTopicSwitch();

    const state = model.getState();
    expect(state).not.toBeNull();
    expect(state!.likelyState).toBe("browsing");
  });

  it("detects frustrated state with clarification requests and short messages", () => {
    const model = new UserMentalModel({
      avgMessageLength: 100, // baseline is long messages
      avgResponseLatency: 5000,
      sessionCount: 15,
    });

    const now = Date.now();
    // Very short messages (< 30 = baseline * 0.3) with clarification patterns
    model.update("huh?", now);
    model.update("what do you mean", now + 3000);
    model.update("I don't understand", now + 6000);
    // Add a repeated question to trigger questionRepetitions
    model.update("huh?", now + 9000);

    const state = model.getState();
    expect(state).not.toBeNull();
    expect(state!.likelyState).toBe("frustrated");
  });

  it("updates baseline on endSession", () => {
    const model = new UserMentalModel({
      avgMessageLength: 50,
      avgResponseLatency: 5000,
      sessionCount: 10,
    });

    const now = Date.now();
    model.update("a short msg", now);
    model.update("another msg", now + 3000);

    model.endSession();

    const baseline = model.getBaseline();
    expect(baseline.sessionCount).toBe(11);
    expect(baseline.avgMessageLength).toBeGreaterThan(0);
  });

  it("formats context string only for confident inferences", () => {
    const model = new UserMentalModel({
      avgMessageLength: 100,
      avgResponseLatency: 5000,
      sessionCount: 15,
    });

    const now = Date.now();
    // Very short messages with clarification + repetition → frustrated
    model.update("huh?", now);
    model.update("what do you mean", now + 2000);
    model.update("I don't understand", now + 4000);
    model.update("huh?", now + 6000);

    const ctx = model.toContextString();
    expect(ctx).toContain("<user_state");
    expect(ctx).toContain("frustrated");
    expect(ctx).toContain("</user_state>");
  });

  it("resets session signals without affecting baseline", () => {
    const model = new UserMentalModel({
      avgMessageLength: 50,
      avgResponseLatency: 5000,
      sessionCount: 10,
    });

    model.update("test");
    model.update("test2");
    model.recordTopicSwitch();

    model.resetSessionSignals();

    // After reset, need 3+ messages to get state
    expect(model.getState()).toBeNull();
    // Baseline unchanged
    expect(model.getBaseline().sessionCount).toBe(10);
  });
});

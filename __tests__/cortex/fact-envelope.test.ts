/**
 * StackOwl — Element 7 T16 — FactEnvelopeStore
 *
 * In-memory provenance store keyed by `(sessionId, turnIndex)`. Holds
 * envelopes that wrap tool outputs with their source metadata. T17 layers
 * `fact:retracted` event emission + ContextPipeline stripping on top.
 *
 * Cost-critical: this never enters the rendered LLM prompt — it stays in
 * working memory only and is consulted by downstream verifiers.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  FactEnvelopeStore,
  type FactEnvelope,
} from "../../src/tools/cortex/fact-envelope.js";

const sample = (toolName: string): Omit<FactEnvelope, "retracted"> => ({
  content: `result from ${toolName}`,
  provenance: {
    toolName,
    args: { q: "x" },
    durationMs: 42,
    confidence: 0.9,
  },
});

describe("FactEnvelopeStore — in-memory provenance", () => {
  let store: FactEnvelopeStore;
  beforeEach(() => {
    store = new FactEnvelopeStore();
  });

  it("records and retrieves an envelope by (sessionId, turnIndex)", () => {
    store.record("s1", 0, sample("web"));
    const got = store.get("s1", 0);
    expect(got).not.toBeNull();
    expect(got!.content).toBe("result from web");
    expect(got!.provenance.toolName).toBe("web");
    expect(got!.retracted).toBe(false);
  });

  it("returns null for unknown keys", () => {
    expect(store.get("s1", 0)).toBeNull();
  });

  it("retract flips the retracted flag and returns the envelope", () => {
    store.record("s1", 3, sample("memory"));
    const result = store.retract("s1", 3);
    expect(result).not.toBeNull();
    expect(result!.retracted).toBe(true);
    expect(store.get("s1", 3)?.retracted).toBe(true);
  });

  it("retract returns null when the envelope does not exist", () => {
    expect(store.retract("missing", 99)).toBeNull();
  });

  it("getActive returns only non-retracted envelopes for a session", () => {
    store.record("s1", 0, sample("web"));
    store.record("s1", 1, sample("memory"));
    store.record("s1", 2, sample("shell"));
    store.record("s2", 0, sample("other"));
    store.retract("s1", 1);

    const active = store.getActive("s1");
    expect(active).toHaveLength(2);
    expect(active.map((e) => e.provenance.toolName).sort()).toEqual([
      "shell",
      "web",
    ]);
  });

  it("evicts the oldest entry once a session exceeds the per-session cap", () => {
    const tiny = new FactEnvelopeStore({ maxPerSession: 3 });
    tiny.record("s1", 0, sample("a"));
    tiny.record("s1", 1, sample("b"));
    tiny.record("s1", 2, sample("c"));
    tiny.record("s1", 3, sample("d")); // pushes "a" out

    expect(tiny.get("s1", 0)).toBeNull();
    expect(tiny.get("s1", 1)).not.toBeNull();
    expect(tiny.get("s1", 3)).not.toBeNull();
  });

  it("clearSession drops every envelope for that session only", () => {
    store.record("s1", 0, sample("a"));
    store.record("s2", 0, sample("b"));
    store.clearSession("s1");
    expect(store.get("s1", 0)).toBeNull();
    expect(store.get("s2", 0)).not.toBeNull();
  });
});

import { describe, it, expect, vi } from "vitest";
import {
  SignalClassifier,
  type ClassifierProvider,
} from "../../src/signals/classifier.js";
import type { ContextSignal } from "../../src/ambient/types.js";

const sig: ContextSignal = {
  id: "1",
  source: "git",
  priority: "low",
  title: "12 uncommitted files",
  content: "M src/x.ts",
  timestamp: 0,
  ttlMs: 60_000,
};

function fakeProvider(content: string): ClassifierProvider {
  return { chat: vi.fn(async () => ({ content })) };
}

function makeSignal(source: string, title: string, content: string): ContextSignal {
  return {
    id: "test-id",
    source: source as any,
    title,
    content,
    priority: "medium",
    timestamp: Date.now(),
    ttlMs: 60_000,
    userSurfaceable: false,
  };
}

describe("SignalClassifier", () => {
  it("returns parsed JSON {keep, confidence}", async () => {
    const c = new SignalClassifier(fakeProvider(`{"keep":true,"confidence":0.85}`));
    const r = await c.classify(sig);
    expect(r).toEqual({ keep: true, confidence: 0.85 });
  });

  it("treats malformed JSON as drop", async () => {
    const c = new SignalClassifier(fakeProvider("not json"));
    const r = await c.classify(sig);
    expect(r).toEqual({ keep: false, confidence: 0 });
  });

  it("treats provider throw as drop (fail-closed)", async () => {
    const c = new SignalClassifier({
      chat: vi.fn(async () => {
        throw new Error("down");
      }),
    });
    const r = await c.classify(sig);
    expect(r).toEqual({ keep: false, confidence: 0 });
  });

  it("clamps confidence to [0,1]", async () => {
    const c = new SignalClassifier(fakeProvider(`{"keep":true,"confidence":2.5}`));
    const r = await c.classify(sig);
    expect(r.confidence).toBe(1);
  });
});

describe("SignalClassifier heuristic pre-filter", () => {
  it("returns keep=false for empty content without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(makeSignal("clipboard", "Clipboard", ""));
    expect(result.keep).toBe(false);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("returns keep=false for content shorter than 5 chars without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(makeSignal("clipboard", "Clip", "hi"));
    expect(result.keep).toBe(false);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("returns keep=false for time_of_day source without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    // Use non-ISO8601 content so Rule 4 doesn't intercept — tests Rule 3 directly
    const result = await classifier.classify(makeSignal("time_of_day", "Current time", "It is now 2:30 PM on Sunday"));
    expect(result.keep).toBe(false);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("returns keep=false for system source without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(makeSignal("system", "CPU", "CPU usage is at 45% memory 2GB free"));
    expect(result.keep).toBe(false);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("returns keep=false for pure ISO8601 timestamp content without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(makeSignal("git", "Timestamp", "2026-05-17T14:30:00Z"));
    expect(result.keep).toBe(false);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("returns keep=true for error keyword content without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    // priority must be "medium" so Rule 1 doesn't fire — tests Rule 5
    const result = await classifier.classify(makeSignal("git_status", "Build output", "fatal error: compilation failed with 3 errors"));
    expect(result.keep).toBe(true);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("calls LLM only for non-obvious signals", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: '{"keep":true,"confidence":0.8}' }),
    };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(
      makeSignal("git", "Modified files", "src/engine/runtime.ts has uncommitted changes with 40 new lines implementing X"),
    );
    expect(mockProvider.chat).toHaveBeenCalledTimes(1);
    expect(result.keep).toBe(true);
  });

  it("returns keep=true immediately for critical source without LLM", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    // critical priority signals bypass classification
    const signal = { ...makeSignal("git", "Error", "fatal: merge conflict"), priority: "critical" as const };
    const result = await classifier.classify(signal);
    expect(result.keep).toBe(true);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });
});

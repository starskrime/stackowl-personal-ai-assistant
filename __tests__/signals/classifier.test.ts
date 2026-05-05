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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type {
  SignalCollector,
  ContextSignal,
} from "../../src/ambient/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const fakeBus = { emit: vi.fn(), on: vi.fn() } as any;
const fakeClassifier = {
  classify: vi.fn(async () => ({ keep: false, confidence: 0 })),
};
const fakeVerifier = { verify: vi.fn() } as any;
const fakeGoalGraph = {
  getActive: vi.fn(() => []),
  getTopPriority: vi.fn(() => undefined),
} as any;

function makePool(consent: any = {}, enabledSources?: any) {
  return new SignalPool({
    bus: fakeBus,
    classifier: fakeClassifier,
    verifier: fakeVerifier,
    goalGraph: fakeGoalGraph,
    config: { maxSignals: 32, enabledSources, consent },
    workspacePath: "/tmp",
  });
}

describe("SignalPool lifecycle", () => {
  beforeEach(() => vi.clearAllMocks());

  it("constructs without throwing", () => {
    expect(() => makePool()).not.toThrow();
  });

  it("addCollector accepts a collector", () => {
    const pool = makePool();
    const c: SignalCollector = {
      source: "git",
      mode: "poll",
      intervalMs: 1000,
      collect: async () => [] as ContextSignal[],
    };
    pool.addCollector(c);
    expect(pool.getState().signals).toEqual([]);
  });

  it("addCollector skips collectors whose source is not in enabledSources", () => {
    const pool = makePool({}, ["git"]);
    const c: SignalCollector = {
      source: "clipboard",
      mode: "poll",
      intervalMs: 1000,
      collect: async () => [] as ContextSignal[],
    };
    pool.addCollector(c);
    pool.start();
    pool.stop();
    expect(fakeBus.emit).not.toHaveBeenCalled();
  });

  it("start is idempotent", () => {
    const pool = makePool();
    pool.start();
    pool.start();
    pool.stop();
  });

  it("stop is idempotent", () => {
    const pool = makePool();
    pool.stop();
    pool.stop();
  });
});

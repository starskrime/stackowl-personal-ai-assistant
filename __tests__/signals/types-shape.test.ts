import { describe, it, expectTypeOf } from "vitest";
import type {
  SignalCollector,
  ContextSignal,
  ConsentMap,
} from "../../src/ambient/types.js";

describe("SignalCollector interface", () => {
  it("supports poll-mode shape", () => {
    const poll: SignalCollector = {
      source: "git",
      mode: "poll",
      intervalMs: 60_000,
      collect: async () => [] as ContextSignal[],
    };
    expectTypeOf(poll.mode).toEqualTypeOf<"poll" | "push">();
  });

  it("supports push-mode shape", () => {
    const push: SignalCollector = {
      source: "perch",
      mode: "push",
      start: (_emit) => {},
      stop: () => {},
    };
    expectTypeOf(push.mode).toEqualTypeOf<"poll" | "push">();
  });
});

describe("ConsentMap", () => {
  it("is a partial record over SignalSource", () => {
    const map: ConsentMap = { clipboard: false, git: true };
    expectTypeOf(map).toEqualTypeOf<ConsentMap>();
  });
});

describe("ContextSignal.userSurfaceable", () => {
  it("accepts an optional userSurfaceable flag", () => {
    const sig: ContextSignal = {
      id: "x",
      source: "git",
      priority: "low",
      title: "t",
      content: "c",
      timestamp: 0,
      ttlMs: 1000,
      userSurfaceable: true,
    };
    expectTypeOf(sig.userSurfaceable).toEqualTypeOf<boolean | undefined>();
  });
});

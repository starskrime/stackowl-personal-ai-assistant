import { describe, it, expectTypeOf } from "vitest";
import type { SignalCollector, ContextSignal } from "../../src/ambient/types.js";

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

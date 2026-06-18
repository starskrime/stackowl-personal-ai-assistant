import { bench, describe, beforeEach } from "vitest";
import { applyToStore, resetStore } from "../../src/cli/v2/state/store.js";
import { reduce } from "../../src/cli/v2/events/reducer.js";

describe("TUI v2 throughput benchmarks", () => {
  beforeEach(() => resetStore());

  bench("token shower: 500 token.delta events", () => {
    const turnId = "perf-turn";
    applyToStore((s) =>
      reduce(s, {
        kind: "turn.started",
        turnId,
        owlId: "owl",
        owlName: "Owl",
        owlEmoji: "🦉",
      })
    );
    for (let i = 0; i < 500; i++) {
      applyToStore((s) =>
        reduce(s, { kind: "token.delta", turnId, text: "word " })
      );
    }
    applyToStore((s) =>
      reduce(s, {
        kind: "turn.committed",
        turnId,
        text: "word ".repeat(500),
      })
    );
  });

  bench("tool storm: 5 parallel tool starts + completes", () => {
    const turnId = "tool-turn";
    applyToStore((s) =>
      reduce(s, {
        kind: "turn.started",
        turnId,
        owlId: "owl",
        owlName: "Owl",
        owlEmoji: "🦉",
      })
    );
    // Start 5 tools
    for (let i = 0; i < 5; i++) {
      applyToStore((s) =>
        reduce(s, {
          kind: "tool.requested",
          toolCallId: `tool-${i}`,
          turnId,
          toolName: `bash_${i}`,
        })
      );
    }
    // Complete all 5
    for (let i = 0; i < 5; i++) {
      applyToStore((s) =>
        reduce(s, {
          kind: "tool.completed",
          toolCallId: `tool-${i}`,
          elapsedMs: 100,
        })
      );
    }
  });

  bench("parliament round: 3 owls × positions + commit", () => {
    const debateId = "perf-debate";
    applyToStore((s) =>
      reduce(s, {
        kind: "parliament.round.started",
        debateId,
        round: 1,
        totalRounds: 3,
        owls: [
          { owlId: "owl1", owlName: "Sage", owlEmoji: "🦉" },
          { owlId: "owl2", owlName: "Hoots", owlEmoji: "🦚" },
          { owlId: "owl3", owlName: "Merlin", owlEmoji: "🦜" },
        ],
      })
    );
    // Commit positions for all 3 owls
    for (const [owlId, owlName, owlEmoji] of [
      ["owl1", "Sage", "🦉"],
      ["owl2", "Hoots", "🦚"],
      ["owl3", "Merlin", "🦜"],
    ]) {
      applyToStore((s) =>
        reduce(s, {
          kind: "parliament.position.ready",
          debateId,
          owlId,
          owlName,
          owlEmoji,
          position: "My position is ".repeat(20),
        })
      );
    }
    // Commit challenges for all 3 owls
    for (const [owlId, owlName, owlEmoji] of [
      ["owl1", "Sage", "🦉"],
      ["owl2", "Hoots", "🦚"],
      ["owl3", "Merlin", "🦜"],
    ]) {
      applyToStore((s) =>
        reduce(s, {
          kind: "parliament.challenge.ready",
          debateId,
          owlId,
          owlName,
          owlEmoji,
          challenge: "I challenge this because ".repeat(20),
        })
      );
    }
    // Synthesis
    applyToStore((s) =>
      reduce(s, {
        kind: "parliament.synthesis.ready",
        debateId,
        owlId: "owl1",
        owlName: "Sage",
        synthesis: "The synthesis is ".repeat(20),
      })
    );
  });

  bench(
    "mixed worst case: tokens + tools + parliament interleaved (100 iterations)",
    () => {
      for (let iter = 0; iter < 100; iter++) {
        const turnId = `mixed-${iter}`;
        applyToStore((s) =>
          reduce(s, {
            kind: "turn.started",
            turnId,
            owlId: "owl",
            owlName: "Owl",
            owlEmoji: "🦉",
          })
        );
        for (let t = 0; t < 10; t++) {
          applyToStore((s) =>
            reduce(s, { kind: "token.delta", turnId, text: "tok " })
          );
        }
        applyToStore((s) =>
          reduce(s, {
            kind: "tool.requested",
            toolCallId: `t-${iter}`,
            turnId,
            toolName: "bash",
          })
        );
        applyToStore((s) =>
          reduce(s, {
            kind: "tool.completed",
            toolCallId: `t-${iter}`,
            elapsedMs: 50,
          })
        );
        applyToStore((s) =>
          reduce(s, {
            kind: "turn.committed",
            turnId,
            text: "tok ".repeat(10),
          })
        );
      }
    }
  );
});

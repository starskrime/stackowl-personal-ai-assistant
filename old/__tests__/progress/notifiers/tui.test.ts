import { describe, it, expect, vi } from "vitest";
import { TuiProgressNotifier } from "../../../src/progress/notifiers/tui.js";
import type { UiBridge } from "../../../src/cli/v2/events/bridge.js";
import type { UiEvent } from "../../../src/cli/v2/events/UiEvent.js";

function makeBridge() {
  const emitted: UiEvent[] = [];
  return {
    emitted,
    emit: vi.fn((event: UiEvent) => { emitted.push(event); }),
  } as unknown as UiBridge & { emitted: UiEvent[] };
}

describe("TuiProgressNotifier", () => {
  it("start() emits thinking.phrase event", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.start("Trabajando en ello...", "turn-1");
    expect(bridge.emitted).toContainEqual({
      kind: "thinking.phrase",
      turnId: "turn-1",
      phrase: "Trabajando en ello...",
    });
  });

  it("update() emits thinking.tool event when turnId is active", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.start("phrase", "turn-1");
    await notifier.update("🐚 Running command…", "turn-1");
    expect(bridge.emitted).toContainEqual({
      kind: "thinking.tool",
      turnId: "turn-1",
      text: "🐚 Running command…",
    });
  });

  it("update() is a no-op for unknown turnId", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.update("text", "unknown");
    expect(bridge.emitted).toHaveLength(0);
  });

  it("stop() clears phrase with empty thinking.phrase", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.start("phrase", "turn-1");
    await notifier.stop("turn-1");
    const phraseEvents = bridge.emitted.filter((e) => e.kind === "thinking.phrase");
    // Last thinking.phrase event should have empty phrase
    expect(phraseEvents.at(-1)).toMatchObject({ kind: "thinking.phrase", phrase: "" });
  });

  it("stop() is a no-op for unknown turnId", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.stop("unknown");
    expect(bridge.emitted).toHaveLength(0);
  });
});

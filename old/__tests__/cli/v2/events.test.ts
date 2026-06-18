/**
 * events.test.ts — globalBridge / UiEvent contract tests.
 *
 * Verifies that captureEvents() correctly captures events, that specific event
 * kinds round-trip faithfully, and that there is no cross-call leakage.
 * No Ink rendering — pure event-bus tests.
 */

import { describe, it, expect } from "vitest";
import { captureEvents } from "./fixtures/events.js";
import { globalBridge } from "../../../src/cli/v2/events/bridge.js";
import type { UiEvent } from "../../../src/cli/v2/events/UiEvent.js";

// ─── Basic capture ────────────────────────────────────────────────────────────

describe("captureEvents() — basic capture", () => {
  it("returns an empty array when nothing is emitted", () => {
    const events = captureEvents(() => {
      // emit nothing
    });
    expect(events).toEqual([]);
  });

  it("captures a single emitted event", () => {
    const events = captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "test", text: "hello" });
    });
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe("notice");
  });

  it("captures multiple events in emission order", () => {
    const events = captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "a", text: "first" });
      globalBridge.emit({ kind: "notice", source: "b", text: "second" });
      globalBridge.emit({ kind: "notice", source: "c", text: "third" });
    });
    expect(events).toHaveLength(3);
    expect((events[0] as Extract<UiEvent, { kind: "notice" }>).text).toBe("first");
    expect((events[1] as Extract<UiEvent, { kind: "notice" }>).text).toBe("second");
    expect((events[2] as Extract<UiEvent, { kind: "notice" }>).text).toBe("third");
  });
});

// ─── notice event ─────────────────────────────────────────────────────────────

describe("captureEvents() — notice event", () => {
  it("captures a notice event with correct fields", () => {
    const events = captureEvents(() => {
      globalBridge.emit({
        kind: "notice",
        source: "instinct",
        text: "rate limit approaching",
        severity: "warn",
      });
    });
    expect(events).toHaveLength(1);
    const ev = events[0] as Extract<UiEvent, { kind: "notice" }>;
    expect(ev.kind).toBe("notice");
    expect(ev.source).toBe("instinct");
    expect(ev.text).toBe("rate limit approaching");
    expect(ev.severity).toBe("warn");
  });

  it("captures a notice with default (undefined) severity", () => {
    const events = captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "perch", text: "file changed" });
    });
    const ev = events[0] as Extract<UiEvent, { kind: "notice" }>;
    expect(ev.severity).toBeUndefined();
  });
});

// ─── Other event kinds ────────────────────────────────────────────────────────

describe("captureEvents() — mixed event kinds", () => {
  it("captures session.changed events", () => {
    const events = captureEvents(() => {
      globalBridge.emit({ kind: "session.changed", sessionId: "sess-42" });
    });
    expect(events).toHaveLength(1);
    const ev = events[0] as Extract<UiEvent, { kind: "session.changed" }>;
    expect(ev.sessionId).toBe("sess-42");
  });

  it("captures panel.opened events", () => {
    const events = captureEvents(() => {
      globalBridge.emit({ kind: "panel.opened", id: "skills", props: { title: "Skills" } });
    });
    expect(events).toHaveLength(1);
    const ev = events[0] as Extract<UiEvent, { kind: "panel.opened" }>;
    expect(ev.id).toBe("skills");
  });

  it("captures events of different kinds in one call", () => {
    const events = captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "x", text: "a" });
      globalBridge.emit({ kind: "session.changed", sessionId: "s-1" });
      globalBridge.emit({ kind: "panel.closed" });
    });
    expect(events).toHaveLength(3);
    expect(events[0].kind).toBe("notice");
    expect(events[1].kind).toBe("session.changed");
    expect(events[2].kind).toBe("panel.closed");
  });
});

// ─── No cross-call leakage ────────────────────────────────────────────────────

describe("captureEvents() — no cross-call leakage", () => {
  it("events from call A do not appear in call B", () => {
    const eventsA = captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "a", text: "from-A" });
    });

    const eventsB = captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "b", text: "from-B" });
    });

    expect(eventsA).toHaveLength(1);
    expect((eventsA[0] as Extract<UiEvent, { kind: "notice" }>).text).toBe("from-A");

    expect(eventsB).toHaveLength(1);
    expect((eventsB[0] as Extract<UiEvent, { kind: "notice" }>).text).toBe("from-B");
  });

  it("an empty call between two emitting calls captures nothing", () => {
    captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "pre", text: "pre" });
    });

    const empty = captureEvents(() => {
      // nothing
    });

    captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "post", text: "post" });
    });

    expect(empty).toHaveLength(0);
  });

  it("emissions outside captureEvents are not captured", () => {
    // Emit before subscribing
    globalBridge.emit({ kind: "notice", source: "before", text: "outside" });

    const events = captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "inside", text: "inside" });
    });

    // Emit after unsubscribing
    globalBridge.emit({ kind: "notice", source: "after", text: "outside" });

    expect(events).toHaveLength(1);
    expect((events[0] as Extract<UiEvent, { kind: "notice" }>).text).toBe("inside");
  });
});

// ─── Exception safety ─────────────────────────────────────────────────────────

describe("captureEvents() — exception safety", () => {
  it("unsubscribes even when fn throws, preventing leakage", () => {
    expect(() => {
      captureEvents(() => {
        globalBridge.emit({ kind: "notice", source: "err-test", text: "before throw" });
        throw new Error("intentional test error");
      });
    }).toThrow("intentional test error");

    // Subsequent call must capture only its own events
    const events = captureEvents(() => {
      globalBridge.emit({ kind: "notice", source: "after-throw", text: "clean" });
    });
    expect(events).toHaveLength(1);
    expect((events[0] as Extract<UiEvent, { kind: "notice" }>).text).toBe("clean");
  });
});

/**
 * StackOwl — Face State Emitter
 *
 * Translates existing EventBus events into face:state transitions.
 * No modifications to runtime.ts needed — derives face state from:
 *
 *   message:received  → listening (user sent something)
 *   tool:called       → thinking (tool in flight)
 *   message:responded → speaking (streaming answer)
 *   pellet:created    → learning (knowledge persisted)
 *   evolution:*       → growing  (owl DNA mutated)
 *   (timeout)         → idle     (2s after last speaking)
 *
 * The face server listens for face:state and face:node_added
 * events and forwards them to connected WebSocket clients.
 */

import type { EventBus } from "./bus.js";

const IDLE_TIMEOUT_MS = 2000;

export class FaceEmitter {
  private idleTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private bus: EventBus) {}

  /** Start listening to EventBus and translating events. */
  start(): void {
    const { bus } = this;

    // User sent a message → listening
    bus.on("message:received", ({ text }) => {
      this.setState("listening", text.slice(0, 60));
    });

    // Tool call in flight → thinking
    bus.on("tool:called", ({ name }) => {
      this.setState("thinking", name);
    });

    // Response completed → speaking, then idle after 2s
    bus.on("message:responded", ({ content }) => {
      this.setState("speaking", content.slice(0, 60));
      this.scheduleIdle();
    });

    // New pellet → learning
    bus.on("pellet:created", ({ id, title, tags }) => {
      this.setState("learning", title);
      bus.emit("face:node_added", { id, label: title, tags });
      this.scheduleIdle();
    });

    // Owl evolution → growing
    bus.on("evolution:triggered", ({ owlName, generation }) => {
      this.setState("growing", `${owlName} gen ${generation}`);
      this.scheduleIdle();
    });
  }

  private setState(
    state: "idle" | "listening" | "thinking" | "speaking" | "learning" | "growing",
    label?: string,
  ): void {
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    this.bus.emit("face:state", { state, label });
  }

  private scheduleIdle(): void {
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => {
      this.bus.emit("face:state", { state: "idle" });
      this.idleTimer = null;
    }, IDLE_TIMEOUT_MS);
  }
}

/**
 * StackOwl — Owl ASCII Art & Animator
 *
 * Provides multi-state owl animation for terminal display:
 *   - INLINE faces: single-line expressions used in the thinking spinner
 *   - MEDIUM frames: 5-line owl used in the boot splash
 *   - OwlAnimator: frame-advance engine that calls back on each tick
 *
 * Zero dependencies — pure Node.js + chalk.
 */

import chalk from "chalk";

export type OwlState = "sleep" | "wake" | "idle" | "think" | "talk";

// ─── Inline Faces ─────────────────────────────────────────────────
// One-character-height faces used in the thinking spinner.
// Arrays = animation frames cycled at THINK_INTERVAL_MS.

const INLINE_FACES: Record<OwlState, string[]> = {
  sleep: ["(-.-)", "(-,-)"],
  wake:  ["(-.-)", "(-_.)", "(-.o)", "(o.o)"],
  idle:  ["(o.o)", "(o,o)", "(o.o)", "(.o.)"],
  think: ["(o_o)", "(-_o)", "(o_-)", "(o_.)"],
  talk:  ["(oOo)", "(o-o)", "(oOo)", "(o-o)"],
};

// ─── Boot Owl ─────────────────────────────────────────────────────
// Multi-line ASCII owls shown during the boot splash sequence.
// All frames are exactly 5 lines × 14 chars so they don't shift.

const PAD = "              "; // blank line to keep height stable

export const BOOT_OWL: Record<"asleep" | "waking" | "awake", string[]> = {
  asleep: [
    chalk.yellow("   ,___,   "),
    chalk.yellow("  ( -.- )  ") + chalk.dim(" zzZ"),
    chalk.yellow("   )-W-(   "),
    chalk.yellow("  /|   |\\  "),
    chalk.yellow(" /_|___|_\\ "),
  ],
  waking: [
    chalk.yellow("   ,___,   "),
    chalk.yellow("  ( o.- )  "),
    chalk.yellow("   )-W-(   "),
    chalk.yellow("  /|   |\\  "),
    chalk.yellow(" /_|___|_\\ "),
  ],
  awake: [
    chalk.yellow("   ,___,   "),
    chalk.yellow("  ( o.o )  "),
    chalk.yellow("   )-W-(   "),
    chalk.yellow("  /|   |\\  "),
    chalk.yellow(" /_|___|_\\ "),
  ],
};

// ─── OwlAnimator ──────────────────────────────────────────────────

export class OwlAnimator {
  private state: OwlState = "idle";
  private frameIdx = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private onFrame: (face: string) => void;

  constructor(onFrame: (face: string) => void) {
    this.onFrame = onFrame;
  }

  /** Transition to a new state and start cycling frames. */
  transition(next: OwlState, intervalMs = 350): void {
    this.stop();
    this.state = next;
    this.frameIdx = 0;
    const frames = INLINE_FACES[next];
    this.onFrame(this.colour(frames[0]));
    if (frames.length > 1) {
      this.timer = setInterval(() => {
        this.frameIdx = (this.frameIdx + 1) % frames.length;
        this.onFrame(this.colour(INLINE_FACES[this.state][this.frameIdx]));
      }, intervalMs);
    }
  }

  /** Stop cycling and leave the current frame as-is. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Return the current face string (coloured, not newline-terminated). */
  currentFace(): string {
    const frames = INLINE_FACES[this.state];
    return this.colour(frames[this.frameIdx % frames.length]);
  }

  private colour(face: string): string {
    return this.state === "sleep"
      ? chalk.dim(face)
      : this.state === "think"
        ? chalk.cyan(face)
        : chalk.yellow(face);
  }
}

export { PAD };

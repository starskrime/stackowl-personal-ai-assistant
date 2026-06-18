/**
 * StackOwl — Owl ASCII Art & Animator
 *
 * Dark Glass palette:
 *   - asleep: dim yellow (sleeping)
 *   - waking: brighter yellow (transitioning)
 *   - awake: full yellow (alert)
 *   - think: cyan (active thinking)
 *   - idle/talk: yellow (normal)
 */

import chalk from "chalk";

export type OwlState = "sleep" | "wake" | "idle" | "think" | "talk";

// ─── Inline Faces ─────────────────────────────────────────────────

const INLINE_FACES: Record<OwlState, string[]> = {
  sleep: ["(-.-)", "(-,-)"],
  wake: ["(-.-)", "(-_.)", "(-.o)", "(o.o)"],
  idle: ["(o.o)", "(o,o)", "(o.o)", "(.o.)"],
  think: ["(o_o)", "(-_o)", "(o_-)", "(o_.)"],
  talk: ["(oOo)", "(o-o)", "(oOo)", "(o-o)"],
};

// ─── Boot Owl ─────────────────────────────────────────────────────
// 5 lines x 14 chars — stable, no shift between frames

const PAD = "              ";

export const BOOT_OWL: Record<"asleep" | "waking" | "awake", string[]> = {
  asleep: [
    chalk.dim("   ,___,   "),
    chalk.dim("  ( -.- )  ") + chalk.dim(" zzZ"),
    chalk.dim("   )-W-(   "),
    chalk.dim("  /|   |\\  "),
    chalk.dim(" /_|___|_\\ "),
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

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  currentFace(): string {
    const frames = INLINE_FACES[this.state];
    return this.colour(frames[this.frameIdx % frames.length]);
  }

  private colour(face: string): string {
    switch (this.state) {
      case "sleep":
        return chalk.dim(face);
      case "think":
        return chalk.cyan(face);
      default:
        return chalk.yellow(face);
    }
  }
}

export { PAD };

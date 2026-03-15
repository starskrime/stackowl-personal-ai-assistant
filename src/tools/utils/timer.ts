import { exec } from "node:child_process";
import type { ToolImplementation, ToolContext } from "../registry.js";

export const TimerTool: ToolImplementation = {
  definition: {
    name: "set_timer",
    description:
      "Set a timer/alarm. Sends a macOS notification and plays a sound when time is up. Duration in seconds.",
    parameters: {
      type: "object",
      properties: {
        duration: {
          type: "number",
          description: "Timer duration in seconds",
        },
        label: {
          type: "string",
          description: "Optional label for the timer",
        },
      },
      required: ["duration"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const duration = Number(args.duration);
      const label = args.label ? String(args.label) : "Timer";

      if (!isFinite(duration) || duration <= 0) {
        return "Error: Duration must be a positive number of seconds.";
      }

      const minutes = Math.floor(duration / 60);
      const seconds = duration % 60;
      const timeStr =
        minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;

      // Set the timer in the background
      setTimeout(() => {
        const escapedLabel = label.replace(/"/g, '\\"');
        const notifCmd = `osascript -e 'display notification "Timer done: ${escapedLabel}" with title "⏰ Timer"'`;
        const soundCmd = "afplay /System/Library/Sounds/Glass.aiff";

        exec(notifCmd, { timeout: 15000 }, () => {});
        exec(soundCmd, { timeout: 15000 }, () => {});
      }, duration * 1000);

      return `Timer set: "${label}" for ${timeStr}. You'll get a notification and sound when it's done.`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error setting timer: ${msg}`;
    }
  },
};

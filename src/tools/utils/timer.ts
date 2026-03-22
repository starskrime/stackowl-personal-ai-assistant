import { exec } from "node:child_process";
import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";

// ─── Scheduled Message Queue ────────────────────────────────────
// Module-level queue so scheduled messages survive across tool calls.
// The gateway polls this via getScheduledMessages().

export interface ScheduledMessage {
  id: string;
  message: string;
  fireAt: number; // Unix timestamp (ms)
  channelId?: string;
  userId?: string;
  fired: boolean;
}

const scheduledMessages: ScheduledMessage[] = [];
let nextId = 1;

/**
 * Get all messages ready to fire. Called by the gateway's tick loop.
 * Marks returned messages as fired so they don't fire again.
 */
export function getReadyMessages(): ScheduledMessage[] {
  const now = Date.now();
  const ready: ScheduledMessage[] = [];

  for (const msg of scheduledMessages) {
    if (!msg.fired && now >= msg.fireAt) {
      msg.fired = true;
      ready.push(msg);
    }
  }

  // Cleanup: remove fired messages older than 5 minutes
  const cutoff = now - 5 * 60 * 1000;
  for (let i = scheduledMessages.length - 1; i >= 0; i--) {
    if (scheduledMessages[i].fired && scheduledMessages[i].fireAt < cutoff) {
      scheduledMessages.splice(i, 1);
    }
  }

  return ready;
}

/**
 * Get count of pending (unfired) scheduled messages.
 */
export function getPendingCount(): number {
  return scheduledMessages.filter(m => !m.fired).length;
}

// ─── Timer Tool ─────────────────────────────────────────────────

export const TimerTool: ToolImplementation = {
  definition: {
    name: "set_timer",
    description:
      "Schedule a delayed message to the user. After the specified duration, " +
      "the message will be sent back to the user through their current channel " +
      "(Telegram, CLI, etc). Use this when the user asks to be pinged, reminded, " +
      "or messaged after some time. Duration in seconds.",
    parameters: {
      type: "object",
      properties: {
        duration: {
          type: "number",
          description:
            "Delay in seconds before sending the message (e.g. 60 for 1 minute, 300 for 5 minutes)",
        },
        message: {
          type: "string",
          description:
            "The message to send to the user when the timer fires. Be friendly and include the context of what they asked for.",
        },
        label: {
          type: "string",
          description: "Short label for the timer (for logging)",
        },
      },
      required: ["duration", "message"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const duration = Number(args.duration);
      const message = String(args.message || "Timer is up!");
      const label = args.label ? String(args.label) : "Timer";

      if (!isFinite(duration) || duration <= 0) {
        return "Error: Duration must be a positive number of seconds.";
      }

      if (duration > 24 * 60 * 60) {
        return "Error: Maximum timer duration is 24 hours (86400 seconds).";
      }

      const minutes = Math.floor(duration / 60);
      const seconds = duration % 60;
      const timeStr =
        minutes > 0 ? `${minutes}m${seconds > 0 ? ` ${seconds}s` : ""}` : `${seconds}s`;

      const fireAt = Date.now() + duration * 1000;
      const fireTime = new Date(fireAt).toLocaleTimeString();

      // Schedule the message in the queue
      // channelId and userId are set by the gateway's delivery tick,
      // not here — the tool doesn't know what channel it's running on.
      const id = `timer-${nextId++}`;
      scheduledMessages.push({
        id,
        message,
        fireAt,
        fired: false,
      });

      log.engine.info(
        `[Timer] Scheduled "${label}" (${id}): "${message.slice(0, 50)}..." in ${timeStr} (fires at ${fireTime})`,
      );

      // Also set a macOS notification as backup
      setTimeout(() => {
        const escapedLabel = label.replace(/"/g, '\\"');
        exec(
          `osascript -e 'display notification "${escapedLabel}" with title "⏰ StackOwl Timer"'`,
          { timeout: 15000 },
          () => {},
        );
        exec("afplay /System/Library/Sounds/Glass.aiff", { timeout: 15000 }, () => {});
      }, duration * 1000);

      return (
        `Timer scheduled: "${label}" — I'll send you this message in ${timeStr} (at ${fireTime}):\n` +
        `"${message}"\n\n` +
        `The message will be delivered right here in this chat.`
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error setting timer: ${msg}`;
    }
  },
};

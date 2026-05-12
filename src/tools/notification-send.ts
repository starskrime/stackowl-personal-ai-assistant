import { log } from "../logger.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { Platform } from "../platform/index.js";

const MAX_PER_MINUTE = 10;
const WINDOW_MS = 60_000;

interface RateWindow {
  startedAt: number;
  count: number;
}

const buckets = new Map<string, RateWindow>();

function checkRate(sessionId: string): { allowed: boolean } {
  const now = Date.now();
  let w = buckets.get(sessionId);
  if (!w || now - w.startedAt >= WINDOW_MS) {
    w = { startedAt: now, count: 0 };
    buckets.set(sessionId, w);
  }
  if (w.count >= MAX_PER_MINUTE) return { allowed: false };
  w.count++;
  return { allowed: true };
}

export function createNotificationSendTool(platform: Platform): ToolImplementation {
  return {
    definition: {
      name: "notification_send",
      description:
        "Send a desktop/system notification to the user via the platform notifier (native if available, system log + event bus, else stderr). " +
        "Rate-limited to 10/minute per session to prevent spam. " +
        'Example: notification_send(title: "Build done", body: "yarn build finished in 3m12s")',
      parameters: {
        type: "object",
        properties: {
          title: { type: "string", description: "Notification title" },
          body: { type: "string", description: "Notification body text" },
          urgency: {
            type: "string",
            enum: ["low", "normal", "critical"],
            description: "Default normal",
          },
          category: {
            type: "string",
            description: 'Grouping hint (e.g. "build", "alert")',
          },
        },
        required: ["title", "body"],
      },
      capabilities: ["notify"],
      executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
    },
    category: "cognitive",
    source: "builtin",

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
      const title = args["title"] as string;
      const body = args["body"] as string;
      const urgency =
        ((args["urgency"] as "low" | "normal" | "critical" | undefined) ?? "normal");
      const category = args["category"] as string | undefined;
      const sessionId = context.engineContext?.sessionId ?? "default";

      log.tool.debug("notification_send.execute: entry", {
        title: title?.slice(0, 60),
        urgency,
      });

      if (!title || !body) {
        return JSON.stringify({
          success: false,
          error: {
            code: "MISSING_ARG",
            message: "title and body are required",
          },
        });
      }

      const { allowed } = checkRate(sessionId);
      if (!allowed) {
        return JSON.stringify({
          success: false,
          error: {
            code: "E_RATE_LIMITED",
            message: `Rate limit: max ${MAX_PER_MINUTE} notifications per minute per session.`,
            hint: "Wait or batch related notifications.",
          },
        });
      }

      const result = await platform.notifier.notify({ title, body, urgency, category });
      log.tool.debug("notification_send.execute: exit", { via: result.via });
      return JSON.stringify({
        success: true,
        data: { delivered: result.delivered, via: result.via },
      });
    },
  };
}

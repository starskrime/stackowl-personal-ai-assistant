import { log } from "../logger.js";
import type { CronJob } from "./types.js";
import type { ChatMessage, ModelProvider } from "../providers/base.js";

export interface IsolatedRunnerOptions {
  provider: ModelProvider;
}

/**
 * IsolatedRunner executes a cron job in a fresh, isolated provider session.
 * Each run starts with an empty message history to prevent contamination
 * from the main conversation or previous cron jobs.
 */
export class IsolatedRunner {
  constructor(private opts: IsolatedRunnerOptions) {}

  async run(job: CronJob, traceId: string): Promise<string> {
    log.engine.info("[IsolatedRunner] Starting isolated job run", {
      jobId: job.id,
      traceId,
      safetyProfile: job.safetyProfile,
    });

    // Fresh session — no contamination from main conversation
    const sessionMessages: ChatMessage[] = [
      {
        role: "system",
        content:
          `You are running a scheduled background task. ` +
          `Complete the following task autonomously and concisely.\n\n` +
          `Safety profile: ${job.safetyProfile}. Task ID: ${job.id}. Trace ID: ${traceId}`,
      },
      {
        role: "user",
        content: job.prompt,
      },
    ];

    try {
      const response = await this.opts.provider.chat(sessionMessages);
      const result =
        typeof response.content === "string"
          ? response.content
          : JSON.stringify(response.content);
      log.engine.info("[IsolatedRunner] Job completed", {
        jobId: job.id,
        traceId,
        resultLen: result.length,
      });
      return result;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.engine.error("[IsolatedRunner] Job failed", err as Error, {
        jobId: job.id,
        traceId,
      });
      return `[Cron job "${job.id}" failed] ${msg}`;
    }
  }
}

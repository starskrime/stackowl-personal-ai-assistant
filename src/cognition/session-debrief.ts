/**
 * StackOwl — Session Debrief
 *
 * At the natural end of a session, generates a structured 5-part debrief:
 *
 *   1. What we decided       — decisions made in this conversation
 *   2. What you learned      — new things the user discovered
 *   3. What you should do    — action items / next steps extracted
 *   4. What I learned        — new things the owl learned about the user
 *   5. One insight to share  — the owl's single most interesting observation
 *
 * Triggered by:
 *   - Explicit `endSession()` call (user quits)
 *   - BackgroundOrchestrator detecting N minutes of inactivity
 *
 * Delivered via the channel's onProactiveMessage callback.
 * Stored as a pellet tagged "session-debrief" for future recall.
 *
 * Architecture: pure generator. No state mutation. Called once at end of session.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface SessionDebrief {
  decided: string[];
  userLearned: string[];
  shouldDo: string[];
  owlLearned: string[];
  insight: string;
  /** Formatted markdown-ready string for delivery to user */
  formatted: string;
}

// ─── SessionDebriefGenerator ──────────────────────────────────────

export class SessionDebriefGenerator {
  private readonly TIMEOUT_MS = 15_000;
  /** Minimum messages before a debrief is worth generating */
  private readonly MIN_MESSAGES = 6;

  constructor(private provider: ModelProvider) {}

  /**
   * Generate a debrief from a completed session's message history.
   * Returns null if the session is too short to debrief.
   */
  async generate(
    messages: ChatMessage[],
    owlName: string,
  ): Promise<SessionDebrief | null> {
    const userMessages = messages.filter((m) => m.role === "user");
    if (userMessages.length < this.MIN_MESSAGES / 2) return null;

    const transcript = this.buildTranscript(messages);
    if (!transcript) return null;

    log.engine.info(`[SessionDebrief] Generating for ${owlName} (${userMessages.length} user turns)`);

    try {
      const raw = await Promise.race([
        this.callLLM(transcript, owlName),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error("debrief timeout")), this.TIMEOUT_MS),
        ),
      ]);

      const debrief = this.parse(raw);
      if (!debrief) return null;

      debrief.formatted = this.format(debrief, owlName);
      return debrief;
    } catch (err) {
      log.engine.warn(`[SessionDebrief] Failed: ${err instanceof Error ? err.message : err}`);
      return null;
    }
  }

  // ─── Private ─────────────────────────────────────────────────

  private buildTranscript(messages: ChatMessage[]): string {
    // Use last 30 messages max to keep prompt bounded
    const recent = messages.slice(-30);
    return recent
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => `${m.role === "user" ? "User" : "Owl"}: ${String(m.content).slice(0, 400)}`)
      .join("\n");
  }

  private async callLLM(transcript: string, owlName: string): Promise<string> {
    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          `You are ${owlName}, wrapping up a conversation. ` +
          `Analyze the session and extract a structured debrief. ` +
          `Be specific — use actual details from the conversation. ` +
          `Respond ONLY with valid JSON, no markdown fences.`,
      },
      {
        role: "user",
        content:
          `Session transcript:\n\n${transcript}\n\n` +
          `Generate a session debrief JSON with these exact keys:\n` +
          `{\n` +
          `  "decided": ["decision 1", "decision 2"],\n` +
          `  "userLearned": ["thing user learned 1"],\n` +
          `  "shouldDo": ["action item 1", "action item 2"],\n` +
          `  "owlLearned": ["what owl learned about the user"],\n` +
          `  "insight": "one interesting observation or pattern you noticed"\n` +
          `}\n\n` +
          `Rules:\n` +
          `- Max 3 items per array\n` +
          `- Each item is 1 concrete sentence\n` +
          `- "decided" = actual decisions made, not just discussed\n` +
          `- "owlLearned" = things about user's style/preferences/knowledge, not task facts\n` +
          `- "insight" = surprising or non-obvious observation, 1-2 sentences\n` +
          `- If nothing to put in a category, use empty array []`,
      },
    ];

    const response = await this.provider.chat(messages);
    return response.content.trim();
  }

  private parse(raw: string): SessionDebrief | null {
    try {
      const cleaned = raw
        .replace(/^```(?:json)?\s*/i, "")
        .replace(/\s*```$/, "")
        .trim();
      const parsed = JSON.parse(cleaned) as Partial<SessionDebrief>;

      return {
        decided: this.toStringArray(parsed.decided),
        userLearned: this.toStringArray(parsed.userLearned),
        shouldDo: this.toStringArray(parsed.shouldDo),
        owlLearned: this.toStringArray(parsed.owlLearned),
        insight: typeof parsed.insight === "string" ? parsed.insight : "",
        formatted: "",
      };
    } catch {
      return null;
    }
  }

  private toStringArray(val: unknown): string[] {
    if (!Array.isArray(val)) return [];
    return val.filter((v) => typeof v === "string" && v.length > 0).slice(0, 3);
  }

  private format(d: SessionDebrief, owlName: string): string {
    const sections: string[] = [`**Session Debrief** *(from ${owlName})*`];

    if (d.decided.length > 0) {
      sections.push(`\n**Decided**\n${d.decided.map((s) => `• ${s}`).join("\n")}`);
    }
    if (d.userLearned.length > 0) {
      sections.push(`\n**You learned**\n${d.userLearned.map((s) => `• ${s}`).join("\n")}`);
    }
    if (d.shouldDo.length > 0) {
      sections.push(`\n**Next steps**\n${d.shouldDo.map((s) => `☐ ${s}`).join("\n")}`);
    }
    if (d.owlLearned.length > 0) {
      sections.push(`\n**I noticed about you**\n${d.owlLearned.map((s) => `• ${s}`).join("\n")}`);
    }
    if (d.insight) {
      sections.push(`\n**One insight**\n${d.insight}`);
    }

    return sections.join("\n");
  }
}

/**
 * StackOwl — Reflexion Engine (Idle-Time Dreaming)
 *
 * Scans recent conversation history during idle time to find instances
 * where the agent struggled (e.g. tool execution errors). It runs a self-correction
 * prompt to analyze the failure and generate a "Behavioral Patch" (Pellet)
 * so it never makes the exact same mistake again.
 */

import type { ModelProvider } from "../providers/base.js";
import type { SessionStore } from "../memory/store.js";
import type { PelletStore } from "../pellets/store.js";
import { log } from "../logger.js";

export class ReflexionEngine {
  /**
   * Tracks which failures have already been analyzed this process lifetime.
   * Key format: "<sessionId>:<failureIndex>" — prevents generating duplicate
   * behavioral patches for the same failure across multiple dream() calls.
   */
  private processedFailures = new Set<string>();

  constructor(
    private provider: ModelProvider,
    private sessionStore: SessionStore,
    private pelletStore: PelletStore,
  ) {}

  /**
   * Trigger an idle-time reflection cycle.
   */
  async dream(): Promise<void> {
    try {
      log.evolution.info(
        "🧠 Initiating idle-time Reflection cycle (Dreaming)...",
      );
      const sessions = await this.sessionStore.listSessions();
      if (sessions.length === 0) return;

      // Look at the most recent session
      const recentSession = sessions[0];
      const messages = recentSession.messages;

      // Find an instance of a tool failure
      const failureIndex = messages.findIndex(
        (msg) =>
          msg.role === "tool" &&
          typeof msg.content === "string" &&
          (msg.content.includes("EXIT_CODE:") ||
            msg.content.includes("Error")) &&
          !msg.content.includes("EXIT_CODE: 0"),
      );

      if (failureIndex === -1) {
        log.evolution.info("   No significant failures found to reflect on.");
        return;
      }

      // Guard: skip if we already generated a patch for this exact failure.
      // Without this, every 15-minute tick re-analyzes the same failure in the
      // same session and saves identical behavioral patches to the pellet store.
      const failureKey = `${recentSession.id}:${failureIndex}`;
      if (this.processedFailures.has(failureKey)) {
        log.evolution.info(
          `   Failure already processed (${failureKey}) — skipping duplicate dream.`,
        );
        return;
      }

      // We found a failure! Let's get the context.
      // Go back to find the user request and the tool call that caused it.
      const startIdx = Math.max(0, failureIndex - 5);
      const contextMessages = messages.slice(startIdx, failureIndex + 2); // include the failure and the immediate reaction

      log.evolution.info("   Found a failure point. Analyzing...");

      const transcript = JSON.stringify(contextMessages, null, 2);

      const prompt = `You are the self-reflection module for an autonomous AI assistant.
Analyze the following transcript of a recent interaction where you (the assistant) encountered a tool execution error.

TRANSCRIPT:
${transcript}

Critique your past performance. Why did the tool fail? What was wrong with your approach or assumptions?
Most importantly, extract a single, concise Behavioral Heuristic or Rule that you must follow in the future to avoid this exact mistake.

Respond strictly in the following JSON format:
{
  "analysis": "Brief explanation of what went wrong",
  "heuristic": "The concrete rule you should follow next time (e.g., 'Always use absolute paths when calling the FileRead tool')"
}`;

      const response = await this.provider.chat(
        [{ role: "system", content: prompt }],
        undefined, // default model
        { temperature: 0.2 },
      );

      // Extract JSON
      const match = response.content.match(/\{[\s\S]*\}/);
      if (!match) {
        log.evolution.warn("   Failed to parse reflection output.");
        return;
      }

      const parsed = JSON.parse(match[0]);
      if (parsed.heuristic && parsed.heuristic.length > 5) {
        log.evolution.info(
          `   🧬 Behavioral Patch Generated: "${parsed.heuristic}"`,
        );

        await this.pelletStore.save({
          id: `patch_${Date.now()}`,
          title: `Reflection Patch: ${parsed.heuristic.slice(0, 30)}...`,
          generatedAt: new Date().toISOString(),
          source: "ReflexionEngine",
          owls: ["system"],
          tags: ["reflexion", "behavioral-patch", "rule"],
          content: parsed.heuristic,
          version: 1,
        });

        // Mark this failure as processed so future dream() calls don't re-analyze
        // the same event and produce duplicate patches.
        this.processedFailures.add(failureKey);

        // Bound the set so it doesn't grow unboundedly across many sessions
        if (this.processedFailures.size > 200) {
          const oldest = this.processedFailures.values().next().value;
          if (oldest) this.processedFailures.delete(oldest);
        }
      }

      // Also look for positive patterns — successful interactions worth repeating
      await this.extractPositivePatterns(messages, recentSession.id);
    } catch (error: any) {
      log.evolution.warn(`Dream cycle failed: ${error.message}`);
    }
  }

  /**
   * Detect successful interactions (user satisfaction signals) and
   * extract the approach as a positive behavioral pattern.
   */
  private async extractPositivePatterns(
    messages: any[],
    sessionId: string,
  ): Promise<void> {
    const SATISFACTION_SIGNALS = [
      "thanks",
      "thank you",
      "perfect",
      "great",
      "awesome",
      "exactly",
      "that works",
      "nice",
      "love it",
      "well done",
    ];

    // Find user messages that express satisfaction after an assistant response
    for (let i = 1; i < messages.length; i++) {
      if (messages[i].role !== "user") continue;
      const text = (messages[i].content ?? "").toLowerCase();

      const isSatisfied = SATISFACTION_SIGNALS.some((s) => text.includes(s));
      if (!isSatisfied) continue;

      // Get the preceding assistant response
      const prevAssistant = messages[i - 1];
      if (prevAssistant.role !== "assistant") continue;

      // Check if we already processed this
      const patternKey = `${sessionId}:positive:${i}`;
      if (this.processedFailures.has(patternKey)) continue;

      try {
        const response = await this.provider.chat(
          [
            {
              role: "system",
              content:
                `The user expressed satisfaction with the following assistant response. ` +
                `Extract a single, concise behavioral rule or heuristic that made this response successful. ` +
                `Respond in JSON: { "pattern": "The rule (e.g., 'When user asks X, always do Y')" }\n\n` +
                `Assistant response: ${(prevAssistant.content ?? "").slice(0, 500)}\n` +
                `User reaction: ${text.slice(0, 200)}`,
            },
          ],
          undefined,
          { temperature: 0.2 },
        );

        const match = response.content.match(/\{[\s\S]*\}/);
        if (match) {
          const parsed = JSON.parse(match[0]);
          if (parsed.pattern && parsed.pattern.length > 10) {
            await this.pelletStore.save({
              id: `pattern_${Date.now()}`,
              title: `Positive Pattern: ${parsed.pattern.slice(0, 40)}...`,
              generatedAt: new Date().toISOString(),
              source: "ReflexionEngine:positive",
              owls: ["system"],
              tags: ["reflexion", "positive-pattern", "best-practice"],
              content: parsed.pattern,
              version: 1,
            });
            log.evolution.info(`   🌟 Positive Pattern: "${parsed.pattern}"`);
          }
        }

        this.processedFailures.add(patternKey);
      } catch {
        // Non-critical — skip this pattern
      }
    }
  }
}

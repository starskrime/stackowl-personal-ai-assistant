/**
 * StackOwl — Learning Engine
 *
 * The central learning orchestrator with self-healing. Two modes:
 *
 *  1. REACTIVE  — called after every conversation. Extracts topics/gaps,
 *     immediately researches anything the owl was uncertain about,
 *     registers new domains for later deep study.
 *
 *  2. PROACTIVE — called during quiet hours (e.g., 2 AM). Picks the top
 *     topics from the study queue and researches them deeply so the owl
 *     is smarter for tomorrow's conversations.
 *
 * Self-healing: tracks consecutive failures, classifies error types,
 * retries with backoff, and falls back to degraded modes when the
 * full pipeline is broken. Never silently dies.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { PelletStore } from "../pellets/store.js";
import type { ProviderRegistry } from "../providers/registry.js";
import { ConversationExtractor } from "./extractor.js";
import { KnowledgeResearcher } from "./researcher.js";
import { KnowledgeGraphManager } from "./knowledge-graph.js";
import { SelfHealer } from "./self-healer.js";
import { join } from "node:path";
import { log } from "../logger.js";

export interface StudySessionResult {
  studied: string[];
  pelletsCreated: number;
  newFrontierTopics: string[];
}

// ─── Error Classification ────────────────────────────────────────

type ErrorClass = "timeout" | "rate_limit" | "parse" | "network" | "unknown";

function classifyError(err: unknown): ErrorClass {
  const msg =
    err instanceof Error
      ? err.message.toLowerCase()
      : String(err).toLowerCase();
  if (
    msg.includes("timeout") ||
    msg.includes("timed out") ||
    msg.includes("econnaborted")
  )
    return "timeout";
  if (
    msg.includes("rate") ||
    msg.includes("429") ||
    msg.includes("quota") ||
    msg.includes("too many")
  )
    return "rate_limit";
  if (
    msg.includes("json") ||
    msg.includes("parse") ||
    msg.includes("unexpected token")
  )
    return "parse";
  if (
    msg.includes("econnrefused") ||
    msg.includes("enotfound") ||
    msg.includes("fetch failed") ||
    msg.includes("network")
  )
    return "network";
  return "unknown";
}

// ─── Health Tracker ──────────────────────────────────────────────

interface HealthState {
  consecutiveFailures: number;
  lastFailure: string | null;
  lastFailureClass: ErrorClass | null;
  lastSuccess: string | null;
  totalAttempts: number;
  totalSuccesses: number;
  totalFailures: number;
}

// ─── Learning Engine ─────────────────────────────────────────────

export class LearningEngine {
  private extractor: ConversationExtractor;
  private graphManager: KnowledgeGraphManager;
  private selfHealer: SelfHealer | null = null;
  private health: HealthState = {
    consecutiveFailures: 0,
    lastFailure: null,
    lastFailureClass: null,
    lastSuccess: null,
    totalAttempts: 0,
    totalSuccesses: 0,
    totalFailures: 0,
  };

  /** Max consecutive failures before triggering self-healing */
  private static readonly MAX_CONSECUTIVE_FAILURES = 3;
  /** Backoff multiplier per consecutive failure (ms) */
  private static readonly BACKOFF_BASE_MS = 2000;

  constructor(
    private provider: ModelProvider,
    private owl: OwlInstance,
    private config: StackOwlConfig,
    private pelletStore: PelletStore,
    workspacePath: string,
    providerRegistry?: ProviderRegistry,
  ) {
    this.extractor = new ConversationExtractor(provider);
    this.graphManager = new KnowledgeGraphManager(workspacePath);

    // Initialize self-healer if Anthropic provider is available
    if (providerRegistry) {
      const projectRoot = join(workspacePath, "..");
      this.selfHealer = new SelfHealer(
        providerRegistry,
        projectRoot,
        workspacePath,
      );
    }
  }

  /**
   * REACTIVE LEARNING — call after each conversation ends.
   */
  async processConversation(messages: ChatMessage[]): Promise<void> {
    const userMessages = messages.filter((m) => m.role === "user");
    if (userMessages.length < 1) return;

    this.health.totalAttempts++;
    const startTime = Date.now();

    // If we've been failing a lot, add backoff delay
    if (this.health.consecutiveFailures > 0) {
      const backoff = Math.min(
        LearningEngine.BACKOFF_BASE_MS *
          Math.pow(2, this.health.consecutiveFailures - 1),
        30_000, // cap at 30s
      );
      log.evolution.info(
        `[Learning] Backoff: waiting ${backoff}ms after ${this.health.consecutiveFailures} consecutive failure(s) ` +
          `(last: ${this.health.lastFailureClass})`,
      );
      await new Promise((resolve) => setTimeout(resolve, backoff));
    }

    // Too many failures — trigger self-healing via Anthropic before falling back
    if (
      this.health.consecutiveFailures >= LearningEngine.MAX_CONSECUTIVE_FAILURES
    ) {
      if (this.selfHealer && this.health.lastFailureClass) {
        await this.triggerSelfHealing(messages, userMessages);
        return;
      }
      // No self-healer available — use degraded mode
      await this.processConversationDegraded(messages, userMessages);
      return;
    }

    let pelletsCreated = 0;

    try {
      await this.graphManager.load();

      log.evolution.evolve(
        `[Learning] Reactive learning started (${messages.length} messages, ${userMessages.length} from user)`,
      );

      // Step 1: Extract insights (LLM call — most likely to fail)
      const insights = await this.retryOnce(
        () => this.extractor.extract(messages),
        "extract insights",
      );

      const hasAnything =
        insights.domains.length > 0 ||
        insights.knowledgeGaps.length > 0 ||
        insights.topics.length > 0;

      if (!hasAnything) {
        log.evolution.info(
          `[Learning] No learning signals found (${Date.now() - startTime}ms) — conversation was routine`,
        );
        this.recordSuccess();
        return;
      }

      log.evolution.evolve(
        `[Learning] Signals: ${insights.domains.length} domains, ` +
          `${insights.knowledgeGaps.length} gaps, ` +
          `${insights.topics.length} topics`,
      );

      // Step 2: Register domains (no LLM — should never fail)
      for (const domain of insights.domains) {
        this.graphManager.touchDomain(domain, "conversation");
      }
      for (const topic of insights.topics) {
        this.graphManager.touchDomain(topic, "conversation");
      }

      // Step 3: Research knowledge gaps (LLM calls — may fail per-gap)
      // This is reactive — only runs after actual user conversations where
      // the assistant couldn't help. Not a proactive background loop.
      if (insights.knowledgeGaps.length > 0) {
        log.evolution.evolve(
          `[Learning] Researching ${Math.min(2, insights.knowledgeGaps.length)} knowledge gap(s)...`,
        );
        const researcher = new KnowledgeResearcher(
          this.provider,
          this.owl,
          this.config,
          this.pelletStore,
          this.graphManager,
        );

        const recentContext = messages
          .filter(
            (m: ChatMessage) => m.role === "user" || m.role === "assistant",
          )
          .slice(-6)
          .map((m: ChatMessage) => (m.content ?? "").slice(0, 200))
          .join(" ");

        for (const gap of insights.knowledgeGaps.slice(0, 2)) {
          try {
            const result = await researcher.research(gap, recentContext);
            pelletsCreated += result.pellets.length;
            log.evolution.evolve(
              `[Learning] Gap "${gap}" → ${result.pellets.length} pellet(s), ` +
                `${result.relatedTopics.length} frontier topics`,
            );
          } catch (err) {
            const errClass = classifyError(err);
            log.evolution.warn(
              `[Learning] Gap research failed for "${gap}" (${errClass}): ` +
                `${err instanceof Error ? err.message : err}`,
            );
            // Don't break the whole learning — save what we have
          }
        }
      }

      await this.graphManager.save();
      this.recordSuccess();

      const elapsed = Date.now() - startTime;
      const stats = this.graphManager.getStats();
      log.evolution.evolve(
        `[Learning] Complete in ${elapsed}ms — ${pelletsCreated} pellet(s) created | ` +
          `graph: ${stats.totalDomains} domains, queue: ${stats.studyQueueLength}`,
      );
    } catch (err) {
      const elapsed = Date.now() - startTime;
      const errClass = classifyError(err);
      this.recordFailure(errClass);
      log.evolution.warn(
        `[Learning] Reactive learning FAILED after ${elapsed}ms (${errClass}, ` +
          `streak: ${this.health.consecutiveFailures}/${LearningEngine.MAX_CONSECUTIVE_FAILURES}): ` +
          `${err instanceof Error ? err.message : err}`,
      );

      // If we just crossed the threshold, warn prominently
      if (
        this.health.consecutiveFailures ===
        LearningEngine.MAX_CONSECUTIVE_FAILURES
      ) {
        log.evolution.error(
          `[Learning] ⚠ DEGRADED MODE: ${LearningEngine.MAX_CONSECUTIVE_FAILURES} consecutive failures. ` +
            `Switching to lightweight learning (domain tracking only, no LLM). ` +
            `Last error class: ${errClass}. Will auto-recover when LLM calls succeed again.`,
        );
      }
    }
  }

  /**
   * DEGRADED MODE — when LLM calls keep failing, do what we can without them.
   *
   * Extracts topics from user messages using simple heuristics (no LLM),
   * registers them in the knowledge graph, and periodically tries to
   * recover by attempting a full learning cycle.
   */
  private async processConversationDegraded(
    messages: ChatMessage[],
    userMessages: ChatMessage[],
  ): Promise<void> {
    const startTime = Date.now();

    // Every 5th attempt in degraded mode, try a full recovery
    if (this.health.totalAttempts % 5 === 0) {
      log.evolution.info(
        "[Learning] Degraded mode: attempting recovery probe...",
      );
      try {
        // Lightweight probe — just try the extractor with minimal messages
        const probe = await this.extractor.extract(messages.slice(-4));
        if (probe.topics.length > 0 || probe.domains.length > 0) {
          log.evolution.evolve(
            "[Learning] Recovery probe succeeded! Exiting degraded mode.",
          );
          this.recordSuccess(); // Resets consecutiveFailures
          // Re-run full learning
          await this.processConversation(messages);
          return;
        }
      } catch {
        log.evolution.info(
          "[Learning] Recovery probe failed, staying in degraded mode.",
        );
      }
    }

    // Heuristic topic extraction (zero LLM cost)
    try {
      await this.graphManager.load();

      const extracted = this.extractTopicsHeuristic(userMessages);
      if (extracted.length > 0) {
        for (const topic of extracted) {
          this.graphManager.touchDomain(topic, "conversation");
        }
        await this.graphManager.save();

        log.evolution.info(
          `[Learning] Degraded: registered ${extracted.length} topic(s) heuristically ` +
            `[${extracted.join(", ")}] (${Date.now() - startTime}ms)`,
        );
      } else {
        log.evolution.info(
          `[Learning] Degraded: no topics extracted (${Date.now() - startTime}ms)`,
        );
      }
    } catch (err) {
      log.evolution.warn(
        `[Learning] Degraded mode also failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * SELF-HEALING — uses Anthropic to read the whole app, diagnose, and fix.
   *
   * When consecutive failures reach the threshold, instead of just degrading,
   * we ask Claude (via Anthropic API) to read the source code, understand the
   * error, and apply a fix. Then retry.
   */
  private async triggerSelfHealing(
    messages: ChatMessage[],
    userMessages: ChatMessage[],
  ): Promise<void> {
    if (!this.selfHealer) {
      await this.processConversationDegraded(messages, userMessages);
      return;
    }

    log.evolution.evolve(
      `[Learning] Self-healing triggered — ${this.health.consecutiveFailures} consecutive failures. ` +
        `Asking Anthropic (Claude) to diagnose and fix...`,
    );

    const lastError = new Error(
      `Learning subsystem failed ${this.health.consecutiveFailures} times consecutively. ` +
        `Last error class: ${this.health.lastFailureClass}. ` +
        `Last failure: ${this.health.lastFailure}`,
    );

    try {
      const result = await this.selfHealer.heal({
        subsystem: "learning",
        error: lastError,
        operation: "processConversation",
        context:
          `Health state: ${JSON.stringify(this.health)}\n` +
          `Owl: ${this.owl.persona.name}\n` +
          `Recent user message: ${userMessages
            .slice(-1)
            .map((m) => (m.content ?? "").slice(0, 200))
            .join("")}`,
      });

      if (result.healed) {
        log.evolution.evolve(
          `[Learning] Self-healing succeeded: ${result.action}. Retrying learning...`,
        );
        // Reset failure counter and retry
        this.recordSuccess();

        // Retry with the fixed state
        try {
          await this.processConversation(messages);
          log.evolution.evolve("[Learning] Post-healing retry succeeded!");
          return;
        } catch (retryErr) {
          log.evolution.warn(
            `[Learning] Post-healing retry still failed: ` +
              `${retryErr instanceof Error ? retryErr.message : String(retryErr)}`,
          );
        }
      } else {
        log.evolution.warn(
          `[Learning] Self-healing could not fix: ${result.diagnosis} — ${result.action}`,
        );
      }
    } catch (healErr) {
      log.evolution.error(
        `[Learning] Self-healing itself failed: ` +
          `${healErr instanceof Error ? healErr.message : String(healErr)}`,
      );
    }

    // Fall back to degraded mode
    await this.processConversationDegraded(messages, userMessages);
  }

  /**
   * Extract topics from user messages using simple heuristics.
   * No LLM required — uses capitalized phrases, domain keywords, etc.
   */
  private extractTopicsHeuristic(userMessages: ChatMessage[]): string[] {
    const topics = new Set<string>();

    for (const msg of userMessages) {
      const text = msg.content ?? "";

      // Capitalized phrases (2+ words): "Machine Learning", "Docker Compose"
      const capPhrases = text.match(/\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b/g);
      if (capPhrases) {
        for (const phrase of capPhrases) {
          if (phrase.length >= 5 && phrase.length <= 40) {
            topics.add(phrase.toLowerCase());
          }
        }
      }

      // Backtick terms
      const backticks = text.match(/`([^`]{2,30})`/g);
      if (backticks) {
        for (const bt of backticks) {
          topics.add(bt.replace(/`/g, "").toLowerCase());
        }
      }

      // Question-based topic extraction: "how to X", "what is X"
      const howTo = text.match(
        /how (?:to|do|can|should|does)\s+(.{5,40}?)(?:\?|$)/gi,
      );
      if (howTo) {
        for (const match of howTo) {
          const topic = match
            .replace(/^how (?:to|do|can|should|does)\s+/i, "")
            .replace(/\?$/, "")
            .trim();
          if (topic.length >= 3) topics.add(topic.toLowerCase());
        }
      }
    }

    return [...topics].slice(0, 6);
  }

  /**
   * PROACTIVE SELF-STUDY — call during quiet hours.
   */
  async runStudySession(maxTopics = 3): Promise<StudySessionResult> {
    await this.graphManager.load();

    const queue = this.graphManager.getStudyQueue(maxTopics);
    if (queue.length === 0) {
      log.evolution.evolve("Self-study: nothing in queue — owl is caught up.");
      return { studied: [], pelletsCreated: 0, newFrontierTopics: [] };
    }

    log.evolution.evolve(
      `Self-study session starting: ${queue.length} topic(s) — ${queue.join(", ")}`,
    );

    const researcher = new KnowledgeResearcher(
      this.provider,
      this.owl,
      this.config,
      this.pelletStore,
      this.graphManager,
    );

    const studied: string[] = [];
    let pelletsCreated = 0;
    const allNewFrontier: string[] = [];

    for (const topic of queue) {
      try {
        const result = await researcher.research(topic);
        studied.push(topic);
        pelletsCreated += result.pellets.length;
        allNewFrontier.push(...result.relatedTopics);
        log.evolution.evolve(
          `[Study] "${topic}" → ${result.pellets.length} pellet(s), ` +
            `${result.relatedTopics.length} frontier topics`,
        );
      } catch (err) {
        const errClass = classifyError(err);
        log.evolution.warn(
          `[Study] Failed for "${topic}" (${errClass}): ` +
            `${err instanceof Error ? err.message : err}`,
        );
        // If it's a rate limit, stop burning more tokens
        if (errClass === "rate_limit") {
          log.evolution.warn(
            "[Study] Rate limited — stopping study session early.",
          );
          break;
        }
      }
    }

    await this.graphManager.save();

    const newFrontierTopics = [...new Set(allNewFrontier)];

    log.evolution.evolve(
      `Self-study complete: ${studied.length}/${queue.length} topic(s) studied, ` +
        `${pelletsCreated} pellets created, ` +
        `${newFrontierTopics.length} new frontier topics discovered`,
    );

    return { studied, pelletsCreated, newFrontierTopics };
  }

  /**
   * Get a human-readable learning report — shown in /learning command.
   */
  async getLearningReport(): Promise<string> {
    await this.graphManager.load();
    const graphReport = this.graphManager.getFullReport();
    const healthReport = this.getHealthReport();
    return `${graphReport}\n\n${healthReport}`;
  }

  /**
   * One-line summary for status displays.
   */
  async getSummaryLine(): Promise<string> {
    await this.graphManager.load();
    const stats = this.graphManager.getStats();
    const summary = this.graphManager.getDomainSummary();
    const healthTag =
      this.health.consecutiveFailures >= LearningEngine.MAX_CONSECUTIVE_FAILURES
        ? " [DEGRADED]"
        : this.health.consecutiveFailures > 0
          ? ` [${this.health.consecutiveFailures} recent failure(s)]`
          : "";
    return (
      `${stats.totalDomains} domains known | ` +
      `avg depth ${Math.round(stats.avgDepth * 100)}% | ` +
      `${stats.studyQueueLength} queued${healthTag}\n` +
      `Top: ${summary}`
    );
  }

  /**
   * Health status for diagnostics.
   */
  getHealthReport(): string {
    const h = this.health;
    const successRate =
      h.totalAttempts > 0
        ? Math.round((h.totalSuccesses / h.totalAttempts) * 100)
        : 0;
    const mode =
      h.consecutiveFailures >= LearningEngine.MAX_CONSECUTIVE_FAILURES
        ? this.selfHealer
          ? "⚠ SELF-HEALING (Anthropic diagnosing)"
          : "⚠ DEGRADED (heuristic-only)"
        : "✓ Normal";

    const lines = [
      "### Learning Health",
      `Mode: ${mode}`,
      `Self-healer: ${this.selfHealer ? "✓ Active (Anthropic)" : "✗ Unavailable (no Anthropic provider)"}`,
      `Success rate: ${successRate}% (${h.totalSuccesses}/${h.totalAttempts})`,
      `Consecutive failures: ${h.consecutiveFailures}`,
      h.lastFailure
        ? `Last failure: ${h.lastFailure} (${h.lastFailureClass})`
        : "",
      h.lastSuccess ? `Last success: ${h.lastSuccess}` : "",
    ];

    // Include healing history
    if (this.selfHealer) {
      const history = this.selfHealer.getHistory();
      if (history.length > 0) {
        lines.push("", "### Self-Healing History");
        for (const entry of history.slice(-5)) {
          const icon = entry.success ? "✓" : "✗";
          lines.push(
            `  ${icon} [${entry.timestamp.slice(0, 19)}] ${entry.subsystem}: ${entry.diagnosis} → ${entry.action}`,
          );
        }
      }
    }

    return lines.filter(Boolean).join("\n");
  }

  // ─── Private Helpers ─────────────────────────────────────────

  private recordSuccess(): void {
    this.health.consecutiveFailures = 0;
    this.health.lastSuccess = new Date().toISOString();
    this.health.totalSuccesses++;
  }

  private recordFailure(errClass: ErrorClass): void {
    this.health.consecutiveFailures++;
    this.health.lastFailure = new Date().toISOString();
    this.health.lastFailureClass = errClass;
    this.health.totalFailures++;
  }

  /**
   * Retry a function once on failure with a short delay.
   * Only retries on transient errors (timeout, network, rate_limit).
   */
  private async retryOnce<T>(fn: () => Promise<T>, label: string): Promise<T> {
    try {
      return await fn();
    } catch (err) {
      const errClass = classifyError(err);
      if (errClass === "parse" || errClass === "unknown") {
        throw err; // Non-transient — don't retry
      }
      log.evolution.info(
        `[Learning] ${label} failed (${errClass}), retrying once after 1s...`,
      );
      await new Promise((resolve) => setTimeout(resolve, 1000));
      return await fn();
    }
  }
}

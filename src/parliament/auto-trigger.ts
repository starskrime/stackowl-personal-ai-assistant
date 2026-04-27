/**
 * StackOwl — Parliament Auto-Trigger
 *
 * Determines whether a user message warrants a Parliament debate.
 * Uses LLM-based detection from detector.ts with additional filtering
 * and config support.
 */

import type { ModelProvider } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import { shouldConveneParliament } from "./detector.js";
import { log } from "../logger.js";

// ─── Fast-Path Patterns ─────────────────────────────────────────

const GREETING_PATTERNS = /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|goodbye|good morning|good evening|gm|gn)\b/i;

const TRIVIAL_PATTERNS = [
  /^(hi|hello|hey|sup|yo)$/i,
  /^(thanks|thank you)$/i,
  /^(ok|okay|sure|yep|nope|cool)$/i,
  /^\?$/,
  /^.$/,
];

function isTrivialMessage(message: string): boolean {
  const trimmed = message.trim();

  // Length check — no LLM call needed for tiny messages
  if (trimmed.length < 25) {
    return true;
  }

  // Greeting check
  if (GREETING_PATTERNS.test(trimmed)) {
    return true;
  }

  // Other trivial patterns
  for (const pattern of TRIVIAL_PATTERNS) {
    if (pattern.test(trimmed)) {
      return true;
    }
  }

  return false;
}

// ─── AutoTrigger ───────────────────────────────────────────────

export interface AutoTriggerResult {
  shouldTrigger: boolean;
  reason: string;
  bypassed: boolean;
}

export class ParliamentAutoTrigger {
  constructor(
    private config: StackOwlConfig,
  ) {}

  /**
   * Check if a message should trigger a Parliament debate.
   * Returns true only if:
   * 1. Parliament is enabled in config
   * 2. Message is not trivial (passes fast-path)
   * 3. LLM-based detection says DEBATE
   */
  async check(
    message: string,
    provider: ModelProvider,
  ): Promise<AutoTriggerResult> {
    // ── Fast-Path Exit ──────────────────────────────────────────
    if (isTrivialMessage(message)) {
      log.engine.debug(
        `[ParliamentAutoTrigger] Fast-path exit for trivial message: "${message.slice(0, 40)}"`,
      );
      return {
        shouldTrigger: false,
        reason: "Trivial message (greeting/short)",
        bypassed: true,
      };
    }

    // ── Config Check ─────────────────────────────────────────────
    // Parliament config exists in StackOwlConfig with maxRounds/maxOwls
    // enabled and autoTriggerThreshold are optional extension fields
    if (!this.config.parliament) {
      log.engine.debug(
        `[ParliamentAutoTrigger] No parliament config found`,
      );
      return {
        shouldTrigger: false,
        reason: "No parliament config",
        bypassed: false,
      };
    }

    const parliamentEnabled = (this.config.parliament as Record<string, unknown>).enabled;
    if (parliamentEnabled === false) {
      log.engine.debug(
        `[ParliamentAutoTrigger] Parliament disabled in config`,
      );
      return {
        shouldTrigger: false,
        reason: "Parliament disabled in config",
        bypassed: false,
      };
    }

    // ── Check autoTriggerThreshold if defined ────────────────────
    const threshold = (this.config.parliament as Record<string, unknown>).autoTriggerThreshold as number | undefined;
    if (threshold !== undefined && threshold > 1.0) {
      log.engine.debug(
        `[ParliamentAutoTrigger] autoTriggerThreshold (${threshold}) > 1.0, skipping`,
      );
      return {
        shouldTrigger: false,
        reason: `autoTriggerThreshold too high (${threshold})`,
        bypassed: false,
      };
    }

    // ── LLM-Based Detection ───────────────────────────────────────
    try {
      const shouldConvene = await shouldConveneParliament(message, provider);

      log.engine.info(
        `[ParliamentAutoTrigger] "${message.slice(0, 60)}..." → ${shouldConvene ? "TRIGGER" : "skip"}`,
      );

      return {
        shouldTrigger: shouldConvene,
        reason: shouldConvene
          ? "LLM detected debate-worthy topic"
          : "LLM detected non-debatable topic",
        bypassed: false,
      };
    } catch (err) {
      log.engine.warn(
        `[ParliamentAutoTrigger] Detection failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return {
        shouldTrigger: false,
        reason: `Detection error: ${err instanceof Error ? err.message : String(err)}`,
        bypassed: false,
      };
    }
  }
}

/**
 * Convenience function for quick parity check.
 * Returns true if the message should be evaluated by Parliament.
 */
export async function shouldTriggerParliament(
  message: string,
  provider: ModelProvider,
  config: StackOwlConfig,
): Promise<boolean> {
  const trigger = new ParliamentAutoTrigger(config);
  const result = await trigger.check(message, provider);
  return result.shouldTrigger;
}
/**
 * StackOwl — Preference Enforcer
 *
 * Post-processes LLM output against user preferences.
 * Also captures explicit preference statements from user messages and
 * infers implicit signals from message patterns.
 *
 * Called AFTER the LLM responds, before the response is sent to the user.
 */

import { log } from "../logger.js";
import type { UserPreferenceModel } from "../preferences/model.js";

// ─── Explicit preference patterns ────────────────────────────────

const EXPLICIT_PATTERNS: Array<{ regex: RegExp; key: string; valueMapper: (m: RegExpMatchArray) => unknown }> = [
  { regex: /\b(?:i prefer|please be|always be|be more)\s+(\w+)/i,       key: "communication_style", valueMapper: m => m[1]!.toLowerCase() },
  { regex: /\bdon'?t use\s+(emojis?|emoji)/i,                           key: "emoji_usage",         valueMapper: () => false },
  { regex: /\buse\s+(emojis?|emoji)/i,                                  key: "emoji_usage",         valueMapper: () => true },
  { regex: /\b(?:stop|never|don'?t)\s+(?:add|use|include)\s+(.+?)(?:\.|$)/i, key: "stop_behavior",  valueMapper: m => m[1]!.toLowerCase().trim() },
  { regex: /\bless\s+(\w+)/i,                                           key: "reduce",              valueMapper: m => m[1]!.toLowerCase() },
  { regex: /\bmore\s+(\w+)/i,                                           key: "increase",            valueMapper: m => m[1]!.toLowerCase() },
  { regex: /\b(?:respond|answer|reply)\s+in\s+([\w ]+)/i,              key: "language",            valueMapper: m => m[1]!.toLowerCase().trim() },
  { regex: /\bshort(?:er)?\s+(?:answers?|responses?|replies?)/i,       key: "conciseness",         valueMapper: () => "concise" },
  { regex: /\blong(?:er)?\s+(?:answers?|responses?|replies?)/i,        key: "conciseness",         valueMapper: () => "verbose" },
];

// ─── Estimator ────────────────────────────────────────────────────

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 3.8);
}

// ─── PreferenceEnforcer ───────────────────────────────────────────

export class PreferenceEnforcer {

  /**
   * Enforce preferences on a completed LLM response.
   * Returns the (possibly modified) response string.
   */
  async enforceOnResponse(
    response: string,
    _userMessage: string,
    preferenceModel: UserPreferenceModel | undefined,
  ): Promise<string> {
    if (!preferenceModel) return response;

    try {
      const conciseness = preferenceModel.getWithConfidence("communication_style")
        ?? preferenceModel.getWithConfidence("conciseness");

      // Length enforcement: concise + high confidence + long response → trim
      if (conciseness && conciseness.confidence > 0.8) {
        const value = String(conciseness.value).toLowerCase();
        const isConcise = value === "concise" || value === "brief" || value === "short";
        const tokens = estimateTokens(response);

        if (isConcise && tokens > 800) {
          const trimmed = this.trimToConcise(response);
          log.engine.info(
            `[PreferenceEnforcer] Trimmed response ${tokens}→${estimateTokens(trimmed)} tokens (conciseness=${conciseness.confidence.toFixed(2)})`,
          );
          return trimmed;
        }
      }
    } catch (err) {
      log.engine.warn(`[PreferenceEnforcer] enforceOnResponse failed: ${err instanceof Error ? err.message : err}`);
    }

    return response;
  }

  /**
   * Scan user message for explicit preference declarations.
   * Writes them with confidence=0.95 immediately.
   */
  async captureExplicitPreferences(
    userMessage: string,
    preferenceModel: UserPreferenceModel | undefined,
  ): Promise<void> {
    if (!preferenceModel) return;

    try {
      for (const { regex, key, valueMapper } of EXPLICIT_PATTERNS) {
        const m = userMessage.match(regex);
        if (m) {
          const value = valueMapper(m);
          // recordSignal with a high-confidence explicit override
          preferenceModel.recordSignal(key, value);
          // Hit it a second time to push confidence higher immediately
          preferenceModel.recordSignal(key, value);
          log.engine.info(`[PreferenceEnforcer] Explicit preference captured: ${key}=${JSON.stringify(value)}`);
        }
      }

      await preferenceModel.save();
    } catch (err) {
      log.engine.warn(`[PreferenceEnforcer] captureExplicitPreferences failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  /**
   * Infer implicit signals from message + response patterns.
   */
  async inferImplicitSignals(
    userMessage: string,
    response: string,
    preferenceModel: UserPreferenceModel | undefined,
  ): Promise<void> {
    if (!preferenceModel) return;

    try {
      const words = userMessage.trim().split(/\s+/).length;

      // Short user messages suggest preference for concise replies
      if (words < 12) {
        preferenceModel.recordSignal("communication_style", "concise");
      } else if (words > 60) {
        preferenceModel.recordSignal("communication_style", "verbose");
      }

      // Code-heavy questions → user wants code examples
      if (/\bcode\b|\bfunction\b|\bimplement\b|\bwrite a\b/i.test(userMessage)) {
        preferenceModel.recordSignal("prefers_code_examples", true);
      }

      // "explain" / "why" → verbose explanations preferred
      if (/\bexplain\b|\bwhy\b|\bhow does\b|\bwhat is\b/i.test(userMessage)) {
        preferenceModel.recordSignal("prefers_explanations", true);
      }

      // Response was long and user followed up with more questions → verbose OK
      const responseLong = estimateTokens(response) > 600;
      const userEngaged = words > 10;
      if (responseLong && userEngaged) {
        preferenceModel.recordSignal("tolerates_long_responses", true);
      }

      await preferenceModel.save();
    } catch (err) {
      log.engine.warn(`[PreferenceEnforcer] inferImplicitSignals failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  // ─── Private ──────────────────────────────────────────────────

  /**
   * Trim a long response to the most essential part.
   * Cuts at the first natural break after 400 tokens worth of content.
   */
  private trimToConcise(text: string): string {
    const targetChars = 400 * 3.8; // ~400 tokens
    if (text.length <= targetChars) return text;

    // Try paragraph break
    const paragraphs = text.split(/\n\n+/);
    let result = "";
    for (const p of paragraphs) {
      if ((result + p).length > targetChars) break;
      result += (result ? "\n\n" : "") + p;
    }

    if (result.length > 200) {
      return result + "\n\n*(Response shortened per your conciseness preference.)*";
    }

    // Fallback: cut at sentence boundary
    const cut = text.slice(0, Math.floor(targetChars));
    const lastPeriod = cut.lastIndexOf(". ");
    return (lastPeriod > 100 ? cut.slice(0, lastPeriod + 1) : cut) +
      "\n\n*(Response shortened.)*";
  }
}

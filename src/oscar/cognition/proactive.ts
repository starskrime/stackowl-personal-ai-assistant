import type { ModelProvider, ChatMessage } from "../../providers/base.js";
import type { Suggestion } from "./types.js";
import { log } from "../../logger.js";

export interface SuggestionContext {
  currentApp: string | null;
  currentElement?: string;
  recentActions: string[];
  timeOfDay: number;
  dayOfWeek: number;
}

const CACHE_TTL_MS = 5 * 60 * 1000;

function contextKey(ctx: SuggestionContext): string {
  return `${ctx.currentApp}|${ctx.timeOfDay}|${ctx.recentActions.slice(0, 3).join(",")}`;
}

export class ProactiveAssistant {
  private cache = new Map<string, { suggestion: Suggestion; expiresAt: number }>();
  private suggestionHistory: Map<string, Suggestion> = new Map();
  private maxHistoryAge = 24 * 60 * 60 * 1000;

  constructor(private provider?: ModelProvider) {}

  async suggest(context: SuggestionContext): Promise<Suggestion[]> {
    log.cognition.debug("proactive.suggest: entry", {
      app: context.currentApp,
      timeOfDay: context.timeOfDay,
      actions: context.recentActions.slice(0, 3),
    });

    if (!this.provider) {
      log.cognition.debug("proactive.suggest: no provider, returning empty");
      return [];
    }

    const key = contextKey(context);
    const cached = this.cache.get(key);
    if (cached && Date.now() < cached.expiresAt) {
      log.cognition.debug("proactive.suggest: cache hit, returning cached suggestion", { key });
      return [cached.suggestion];
    }

    const appDesc = context.currentApp ?? "unknown application";
    const timeLabel =
      context.timeOfDay < 12 ? "morning" : context.timeOfDay < 17 ? "afternoon" : "evening";

    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          "You are a proactive AI assistant. Given the user's current context, " +
          "suggest ONE brief, actionable insight (1 sentence, under 20 words). " +
          "Be specific and useful. Output only the suggestion text — no preamble.",
      },
      {
        role: "user",
        content:
          `App: ${appDesc}. Time: ${timeLabel}. ` +
          `Recent actions: ${context.recentActions.slice(0, 3).join(", ") || "none"}.`,
      },
    ];

    try {
      log.cognition.debug("proactive.suggest: calling provider.chat", { appDesc, timeLabel });
      const response = await this.provider.chat(messages);
      const message = response.content.trim();

      if (!message || message.length < 5) {
        log.cognition.debug("proactive.suggest: provider returned empty/too-short content");
        return [];
      }

      const suggestion: Suggestion = {
        id: `proactive_${Date.now()}`,
        type: "proactive",
        message,
        confidence: 0.75,
        context: { app: context.currentApp ?? undefined, timeOfDay: context.timeOfDay },
        createdAt: Date.now(),
      };

      this.cache.set(key, { suggestion, expiresAt: Date.now() + CACHE_TTL_MS });
      log.cognition.debug("proactive.suggest: exit", { message, cached: true });
      return [suggestion];
    } catch (err) {
      log.cognition.error("proactive.suggest: provider call failed", err as Error, {
        app: context.currentApp,
      });
      return [];
    }
  }

  recordSuggestion(suggestion: Suggestion): void {
    this.suggestionHistory.set(suggestion.id, suggestion);
  }

  recordSuggestionResponse(suggestionId: string, accepted: boolean): void {
    const suggestion = this.suggestionHistory.get(suggestionId);
    if (suggestion) {
      suggestion.confidence = accepted
        ? Math.min(1, suggestion.confidence + 0.1)
        : Math.max(0.1, suggestion.confidence - 0.2);
    }

    // Also update confidence in the cache if present
    for (const [, cached] of this.cache) {
      if (cached.suggestion.id === suggestionId) {
        cached.suggestion.confidence = accepted
          ? Math.min(1, cached.suggestion.confidence + 0.1)
          : Math.max(0.1, cached.suggestion.confidence - 0.2);
        break;
      }
    }
  }

  private pruneHistory(): void {
    const cutoff = Date.now() - this.maxHistoryAge;
    for (const [id, suggestion] of this.suggestionHistory) {
      if (suggestion.createdAt < cutoff) {
        this.suggestionHistory.delete(id);
      }
    }
  }

  getSuggestionStats(): {
    total: number;
    byType: Record<string, number>;
    avgConfidence: number;
  } {
    this.pruneHistory();
    const suggestions = Array.from(this.suggestionHistory.values());
    const byType: Record<string, number> = {};
    let totalConfidence = 0;

    for (const s of suggestions) {
      byType[s.type] = (byType[s.type] || 0) + 1;
      totalConfidence += s.confidence;
    }

    return {
      total: suggestions.length,
      byType,
      avgConfidence: suggestions.length > 0 ? totalConfidence / suggestions.length : 0,
    };
  }
}

export const proactiveAssistant = new ProactiveAssistant();

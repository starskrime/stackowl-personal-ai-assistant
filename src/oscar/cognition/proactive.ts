import type { Suggestion } from "./types.js";

export interface SuggestionContext {
  currentApp: string | null;
  currentElement?: string;
  recentActions: string[];
  timeOfDay: number;
  dayOfWeek: number;
}

export class ProactiveAssistant {
  private suggestionHistory: Map<string, Suggestion> = new Map();
  private maxHistoryAge = 24 * 60 * 60 * 1000;

  async suggest(context: SuggestionContext): Promise<Suggestion[]> {
    const suggestions: Suggestion[] = [];

    const habitual = await this.findHabitualSuggestions(context);
    suggestions.push(...habitual);

    const contextual = await this.findContextualSuggestions(context);
    suggestions.push(...contextual);

    const preventive = await this.findPreventiveSuggestions(context);
    suggestions.push(...preventive);

    const proactive = await this.findProactiveSuggestions(context);
    suggestions.push(...proactive);

    const filtered = suggestions
      .filter((s) => s.confidence > 0.6)
      .sort((a, b) => b.confidence - a.confidence)
      .slice(0, 5);

    this.pruneHistory();

    return filtered;
  }

  private async findHabitualSuggestions(context: SuggestionContext): Promise<Suggestion[]> {
    const suggestions: Suggestion[] = [];

    const hour = context.timeOfDay;

    if (hour === 9 && context.currentApp === null) {
      suggestions.push({
        id: `habitual_morning_${Date.now()}`,
        type: "habitual",
        message: "Good morning! Ready to start your daily routine?",
        confidence: 0.85,
        context: { timeOfDay: hour },
        createdAt: Date.now(),
      });
    }

    if (hour === 17 && context.currentApp === "email") {
      suggestions.push({
        id: `habitual_evening_${Date.now()}`,
        type: "habitual",
        message: "End of day approaching. Wrap up emails and prepare for tomorrow?",
        confidence: 0.75,
        context: { timeOfDay: hour, app: "email" },
        createdAt: Date.now(),
      });
    }

    return suggestions;
  }

  private async findContextualSuggestions(context: SuggestionContext): Promise<Suggestion[]> {
    const suggestions: Suggestion[] = [];

    if (context.currentApp === "photoshop" && context.recentActions.includes("click")) {
      suggestions.push({
        id: `context_save_${Date.now()}`,
        type: "contextual",
        message: "You've been editing. Would you like me to create a checkpoint?",
        confidence: 0.7,
        action: { type: "checkpoint.create" },
        context: { app: "photoshop" },
        createdAt: Date.now(),
      });
    }

    if (context.currentApp === "browser" && context.recentActions.includes("navigate")) {
      suggestions.push({
        id: `context_bookmark_${Date.now()}`,
        type: "contextual",
        message: "I notice you've been browsing. Want me to bookmark this page?",
        confidence: 0.5,
        action: { type: "bookmark.create" },
        context: { app: "browser" },
        createdAt: Date.now(),
      });
    }

    return suggestions;
  }

  private async findPreventiveSuggestions(context: SuggestionContext): Promise<Suggestion[]> {
    const suggestions: Suggestion[] = [];

    if (context.recentActions.includes("delete") && context.recentActions.includes("delete")) {
      suggestions.push({
        id: `preventive_backup_${Date.now()}`,
        type: "preventive",
        message: "I see multiple delete actions. Would you like me to create a backup first?",
        confidence: 0.8,
        action: { type: "backup.create" },
        context: {},
        createdAt: Date.now(),
      });
    }

    if (context.currentApp === "excel" && context.recentActions.includes("type")) {
      suggestions.push({
        id: `preventive_save_${Date.now()}`,
        type: "preventive",
        message: "You've been typing in Excel. Save your work to prevent data loss?",
        confidence: 0.75,
        action: { type: "save" },
        context: { app: "excel" },
        createdAt: Date.now(),
      });
    }

    return suggestions;
  }

  private async findProactiveSuggestions(context: SuggestionContext): Promise<Suggestion[]> {
    const suggestions: Suggestion[] = [];

    const recentSameContext = this.findRecentSuggestions(context);
    if (recentSameContext.length > 0) {
      const last = recentSameContext[0];
      if (last.type === "learning") {
        suggestions.push({
          id: `proactive_repeat_${Date.now()}`,
          type: "proactive",
          message: "Would you like to repeat that action I just learned?",
          confidence: 0.65,
          context: {},
          createdAt: Date.now(),
        });
      }
    }

    return suggestions;
  }

  private findRecentSuggestions(_context: SuggestionContext): Suggestion[] {
    const now = Date.now();
    return Array.from(this.suggestionHistory.values())
      .filter((s) => now - s.createdAt < 5 * 60 * 1000)
      .filter((s) => s.type === "learning");
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

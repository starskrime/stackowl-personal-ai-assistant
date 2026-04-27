/**
 * StackOwl — Truncation Alerter
 *
 * Detects when context has been truncated and generates alerts
 * to inform users of potential gaps in conversation context.
 */

import { log } from "../logger.js";

export type TruncationSeverity = "none" | "partial" | "significant" | "severe";

export interface TruncationEvent {
  timestamp: number;
  severity: TruncationSeverity;
  removedCount: number;
  removedTokens: number;
  originalCount: number;
  originalTokens: number;
  contentTypes: string[];
  affectedCategories: string[];
}

export interface TruncationAlert {
  message: string;
  severity: TruncationSeverity;
  details: {
    removedMessages: number;
    removedTokens: number;
    contentTypes: string[];
    suggestion: string;
  };
  recoveryHint?: string;
}

const SEVERITY_THRESHOLDS = {
  partial: 0.1,
  significant: 0.3,
  severe: 0.5,
};

export class TruncationAlerter {
  private recentAlerts: TruncationEvent[] = [];
  private maxAlerts = 10;

  constructor() {}

  /**
   * Record a truncation event
   */
  recordTruncation(
    removedCount: number,
    removedTokens: number,
    originalCount: number,
    originalTokens: number,
    contentTypes: string[] = ["message"],
  ): TruncationEvent {
    const removedRatio = removedCount / Math.max(originalCount, 1);
    const removedTokenRatio = removedTokens / Math.max(originalTokens, 1);

    const severity = this.calculateSeverity(
      removedRatio,
      removedTokenRatio,
    );

    const event: TruncationEvent = {
      timestamp: Date.now(),
      severity,
      removedCount,
      removedTokens,
      originalCount,
      originalTokens,
      contentTypes,
      affectedCategories: this.categorizeContent(contentTypes),
    };

    this.recentAlerts.push(event);
    if (this.recentAlerts.length > this.maxAlerts) {
      this.recentAlerts.shift();
    }

    log.engine.debug(
      `[TruncationAlerter] Recorded ${severity} truncation: ${removedCount} messages, ${removedTokens} tokens`,
    );

    return event;
  }

  /**
   * Generate an alert for a truncation event
   */
  generateAlert(event: TruncationEvent): TruncationAlert | null {
    if (event.severity === "none") return null;

    const message = this.formatAlertMessage(event);
    const suggestion = this.generateSuggestion(event);

    return {
      message,
      severity: event.severity,
      details: {
        removedMessages: event.removedCount,
        removedTokens: event.removedTokens,
        contentTypes: event.contentTypes,
        suggestion,
      },
      recoveryHint: this.generateRecoveryHint(event),
    };
  }

  /**
   * Check if we should warn user about recent truncation
   */
  shouldWarnUser(): boolean {
    if (this.recentAlerts.length === 0) return false;

    const recent = this.recentAlerts[this.recentAlerts.length - 1];
    const timeSinceLastAlert = Date.now() - recent.timestamp;

    return (
      recent.severity !== "none" &&
      recent.severity !== "partial" &&
      timeSinceLastAlert < 60000
    );
  }

  /**
   * Get recent alerts
   */
  getRecentAlerts(limit = 5): TruncationEvent[] {
    return this.recentAlerts.slice(-limit);
  }

  /**
   * Get the most severe recent alert
   */
  getMostSevereRecentAlert(): TruncationEvent | null {
    if (this.recentAlerts.length === 0) return null;

    return this.recentAlerts.reduce((most, current) =>
      this.severityRank(current.severity) > this.severityRank(most.severity)
        ? current
        : most,
    );
  }

  /**
   * Build an alert string for system prompt injection
   */
  buildSystemPromptAlert(): string {
    const recent = this.getRecentAlerts(3);
    if (recent.length === 0) return "";

    const significant = recent.filter(
      (e) => e.severity === "significant" || e.severity === "severe",
    );
    if (significant.length === 0) return "";

    const mostSevere = significant.reduce((a, b) =>
      this.severityRank(a.severity) > this.severityRank(b.severity) ? a : b,
    );

    const alert = this.generateAlert(mostSevere);
    if (!alert) return "";

    return `\n\n[CONTEXT WARNING] ${alert.message}\n`;
  }

  /**
   * Calculate severity based on removal ratios
   */
  private calculateSeverity(
    removedCountRatio: number,
    removedTokenRatio: number,
  ): TruncationSeverity {
    const maxRatio = Math.max(removedCountRatio, removedTokenRatio);

    if (maxRatio >= SEVERITY_THRESHOLDS.severe) return "severe";
    if (maxRatio >= SEVERITY_THRESHOLDS.significant) return "significant";
    if (maxRatio >= SEVERITY_THRESHOLDS.partial) return "partial";
    return "none";
  }

  /**
   * Rank severity levels numerically
   */
  private severityRank(severity: TruncationSeverity): number {
    switch (severity) {
      case "severe":
        return 4;
      case "significant":
        return 3;
      case "partial":
        return 2;
      case "none":
        return 0;
    }
  }

  /**
   * Categorize content types
   */
  private categorizeContent(contentTypes: string[]): string[] {
    const categories: string[] = [];

    for (const type of contentTypes) {
      if (type.includes("preference")) categories.push("preferences");
      if (type.includes("fact")) categories.push("facts");
      if (type.includes("episode")) categories.push("past discussions");
      if (type.includes("message")) categories.push("recent messages");
    }

    if (categories.length === 0) categories.push("context");

    return [...new Set(categories)];
  }

  /**
   * Format alert message
   */
  private formatAlertMessage(event: TruncationEvent): string {
    switch (event.severity) {
      case "severe":
        return `Context severely truncated - ${event.removedCount} messages removed. Some earlier context may be missing.`;
      case "significant":
        return `Context truncated - ${event.removedCount} earlier messages removed due to length limits.`;
      case "partial":
        return `Some earlier context removed to fit within limits.`;
      default:
        return "";
    }
  }

  /**
   * Generate suggestion based on truncation type
   */
  private generateSuggestion(event: TruncationEvent): string {
    if (event.affectedCategories.includes("past discussions")) {
      return "If referring to something we discussed earlier, please remind me of the key points.";
    }
    if (event.affectedCategories.includes("preferences")) {
      return "Let me know if I've missed any important preferences or requirements.";
    }
    return "Feel free to repeat any important information if needed.";
  }

  /**
   * Generate recovery hint for user
   */
  private generateRecoveryHint(event: TruncationEvent): string {
    const hints: string[] = [];

    hints.push("You can ask me to 'look back' or 'check earlier' to restore context.");

    if (event.affectedCategories.includes("past discussions")) {
      hints.push("If this relates to a previous conversation, please summarize what was discussed.");
    }

    if (event.affectedCategories.includes("preferences")) {
      hints.push("Please restate any important preferences that may have been truncated.");
    }

    return hints.join(" ");
  }

  /**
   * Clear recent alerts
   */
  clearAlerts(): void {
    this.recentAlerts = [];
  }
}

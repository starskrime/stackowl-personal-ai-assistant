import type { AnomalyAlert, AnomalyRule, ObservationContext } from "./types.js";
import type { CanonicalAction } from "../types.js";

export interface AnomalyConfig {
  enableRuleBased: boolean;
  enableSequenceBased: boolean;
  enableVisualBased: boolean;
  criticalThreshold: number;
  warningThreshold: number;
}

export class AnomalyDetector {
  private config: Required<AnomalyConfig>;
  private rules: AnomalyRule[] = [];
  private alertHistory: AnomalyAlert[] = [];
  private sequenceHistory: CanonicalAction[][] = [];
  private maxSequenceLength = 20;
  private maxAlertHistory = 100;

  constructor(config: Partial<AnomalyConfig> = {}) {
    this.config = {
      enableRuleBased: config.enableRuleBased ?? true,
      enableSequenceBased: config.enableSequenceBased ?? true,
      enableVisualBased: config.enableVisualBased ?? false,
      criticalThreshold: config.criticalThreshold ?? 0.9,
      warningThreshold: config.warningThreshold ?? 0.7,
    };

    this.initializeRules();
  }

  private initializeRules(): void {
    this.rules = [
      {
        name: "delete_many_files",
        check: (ctx) => {
          if (!ctx.action) return false;
          const params = ctx.action.params as { count?: number; paths?: string[] };
          return ctx.action.type === "invoke" && (params?.count ?? 0) > 10;
        },
        severity: "critical",
        message: "Attempting to delete many files at once",
      },
      {
        name: "system_level_change",
        check: (ctx) => {
          if (!ctx.action || !ctx.currentApp) return false;
          const systemApps = ["System Preferences", "System Settings", "Terminal"];
          return systemApps.includes(ctx.currentApp) && ctx.action.type !== "observe";
        },
        severity: "critical",
        message: "Performing system-level changes",
      },
      {
        name: "unusual_app",
        check: (ctx) => {
          if (!ctx.currentApp) return false;
          const unknownApps = ["Unknown", "Unknown Application"];
          return unknownApps.includes(ctx.currentApp);
        },
        severity: "warning",
        message: "Action on unknown application",
      },
      {
        name: "rapid_clicks",
        check: (ctx) => {
          if (!ctx.action || ctx.action.type !== "click") return false;
          const recentClicks = ctx.recentActions.filter((a) => a.type === "click").length;
          return recentClicks > 10;
        },
        severity: "warning",
        message: "Rapid clicking detected - possible automation loop",
      },
      {
        name: "unauthenticated_action",
        check: (ctx) => {
          if (!ctx.action) return false;
          const authRequired = ["bank", "payment", "password", "login"];
          const targetLabel = ctx.action.target?.semanticSelector?.label?.toLowerCase() || "";
          return authRequired.some((term) => targetLabel.includes(term));
        },
        severity: "warning",
        message: "Action may require authentication",
      },
      {
        name: "sensitive_data_access",
        check: (ctx) => {
          if (!ctx.action) return false;
          const sensitiveKeywords = ["password", "credit card", "ssn", "secret"];
          const paramsStr = JSON.stringify(ctx.action.params).toLowerCase();
          return sensitiveKeywords.some((kw) => paramsStr.includes(kw));
        },
        severity: "critical",
        message: "Accessing sensitive data",
      },
    ];
  }

  async detect(context: ObservationContext): Promise<AnomalyAlert[]> {
    const alerts: AnomalyAlert[] = [];

    if (this.config.enableRuleBased) {
      const ruleAlerts = this.detectRuleViolations(context);
      alerts.push(...ruleAlerts);
    }

    if (this.config.enableSequenceBased) {
      const sequenceAlerts = await this.detectSequenceAnomalies(context);
      alerts.push(...sequenceAlerts);
    }

    for (const alert of alerts) {
      this.alertHistory.push(alert);
    }

    if (this.alertHistory.length > this.maxAlertHistory) {
      this.alertHistory = this.alertHistory.slice(-this.maxAlertHistory);
    }

    if (context.action) {
      this.recordAction(context.action);
    }

    return alerts;
  }

  private detectRuleViolations(context: ObservationContext): AnomalyAlert[] {
    const alerts: AnomalyAlert[] = [];

    for (const rule of this.rules) {
      if (rule.check(context)) {
        alerts.push({
          id: `alert_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
          severity: rule.severity,
          type: "rule",
          message: rule.message,
          details: { rule: rule.name },
          timestamp: Date.now(),
          acknowledged: false,
        });
      }
    }

    return alerts;
  }

  private async detectSequenceAnomalies(context: ObservationContext): Promise<AnomalyAlert[]> {
    const alerts: AnomalyAlert[] = [];

    if (this.sequenceHistory.length < 5) return alerts;

    const lastSequences = this.sequenceHistory.slice(-10);
    const repeatingCount = this.countRepeatingSequences(lastSequences);

    if (repeatingCount > 3) {
      alerts.push({
        id: `alert_seq_${Date.now()}`,
        severity: "warning",
        type: "sequence",
        message: "Repetitive action sequence detected",
        details: { repeatingCount },
        timestamp: Date.now(),
        acknowledged: false,
      });
    }

    const unusualLength = this.detectUnusualSequenceLength(context.recentActions);
    if (unusualLength) {
      alerts.push({
        id: `alert_len_${Date.now()}`,
        severity: "warning",
        type: "sequence",
        message: `Unusually long action sequence (${unusualLength} actions)`,
        details: { length: unusualLength },
        timestamp: Date.now(),
        acknowledged: false,
      });
    }

    return alerts;
  }

  private countRepeatingSequences(sequences: CanonicalAction[][]): number {
    const sequenceSignatures = sequences.map((seq) =>
      seq.map((a) => a.type).join(",")
    );

    const counts = new Map<string, number>();
    for (const sig of sequenceSignatures) {
      counts.set(sig, (counts.get(sig) || 0) + 1);
    }

    return Math.max(...Array.from(counts.values()), 0);
  }

  private detectUnusualSequenceLength(actions: CanonicalAction[]): number | null {
    if (actions.length > 15) {
      return actions.length;
    }
    return null;
  }

  private recordAction(action: CanonicalAction): void {
    this.sequenceHistory.push([...this.sequenceHistory, action].flat());

    if (this.sequenceHistory.length > this.maxSequenceLength * 2) {
      this.sequenceHistory = this.sequenceHistory.slice(-this.maxSequenceLength);
    }
  }

  acknowledgeAlert(alertId: string): boolean {
    const alert = this.alertHistory.find((a) => a.id === alertId);
    if (alert) {
      alert.acknowledged = true;
      return true;
    }
    return false;
  }

  getAlerts(includeAcknowledged = false, limit?: number): AnomalyAlert[] {
    let alerts = includeAcknowledged
      ? this.alertHistory
      : this.alertHistory.filter((a) => !a.acknowledged);

    alerts = alerts.sort((a, b) => b.timestamp - a.timestamp);

    return limit ? alerts.slice(0, limit) : alerts;
  }

  getAlertStats(): {
    total: number;
    bySeverity: Record<string, number>;
    byType: Record<string, number>;
    unacknowledged: number;
  } {
    const stats = {
      total: this.alertHistory.length,
      bySeverity: { critical: 0, warning: 0, info: 0 },
      byType: { rule: 0, sequence: 0, visual: 0 },
      unacknowledged: 0,
    };

    for (const alert of this.alertHistory) {
      stats.bySeverity[alert.severity]++;
      stats.byType[alert.type]++;
      if (!alert.acknowledged) stats.unacknowledged++;
    }

    return stats;
  }

  clearHistory(): void {
    this.alertHistory = [];
    this.sequenceHistory = [];
  }

  addRule(rule: AnomalyRule): void {
    this.rules.push(rule);
  }

  removeRule(name: string): boolean {
    const index = this.rules.findIndex((r) => r.name === name);
    if (index !== -1) {
      this.rules.splice(index, 1);
      return true;
    }
    return false;
  }
}

export const anomalyDetector = new AnomalyDetector();

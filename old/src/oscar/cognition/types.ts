import type { CanonicalAction, UIElement } from "../types.js";

export interface Observation {
  timestamp: number;
  app: string | null;
  focusedElement: string | null;
  screenChanged: boolean;
  elements: UIElement[];
  cursorPosition: { x: number; y: number };
  recentActions: CanonicalAction[];
  timeOfDay: number;
}

export interface Reflection {
  timestamp: number;
  recentEpisodes: Episode[];
  patterns: Pattern[];
  anomalies: Improvement[];
  improvements: Improvement[];
}

export interface Episode {
  id: string;
  timestamp: number;
  actions: CanonicalAction[];
  outcome: "success" | "partial" | "failed";
  app: string;
  error?: string;
  duration?: number;
}

export interface Pattern {
  id: string;
  type: "habitual" | "contextual" | "sequential";
  trigger: PatternTrigger;
  action: string;
  confidence: number;
  occurrences: number;
  lastSeen: number;
}

export interface PatternTrigger {
  type: "time" | "app" | "element" | "sequence";
  timeOfDay?: number;
  app?: string;
  elementLabel?: string;
  precedingActions?: string[];
}

export interface AnomalyAlert {
  id: string;
  severity: "critical" | "warning" | "info";
  type: "rule" | "sequence" | "visual";
  message: string;
  details?: unknown;
  timestamp: number;
  acknowledged: boolean;
}

export interface Improvement {
  id: string;
  type: "affordance" | "skill" | "precondition" | "alternative";
  description: string;
  confidence: number;
  source: "reflection" | "failure" | "user";
  createdAt: number;
  applied: boolean;
}

export interface Suggestion {
  id: string;
  type: "habitual" | "contextual" | "preventive" | "proactive" | "learning";
  message: string;
  confidence: number;
  action?: {
    type: string;
    params?: Record<string, unknown>;
  };
  context: {
    app?: string;
    element?: string;
    timeOfDay?: number;
  };
  createdAt: number;
}

export interface Diagnosis {
  hypothesis: string;
  canSelfVerify: boolean;
  verificationSteps?: string[];
  alternative?: {
    action: string;
    worked: boolean;
  };
  rootCause?: string;
}

export interface SelfVerificationResult {
  verified: boolean;
  hypothesis: string;
  evidence: string[];
  confidence: number;
}

export interface AnomalyRule {
  name: string;
  check: (context: ObservationContext) => boolean;
  severity: AnomalyAlert["severity"];
  message: string;
}

export interface ObservationContext {
  currentApp: string | null;
  action: CanonicalAction | null;
  recentActions: CanonicalAction[];
  screenGraph: unknown;
  timeOfDay: number;
}

export class PatternAnalyzer {
  private patterns: Map<string, Pattern> = new Map();

  async findPatterns(episodes: Episode[]): Promise<Pattern[]> {
    const timePatterns = this.findTimePatterns(episodes);
    const appPatterns = this.findAppPatterns(episodes);
    const sequencePatterns = this.findSequencePatterns(episodes);

    return [...timePatterns, ...appPatterns, ...sequencePatterns];
  }

  private findTimePatterns(episodes: Episode[]): Pattern[] {
    const patterns: Pattern[] = [];
    const hourlyBuckets = new Map<number, Episode[]>();

    for (const ep of episodes) {
      const hour = new Date(ep.timestamp).getHours();
      if (!hourlyBuckets.has(hour)) {
        hourlyBuckets.set(hour, []);
      }
      hourlyBuckets.get(hour)!.push(ep);
    }

    for (const [hour, eps] of hourlyBuckets) {
      if (eps.length >= 5) {
        const actionCounts = new Map<string, number>();
        for (const ep of eps) {
          for (const action of ep.actions) {
            actionCounts.set(action.type, (actionCounts.get(action.type) || 0) + 1);
          }
        }

        const mostCommon = [...actionCounts.entries()]
          .sort((a, b) => b[1] - a[1])[0];

        if (mostCommon) {
          patterns.push({
            id: `time_pattern_${hour}`,
            type: "habitual",
            trigger: { type: "time", timeOfDay: hour },
            action: mostCommon[0],
            confidence: eps.length / episodes.length,
            occurrences: eps.length,
            lastSeen: Math.max(...eps.map((e) => e.timestamp)),
          });
        }
      }
    }

    return patterns;
  }

  private findAppPatterns(episodes: Episode[]): Pattern[] {
    const patterns: Pattern[] = [];
    const appActionPairs = new Map<string, Map<string, number>>();

    for (const ep of episodes) {
      if (!appActionPairs.has(ep.app)) {
        appActionPairs.set(ep.app, new Map());
      }
      const appMap = appActionPairs.get(ep.app)!;
      for (const action of ep.actions) {
        appMap.set(action.type, (appMap.get(action.type) || 0) + 1);
      }
    }

    for (const [app, actions] of appActionPairs) {
      for (const [action, count] of actions) {
        if (count >= 3) {
          patterns.push({
            id: `app_pattern_${app}_${action}`,
            type: "contextual",
            trigger: { type: "app", app },
            action,
            confidence: Math.min(1, count / 10),
            occurrences: count,
            lastSeen: Date.now(),
          });
        }
      }
    }

    return patterns;
  }

  private findSequencePatterns(_episodes: Episode[]): Pattern[] {
    return [];
  }

  getPatterns(): Pattern[] {
    return Array.from(this.patterns.values());
  }

  getPattern(id: string): Pattern | undefined {
    return this.patterns.get(id);
  }
}

export const patternAnalyzer = new PatternAnalyzer();

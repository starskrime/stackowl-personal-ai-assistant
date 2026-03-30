import type { CanonicalAction } from "../types.js";
import type { Episode, Diagnosis, SelfVerificationResult } from "./types.js";

export interface FailureAnalysis {
  episode: Episode;
  diagnosis: Diagnosis;
  verificationResult?: SelfVerificationResult;
  learnedInsight?: LearnedInsight;
}

export interface LearnedInsight {
  id: string;
  type: "precondition" | "alternative" | "context" | "sequence";
  description: string;
  confidence: number;
  verified: boolean;
  sourceFailure: string;
  createdAt: number;
  applied: boolean;
}

export interface ActionContext {
  app: string;
  elements: string[];
  precedingActions: string[];
  screenState: unknown;
}

export class SelfSupervisedLearner {
  private learnedInsights: Map<string, LearnedInsight> = new Map();
  private verificationCache: Map<string, boolean> = new Map();

  async learnFromFailure(
    episode: Episode,
    action: CanonicalAction
  ): Promise<FailureAnalysis | null> {
    if (episode.outcome !== "failed") {
      return null;
    }

    const diagnosis = await this.diagnose(episode, action);

    let verificationResult: SelfVerificationResult | undefined;

    if (diagnosis.canSelfVerify && diagnosis.verificationSteps) {
      verificationResult = await this.verifyHypothesis(diagnosis, episode);

      if (verificationResult.verified) {
        const insight = this.createInsightFromDiagnosis(diagnosis, episode);
        this.learnedInsights.set(insight.id, insight);
      }
    }

    return {
      episode,
      diagnosis,
      verificationResult,
      learnedInsight: verificationResult?.verified
        ? this.createInsightFromDiagnosis(diagnosis, episode)
        : undefined,
    };
  }

  private async diagnose(
    episode: Episode,
    action: CanonicalAction
  ): Promise<Diagnosis> {
    const hypotheses: string[] = [];

    if (episode.error?.includes("element")) {
      hypotheses.push("element_not_found");
    } else if (episode.error?.includes("disabled")) {
      hypotheses.push("element_disabled");
    } else if (episode.error?.includes("permission")) {
      hypotheses.push("permission_denied");
    } else if (episode.error?.includes("timeout")) {
      hypotheses.push("action_timeout");
    }

    const canSelfVerify = this.canVerifyHypothesis(hypotheses[0] || "");

    return {
      hypothesis: hypotheses[0] || "unknown_failure",
      canSelfVerify,
      verificationSteps: canSelfVerify ? this.getVerificationSteps(hypotheses[0]) : undefined,
      rootCause: this.inferRootCause(episode, action),
    };
  }

  private canVerifyHypothesis(hypothesis: string): boolean {
    const verifiable = [
      "element_not_found",
      "element_disabled",
      "wrong_element",
      "action_sequence",
    ];
    return verifiable.includes(hypothesis);
  }

  private getVerificationSteps(hypothesis: string): string[] {
    switch (hypothesis) {
      case "element_not_found":
        return [
          "Re-scan screen for element",
          "Try alternative selector",
          "Check if element is in different location",
        ];
      case "element_disabled":
        return [
          "Check parent dialog state",
          "Identify what enables the element",
          "Perform enabling action first",
        ];
      default:
        return [];
    }
  }

  private inferRootCause(episode: Episode, _action: CanonicalAction): string | undefined {
    if (episode.error?.includes("disabled")) {
      const parentAction = episode.actions[episode.actions.length - 2];
      if (parentAction) {
        return `Element requires "${parentAction.type}" to be performed first`;
      }
    }

    if (episode.actions.length > 1) {
      const preceding = episode.actions.slice(0, -1);
      if (preceding.every((a) => a.type === "observe")) {
        return "Missing prerequisite action in sequence";
      }
    }

    return episode.error;
  }

  private async verifyHypothesis(
    diagnosis: Diagnosis,
    episode: Episode
  ): Promise<SelfVerificationResult> {
    const hypothesis = diagnosis.hypothesis;

    if (this.verificationCache.has(hypothesis)) {
      return {
        verified: this.verificationCache.get(hypothesis)!,
        hypothesis,
        evidence: ["Cached verification result"],
        confidence: 0.9,
      };
    }

    const evidence: string[] = [];

    switch (hypothesis) {
      case "element_disabled": {
        const alternative = episode.actions.find(
          (a) => a.type === "click" && a.target.semanticSelector?.label
        );
        if (alternative) {
          evidence.push("Alternative action identified in history");
        }
        break;
      }
      case "element_not_found": {
        evidence.push("Element may be in different location or hidden");
        break;
      }
    }

    const verified = evidence.length >= 1;
    this.verificationCache.set(hypothesis, verified);

    return {
      verified,
      hypothesis,
      evidence,
      confidence: verified ? 0.8 : 0.4,
    };
  }

  private createInsightFromDiagnosis(diagnosis: Diagnosis, episode: Episode): LearnedInsight {
    const insightType = this.getInsightType(diagnosis);

    return {
      id: `insight_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      type: insightType,
      description: this.formatInsightDescription(diagnosis, episode),
      confidence: 0.7,
      verified: diagnosis.canSelfVerify,
      sourceFailure: episode.id,
      createdAt: Date.now(),
      applied: false,
    };
  }

  private getInsightType(diagnosis: Diagnosis): LearnedInsight["type"] {
    if (diagnosis.hypothesis.includes("disabled")) return "precondition";
    if (diagnosis.alternative?.worked) return "alternative";
    if (diagnosis.hypothesis.includes("sequence")) return "sequence";
    return "context";
  }

  private formatInsightDescription(diagnosis: Diagnosis, episode: Episode): string {
    const type = this.getInsightType(diagnosis);
    switch (type) {
      case "precondition":
        return `Action requires: ${diagnosis.rootCause || diagnosis.hypothesis}`;
      case "alternative":
        return `Alternative found: ${diagnosis.alternative?.action}`;
      default:
        return `Learn from failure: ${episode.error || diagnosis.hypothesis}`;
    }
  }

  async learnFromSuccess(
    episode: Episode
  ): Promise<LearnedInsight | null> {
    if (episode.outcome !== "success") return null;

    if (episode.actions.length < 2) return null;

    const insight: LearnedInsight = {
      id: `insight_success_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      type: "sequence",
      description: `Successful sequence: ${episode.actions.map((a) => a.type).join(" → ")}`,
      confidence: 0.6,
      verified: false,
      sourceFailure: episode.id,
      createdAt: Date.now(),
      applied: false,
    };

    this.learnedInsights.set(insight.id, insight);
    return insight;
  }

  getInsights(includeApplied = false): LearnedInsight[] {
    return includeApplied
      ? Array.from(this.learnedInsights.values())
      : Array.from(this.learnedInsights.values()).filter((i) => !i.applied);
  }

  getInsight(id: string): LearnedInsight | undefined {
    return this.learnedInsights.get(id);
  }

  markInsightApplied(id: string): void {
    const insight = this.learnedInsights.get(id);
    if (insight) {
      insight.applied = true;
    }
  }

  getInsightsByType(type: LearnedInsight["type"]): LearnedInsight[] {
    return Array.from(this.learnedInsights.values()).filter((i) => i.type === type);
  }

  async applyInsight(insight: LearnedInsight): Promise<boolean> {
    if (insight.applied) return false;

    this.markInsightApplied(insight.id);
    return true;
  }

  getInsightStats(): {
    total: number;
    byType: Record<string, number>;
    verifiedCount: number;
    appliedCount: number;
  } {
    const insights = Array.from(this.learnedInsights.values());

    return {
      total: insights.length,
      byType: {
        precondition: insights.filter((i) => i.type === "precondition").length,
        alternative: insights.filter((i) => i.type === "alternative").length,
        sequence: insights.filter((i) => i.type === "sequence").length,
        context: insights.filter((i) => i.type === "context").length,
      },
      verifiedCount: insights.filter((i) => i.verified).length,
      appliedCount: insights.filter((i) => i.applied).length,
    };
  }
}

export const selfSupervisedLearner = new SelfSupervisedLearner();

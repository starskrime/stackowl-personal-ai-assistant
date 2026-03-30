import type { Affordance, VisualSignature, UIElement, CanonicalAction } from "./types.js";
import { visualSignatureExtractor } from "./types.js";
import { semanticStore } from "./semantic-store.js";

export interface LearningOutcome {
  success: boolean;
  error?: string;
  correctedAction?: CanonicalAction;
}

export class AffordanceLearner {
  async recordAffordance(
    element: UIElement,
    action: CanonicalAction,
    outcome: LearningOutcome,
    appBundleId: string
  ): Promise<Affordance | null> {
    const signature = visualSignatureExtractor.extract(element);

    const existing = await this.findExistingAffordance(signature, action.type, appBundleId);

    if (existing) {
      await semanticStore.recordAttempt(existing.id, outcome.success);
      return existing;
    }

    if (!outcome.success && !outcome.correctedAction) {
      return null;
    }

    const actualAction = outcome.correctedAction?.type || action.type;

    return semanticStore.store({
      visualSignature: signature,
      action: actualAction,
      targetRole: element.type,
      targetLabel: element.semantic.label,
      app: appBundleId,
      successRate: outcome.success ? 1 : 0,
      attempts: 1,
      lastAttempt: Date.now(),
      alternatives: [],
    });
  }

  private async findExistingAffordance(
    signature: VisualSignature,
    action: string,
    app: string
  ): Promise<Affordance | null> {
    const similar = await semanticStore.findSimilar(signature, {
      role: undefined,
      action,
      app,
      limit: 5,
    });

    for (const aff of similar) {
      const distance = visualSignatureExtractor.distance(signature, aff.visualSignature);
      if (distance < 0.2) {
        return aff;
      }
    }

    return null;
  }

  async learnFromEpisode(
    elements: UIElement[],
    actions: CanonicalAction[],
    outcome: LearningOutcome,
    appBundleId: string
  ): Promise<void> {
    if (actions.length === 0) return;

    const lastAction = actions[actions.length - 1];
    const targetElement = this.findTargetElement(elements, lastAction);

    if (targetElement) {
      await this.recordAffordance(targetElement, lastAction, outcome, appBundleId);
    }
  }

  private findTargetElement(elements: UIElement[], action: CanonicalAction): UIElement | null {
    if (action.target.accessibilityPath) {
      return elements.find((e) => e.id === action.target.accessibilityPath) || null;
    }

    if (action.target.semanticSelector?.label) {
      return (
        elements.find((e) =>
          e.semantic.label?.includes(action.target.semanticSelector!.label!)
        ) || null
      );
    }

    if (action.target.visualRegion) {
      const region = action.target.visualRegion;
      return (
        elements.find(
          (e) =>
            e.bounds.x >= region.x &&
            e.bounds.y >= region.y &&
            e.bounds.x + e.bounds.width <= region.x + region.width &&
            e.bounds.y + e.bounds.height <= region.y + region.height
        ) || null
      );
    }

    return null;
  }

  async retrieveAffordances(
    element: UIElement,
    actionType: string,
    appBundleId?: string
  ): Promise<Affordance[]> {
    const signature = visualSignatureExtractor.extract(element);

    return semanticStore.findSimilar(signature, {
      role: element.type,
      action: actionType,
      app: appBundleId,
      limit: 10,
    });
  }

  async getLearnedActions(appBundleId: string): Promise<Map<string, Affordance[]>> {
    const all = await semanticStore.findByApp(appBundleId, 1000);

    const byAction = new Map<string, Affordance[]>();
    for (const aff of all) {
      const existing = byAction.get(aff.action) || [];
      existing.push(aff);
      byAction.set(aff.action, existing);
    }

    return byAction;
  }

  async getAffordanceStats(): Promise<{
    total: number;
    avgSuccessRate: number;
    byApp: Record<string, number>;
  }> {
    const stats = await semanticStore.getStats();
    return {
      total: stats.total,
      avgSuccessRate: stats.avgSuccessRate,
      byApp: stats.byApp,
    };
  }

  async improveAffordance(
    affordanceId: string,
    newElement: UIElement,
    success: boolean
  ): Promise<Affordance | null> {
    const existing = await semanticStore.query({});
    const aff = existing.find((a) => a.id === affordanceId);
    if (!aff) return null;

    const newSignature = visualSignatureExtractor.extract(newElement);

    const mergedSignature = this.mergeSignatures(aff.visualSignature, newSignature);

    return semanticStore.update(affordanceId, {
      visualSignature: mergedSignature,
      successRate: success ? Math.min(1, aff.successRate + 0.1) : Math.max(0, aff.successRate - 0.1),
    });
  }

  private mergeSignatures(a: VisualSignature, b: VisualSignature): VisualSignature {
    return {
      iconHash: a.iconHash || b.iconHash,
      colorHistogram: this.mergeHistograms(a.colorHistogram, b.colorHistogram),
      shapeFeatures: {
        aspectRatio: (a.shapeFeatures.aspectRatio + b.shapeFeatures.aspectRatio) / 2,
        borderRadius: (a.shapeFeatures.borderRadius + b.shapeFeatures.borderRadius) / 2,
        hasIcon: a.shapeFeatures.hasIcon || b.shapeFeatures.hasIcon,
        iconPosition: a.shapeFeatures.iconPosition,
      },
      textFeatures: {
        hasText: a.textFeatures.hasText || b.textFeatures.hasText,
        textLength: Math.round((a.textFeatures.textLength + b.textFeatures.textLength) / 2),
        isUppercase: a.textFeatures.isUppercase,
        hasShortcut: a.textFeatures.hasShortcut || b.textFeatures.hasShortcut,
      },
      positionFeatures: {
        region: a.positionFeatures.region,
        alignment: a.positionFeatures.alignment,
        relativeY: (a.positionFeatures.relativeY + b.positionFeatures.relativeY) / 2,
      },
    };
  }

  private mergeHistograms(a: number[], b: number[]): number[] {
    if (a.length === 0) return b;
    if (b.length === 0) return a;
    const len = Math.max(a.length, b.length);
    const result = new Array(len);
    for (let i = 0; i < len; i++) {
      result[i] = Math.round(((a[i] || 0) + (b[i] || 0)) / 2);
    }
    return result;
  }
}

export const affordanceLearner = new AffordanceLearner();

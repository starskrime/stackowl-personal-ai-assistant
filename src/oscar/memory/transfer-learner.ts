import type { Affordance, TransferCandidate, AppCategory, VisualSignature } from "./types.js";
import { visualSignatureExtractor } from "./types.js";
import { semanticStore } from "./semantic-store.js";

const APP_CATEGORIES: AppCategory[] = [
  {
    name: "photo-editor",
    apps: ["photoshop", "gimp", "preview", "affinity-photo"],
    commonAffordances: ["tool.select", "canvas.click", "layer.delete", "export"],
  },
  {
    name: "browser",
    apps: ["safari", "chrome", "firefox", "arc"],
    commonAffordances: ["navigate", "click", "type", "scroll"],
  },
  {
    name: "code-editor",
    apps: ["xcode", "vscode", "sublime", "textedit"],
    commonAffordances: ["open.file", "edit.text", "save.file", "find"],
  },
  {
    name: "word-processor",
    apps: ["word", "pages", "textedit"],
    commonAffordances: ["open.file", "edit.text", "format", "save.file"],
  },
];

export class TransferLearner {
  private categoryCache: Map<string, AppCategory> = new Map();

  constructor() {
    for (const cat of APP_CATEGORIES) {
      for (const app of cat.apps) {
        this.categoryCache.set(app, cat);
      }
    }
  }

  async transferTo(
    targetApp: string,
    targetElementSignature: VisualSignature,
    actionType?: string
  ): Promise<TransferCandidate[]> {
    const candidates: TransferCandidate[] = [];

    const sourceApps = await this.findSourceApps(targetApp);

    for (const sourceApp of sourceApps) {
      const affordances = await semanticStore.findByApp(sourceApp, 100);

      for (const aff of affordances) {
        if (actionType && aff.action !== actionType) continue;

        const score = this.computeTransferability(aff, targetApp, targetElementSignature);
        if (score > 0.3) {
          candidates.push({
            affordance: aff,
            transferabilityScore: score,
            sourceApp: aff.app,
            targetApp,
            matchedFeatures: this.getMatchedFeatures(aff.visualSignature, targetElementSignature),
          });
        }
      }
    }

    candidates.sort((a, b) => b.transferabilityScore - a.transferabilityScore);
    return candidates.slice(0, 20);
  }

  private async findSourceApps(targetApp: string): Promise<string[]> {
    const targetCategory = this.categoryCache.get(targetApp);

    if (!targetCategory) {
      const allApps = await semanticStore.getStats();
      return Object.keys(allApps.byApp).filter((app) => app !== targetApp);
    }

    const category = targetCategory;
    const knownApps = category.apps.filter(
      async (app) => app !== targetApp && (await semanticStore.findByApp(app)).length > 0
    );

    return knownApps;
  }

  computeTransferability(
    affordance: Affordance,
    targetApp: string,
    targetSignature: VisualSignature
  ): number {
    const sourceCategory = this.categoryCache.get(affordance.app);
    const targetCategory = this.categoryCache.get(targetApp);

    let iconSimilarity = 0.5;
    if (affordance.visualSignature.iconHash && targetSignature.iconHash) {
      iconSimilarity =
        1 - visualSignatureExtractor.distance(affordance.visualSignature, targetSignature);
    }

    const roleMatch =
      affordance.targetRole === targetSignature.shapeFeatures.hasIcon.toString() ? 0.8 : 0.5;

    let categoryBonus = 0;
    if (sourceCategory && targetCategory) {
      if (sourceCategory.name === targetCategory.name) {
        categoryBonus = 0.2;
      }
    }

    const successWeight = affordance.successRate;

    return Math.min(
      1,
      iconSimilarity * 0.4 + roleMatch * 0.3 + categoryBonus + successWeight * 0.1
    );
  }

  private getMatchedFeatures(
    source: VisualSignature,
    target: VisualSignature
  ): string[] {
    const matched: string[] = [];

    if (source.iconHash && target.iconHash) {
      const dist = visualSignatureExtractor.distance(source, target);
      if (dist < 0.3) matched.push("icon");
    }

    if (
      source.shapeFeatures.hasIcon === target.shapeFeatures.hasIcon &&
      source.shapeFeatures.hasIcon
    ) {
      matched.push("iconPresence");
    }

    if (source.positionFeatures.region === target.positionFeatures.region) {
      matched.push("region");
    }

    if (
      Math.abs(source.positionFeatures.relativeY - target.positionFeatures.relativeY) < 0.1
    ) {
      matched.push("verticalPosition");
    }

    if (source.textFeatures.hasText === target.textFeatures.hasText) {
      matched.push("textPresence");
    }

    return matched;
  }

  async suggestAdaptations(
    affordance: Affordance,
    targetApp: string
  ): Promise<{ adapted: Partial<Affordance>; confidence: number }> {
    const sourceCategory = this.categoryCache.get(affordance.app);
    const targetCategory = this.categoryCache.get(targetApp);

    let confidence = 0.5;

    if (sourceCategory && targetCategory && sourceCategory.name === targetCategory.name) {
      confidence = 0.8;
    }

    const adaptedSignature: VisualSignature = {
      ...affordance.visualSignature,
      positionFeatures: {
        ...affordance.visualSignature.positionFeatures,
        region: this.inferRegionForApp(targetApp, affordance.visualSignature.positionFeatures.region),
      },
    };

    return {
      adapted: {
        visualSignature: adaptedSignature,
        app: targetApp,
        attempts: 0,
        successRate: affordance.successRate * confidence,
      },
      confidence,
    };
  }

  private inferRegionForApp(
    _app: string,
    currentRegion: VisualSignature["positionFeatures"]["region"]
  ): VisualSignature["positionFeatures"]["region"] {
    return currentRegion;
  }

  async getAppSimilarity(app1: string, app2: string): Promise<number> {
    const cat1 = this.categoryCache.get(app1);
    const cat2 = this.categoryCache.get(app2);

    if (cat1 && cat2 && cat1.name === cat2.name) {
      return 0.9;
    }

    if (cat1 && cat2) {
      return 0.3;
    }

    const affs1 = await semanticStore.findByApp(app1);
    const affs2 = await semanticStore.findByApp(app2);

    if (affs1.length === 0 || affs2.length === 0) {
      return 0.1;
    }

    let totalSimilarity = 0;
    let comparisons = 0;

    for (const a1 of affs1.slice(0, 10)) {
      for (const a2 of affs2.slice(0, 10)) {
        if (a1.action === a2.action) {
          totalSimilarity += visualSignatureExtractor.distance(a1.visualSignature, a2.visualSignature);
          comparisons++;
        }
      }
    }

    return comparisons > 0 ? 1 - totalSimilarity / comparisons : 0.1;
  }

  getCategories(): AppCategory[] {
    return APP_CATEGORIES;
  }

  getCategoryForApp(app: string): AppCategory | undefined {
    return this.categoryCache.get(app);
  }
}

export const transferLearner = new TransferLearner();

import type { Episode, Affordance, Skill, UIElement, CanonicalAction } from "./types.js";
import { EpisodicStore, EpisodicQuery } from "./episodic-store.js";
import { SemanticStore } from "./semantic-store.js";
import { ProceduralStore, SkillQuery } from "./procedural-store.js";
import { AffordanceLearner } from "./affordance-learner.js";
import { TransferLearner } from "./transfer-learner.js";
import { visualSignatureExtractor } from "./types.js";
import { SkillComposer, Recording } from "../skills/composer.js";
import { SkillReplayer } from "../skills/replayer.js";

export interface MemoryStats {
  episodes: {
    total: number;
    byOutcome: Record<string, number>;
    byApp: Record<string, number>;
  };
  affordances: {
    total: number;
    byApp: Record<string, number>;
    avgSuccessRate: number;
  };
  skills: {
    total: number;
    byTargetApp: Record<string, number>;
    avgSuccessRate: number;
    totalUsage: number;
  };
}

class VisualMemoryNetworkImpl {
  private episodicStore = new EpisodicStore();
  private semanticStore = new SemanticStore();
  private proceduralStore = new ProceduralStore();
  private affordanceLearner = new AffordanceLearner();
  private transferLearner = new TransferLearner();
  private skillComposer = new SkillComposer();
  private skillReplayer = new SkillReplayer();

  async recordEpisode(episode: Omit<Episode, "id" | "timestamp">): Promise<Episode> {
    return this.episodicStore.record(episode);
  }

  async recordAffordance(
    element: UIElement,
    action: CanonicalAction,
    outcome: { success: boolean; error?: string; correctedAction?: CanonicalAction },
    appBundleId: string
  ): Promise<Affordance | null> {
    return this.affordanceLearner.recordAffordance(element, action, outcome, appBundleId);
  }

  async learnFromExperience(
    elements: UIElement[],
    actions: CanonicalAction[],
    outcome: "success" | "partial" | "failed",
    appBundleId: string,
    error?: string
  ): Promise<void> {
    await this.episodicStore.record({
      app: appBundleId,
      appBundleId,
      actions,
      outcome,
      error,
    });

    if (outcome !== "success") {
      await this.affordanceLearner.learnFromEpisode(
        elements,
        actions,
        { success: outcome === "partial", error },
        appBundleId
      );
    }
  }

  async queryEpisodes(query: EpisodicQuery): Promise<Episode[]> {
    return this.episodicStore.query(query);
  }

  async queryAffordances(query: Parameters<typeof this.semanticStore.query>[0]): Promise<Affordance[]> {
    return this.semanticStore.query(query);
  }

  async querySkills(query: SkillQuery): Promise<Skill[]> {
    return this.proceduralStore.query(query);
  }

  async retrieveAffordancesForElement(
    element: UIElement,
    actionType: string,
    appBundleId?: string
  ): Promise<Affordance[]> {
    return this.affordanceLearner.retrieveAffordances(element, actionType, appBundleId);
  }

  async findSkillsForTask(taskName: string, _appBundleId?: string): Promise<Skill[]> {
    return this.proceduralStore.findByName(taskName, 10);
  }

  async transferKnowledge(
    targetApp: string,
    targetElement: UIElement,
    actionType?: string
  ): Promise<{ affordance: Affordance; transferabilityScore: number; sourceApp: string; targetApp: string; matchedFeatures: string[] }[]> {
    const signature = visualSignatureExtractor.extract(targetElement);
    return this.transferLearner.transferTo(targetApp, signature, actionType);
  }

  async createSkillFromRecording(
    recording: Recording,
    name?: string
  ): Promise<{ skill: Skill; warnings: string[] }> {
    const result = this.skillComposer.generalize(recording, name);
    const stored = await this.proceduralStore.store(result.skill);

    return {
      skill: stored,
      warnings: result.warnings,
    };
  }

  async executeSkill(
    skill: Skill,
    context: { app: string; elements: UIElement[] },
    params: Record<string, unknown> = {}
  ): Promise<{ success: boolean; stepsExecuted: number; totalSteps: number; errors: string[]; failedStep?: number }> {
    const validated = await this.skillReplayer.validateParams(skill, params);
    if (!validated.valid) {
      return {
        success: false,
        stepsExecuted: 0,
        totalSteps: skill.steps.length,
        errors: [`Missing required parameters: ${validated.missing.join(", ")}`],
      };
    }

    await this.proceduralStore.recordUsage(skill.id);

    return this.skillReplayer.replay(skill, context, params);
  }

  async getRecommendations(
    appBundleId: string,
    actionType?: string
  ): Promise<{
    affordances: Affordance[];
    skills: Skill[];
    transferred: { affordance: Affordance; transferabilityScore: number; sourceApp: string; targetApp: string; matchedFeatures: string[] }[];
  }> {
    const [affordances, skills] = await Promise.all([
      actionType
        ? this.semanticStore.query({ app: appBundleId, action: actionType, limit: 5 })
        : this.semanticStore.findByApp(appBundleId, 5),
      this.proceduralStore.findByTargetApp(appBundleId, 5),
    ]);

    const transferred: { affordance: Affordance; transferabilityScore: number; sourceApp: string; targetApp: string; matchedFeatures: string[] }[] = actionType
      ? []
      : [];

    return {
      affordances,
      skills,
      transferred,
    };
  }

  async getStats(): Promise<MemoryStats> {
    const [episodeStats, affordanceStats, skillStats] = await Promise.all([
      this.episodicStore.getStats(),
      this.semanticStore.getStats(),
      this.proceduralStore.getStats(),
    ]);

    return {
      episodes: {
        total: episodeStats.total,
        byOutcome: episodeStats.byOutcome,
        byApp: episodeStats.byApp,
      },
      affordances: {
        total: affordanceStats.total,
        byApp: affordanceStats.byApp,
        avgSuccessRate: affordanceStats.avgSuccessRate,
      },
      skills: {
        total: skillStats.total,
        byTargetApp: skillStats.byTargetApp,
        avgSuccessRate: skillStats.avgSuccessRate,
        totalUsage: skillStats.totalUsage,
      },
    };
  }

  async getRecentEpisodes(count: number = 10): Promise<Episode[]> {
    return this.episodicStore.getRecent(count);
  }

  async getFailedEpisodes(count: number = 10): Promise<Episode[]> {
    return this.episodicStore.getFailed(count);
  }

  async getCorrectedEpisodes(count: number = 10): Promise<Episode[]> {
    return this.episodicStore.getCorrected(count);
  }

  startRecording(app: { bundleId: string; name: string }, name?: string): string {
    return this.skillComposer.startRecording(app, name);
  }

  async finishRecording(recordingId: string): Promise<Recording | null> {
    return this.skillComposer.finishRecording(recordingId);
  }

  cancelRecording(recordingId: string): void {
    this.skillComposer.cancelRecording(recordingId);
  }

  getActiveRecordings(): Recording[] {
    return this.skillComposer.getActiveRecordings();
  }

  async clearAll(): Promise<void> {
    await this.episodicStore.clear();
    await this.semanticStore.clear();
    await this.proceduralStore.clear();
    console.log("[VisualMemoryNetwork] All memory stores cleared");
  }
}

export const visualMemoryNetwork = new VisualMemoryNetworkImpl();
export { VisualMemoryNetworkImpl as VisualMemoryNetwork };

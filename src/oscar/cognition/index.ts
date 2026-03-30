import { CognitiveLoop, cognitiveLoop } from "./loop.js";
import { ProactiveAssistant, proactiveAssistant } from "./proactive.js";
import { SelfSupervisedLearner, selfSupervisedLearner } from "./self-learner.js";
import type { LearnedInsight, FailureAnalysis } from "./self-learner.js";
import { AnomalyDetector, anomalyDetector } from "./anomaly-detector.js";
import { MultiOscarParliament, multiOscarParliament } from "./multi-oscar.js";
import type { Observation, Reflection, Suggestion, AnomalyAlert, Episode } from "./types.js";
import type { CognitiveLoopConfig, CognitiveState } from "./loop.js";
import type { AnomalyConfig } from "./anomaly-detector.js";
import type { CanonicalAction } from "../types.js";

export interface CognitionConfig {
  cognitiveLoop?: Partial<CognitiveLoopConfig>;
  anomaly?: Partial<AnomalyConfig>;
  enableParliament?: boolean;
}

export class CognitionEngine {
  private cognitiveLoop: CognitiveLoop;
  private proactiveAssistant: ProactiveAssistant;
  private selfLearner: SelfSupervisedLearner;
  private anomalyDetector: AnomalyDetector;
  private parliament: MultiOscarParliament;

  constructor(config: CognitionConfig = {}) {
    this.cognitiveLoop = new CognitiveLoop(config.cognitiveLoop);
    this.proactiveAssistant = new ProactiveAssistant();
    this.selfLearner = new SelfSupervisedLearner();
    this.anomalyDetector = new AnomalyDetector(config.anomaly);
    this.parliament = new MultiOscarParliament();

    this.setupHandlers();
  }

  private setupHandlers(): void {
    this.cognitiveLoop.onReflection((reflection) => {
      this.handleReflection(reflection);
    });
  }

  private async handleReflection(reflection: Reflection): Promise<void> {
    for (const improvement of reflection.improvements) {
      if (improvement.type === "affordance" || improvement.type === "precondition") {
        await this.selfLearner.applyInsight(improvement as unknown as LearnedInsight);
      }
    }
  }

  async start(): Promise<void> {
    await this.cognitiveLoop.start();
    console.log("[CognitionEngine] Started - Autonomous cognitive capabilities active");
  }

  stop(): void {
    this.cognitiveLoop.stop();
  }

  async observe(): Promise<Observation | null> {
    return this.cognitiveLoop.observe();
  }

  async reflect(): Promise<Reflection | null> {
    return this.cognitiveLoop.reflect();
  }

  recordEpisode(episode: Episode): void {
    this.cognitiveLoop.recordEpisode(episode);
  }

  async learnFromFailure(
    episode: Episode,
    action: CanonicalAction
  ): Promise<{ analysis: FailureAnalysis | null; alerts: AnomalyAlert[] }> {
    const analysis = await this.selfLearner.learnFromFailure(episode, action);

    const context = {
      currentApp: episode.app || null,
      action,
      recentActions: episode.actions,
      screenGraph: null,
      timeOfDay: new Date(episode.timestamp).getHours(),
    };

    const alerts = await this.anomalyDetector.detect(context);

    return { analysis, alerts };
  }

  async learnFromSuccess(episode: Episode): Promise<LearnedInsight | null> {
    return this.selfLearner.learnFromSuccess(episode);
  }

  async detectAnomalies(context: {
    currentApp: string | null;
    action: CanonicalAction | null;
    recentActions: CanonicalAction[];
    screenGraph?: unknown;
  }): Promise<AnomalyAlert[]> {
    return this.anomalyDetector.detect({
      currentApp: context.currentApp,
      action: context.action,
      recentActions: context.recentActions,
      screenGraph: context.screenGraph ?? null,
      timeOfDay: new Date().getHours(),
    });
  }

  async getSuggestions(context: {
    currentApp: string | null;
    currentElement?: string;
    recentActions: string[];
  }): Promise<Suggestion[]> {
    return this.proactiveAssistant.suggest({
      ...context,
      timeOfDay: new Date().getHours(),
      dayOfWeek: new Date().getDay(),
    });
  }

  getInsights(includeApplied = false): LearnedInsight[] {
    return this.selfLearner.getInsights(includeApplied);
  }

  getAlerts(includeAcknowledged = false, limit?: number): AnomalyAlert[] {
    return this.anomalyDetector.getAlerts(includeAcknowledged, limit);
  }

  acknowledgeAlert(alertId: string): boolean {
    return this.anomalyDetector.acknowledgeAlert(alertId);
  }

  getCognitiveState(): CognitiveState {
    return this.cognitiveLoop.getState();
  }

  getAnomalyStats(): ReturnType<AnomalyDetector["getAlertStats"]> {
    return this.anomalyDetector.getAlertStats();
  }

  getInsightStats(): ReturnType<SelfSupervisedLearner["getInsightStats"]> {
    return this.selfLearner.getInsightStats();
  }

  getSuggestionStats(): ReturnType<ProactiveAssistant["getSuggestionStats"]> {
    return this.proactiveAssistant.getSuggestionStats();
  }

  getPatterns() {
    return this.cognitiveLoop.getPatterns();
  }

  getImprovements() {
    return this.cognitiveLoop.getImprovements();
  }

  markImprovementApplied(id: string): void {
    this.cognitiveLoop.markImprovementApplied(id);
  }

  isRunning(): boolean {
    return this.cognitiveLoop.isRunning();
  }

  getParliament(): MultiOscarParliament {
    return this.parliament;
  }
}

export const cognitionEngine = new CognitionEngine();
export {
  cognitiveLoop,
  proactiveAssistant,
  selfSupervisedLearner,
  anomalyDetector,
  multiOscarParliament,
};

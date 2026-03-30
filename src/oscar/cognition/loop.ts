import type { Observation, Reflection, Episode, Pattern, Improvement } from "./types.js";
import { patternAnalyzer } from "./types.js";

export interface CognitiveLoopConfig {
  observeIntervalMs: number;
  reflectIntervalMs: number;
  maxRecentEpisodes: number;
  enableProactive: boolean;
  enableSelfLearning: boolean;
  enableAnomalyDetection: boolean;
}

export interface CognitiveState {
  running: boolean;
  lastObserve: number;
  lastReflect: number;
  observationCount: number;
  reflectionCount: number;
  anomaliesDetected: number;
  suggestionsGenerated: number;
}

export class CognitiveLoop {
  private config: Required<CognitiveLoopConfig>;
  private state: CognitiveState;
  private recentEpisodes: Episode[] = [];
  private patterns: Pattern[] = [];
  private improvements: Improvement[] = [];
  private observeTimer: ReturnType<typeof setInterval> | null = null;
  private reflectTimer: ReturnType<typeof setInterval> | null = null;
  private lastApp: string | null = null;

  private observers: Set<(obs: Observation) => void> = new Set();
  private reflectionHandlers: Set<(ref: Reflection) => void> = new Set();

  constructor(config: Partial<CognitiveLoopConfig> = {}) {
    this.config = {
      observeIntervalMs: config.observeIntervalMs ?? 1000,
      reflectIntervalMs: config.reflectIntervalMs ?? 60000,
      maxRecentEpisodes: config.maxRecentEpisodes ?? 100,
      enableProactive: config.enableProactive ?? true,
      enableSelfLearning: config.enableSelfLearning ?? true,
      enableAnomalyDetection: config.enableAnomalyDetection ?? true,
    };

    this.state = {
      running: false,
      lastObserve: 0,
      lastReflect: 0,
      observationCount: 0,
      reflectionCount: 0,
      anomaliesDetected: 0,
      suggestionsGenerated: 0,
    };
  }

  async start(): Promise<void> {
    if (this.state.running) return;

    this.state.running = true;
    this.state.lastObserve = Date.now();
    this.state.lastReflect = Date.now();

    this.observeTimer = setInterval(
      () => this.observe().catch(console.error),
      this.config.observeIntervalMs
    );

    this.reflectTimer = setInterval(
      () => this.reflect().catch(console.error),
      this.config.reflectIntervalMs
    );

    console.log("[CognitiveLoop] Started - Observe-reflect-act cycle active");
  }

  stop(): void {
    if (!this.state.running) return;

    this.state.running = false;

    if (this.observeTimer) {
      clearInterval(this.observeTimer);
      this.observeTimer = null;
    }

    if (this.reflectTimer) {
      clearInterval(this.reflectTimer);
      this.reflectTimer = null;
    }

    console.log("[CognitiveLoop] Stopped");
  }

  async observe(): Promise<Observation | null> {
    if (!this.state.running) return null;

    try {
      const observation = await this.captureObservation();
      this.state.lastObserve = Date.now();
      this.state.observationCount++;

      this.notifyObservers(observation);

      return observation;
    } catch (error) {
      console.warn("[CognitiveLoop] Observation failed:", error);
      return null;
    }
  }

  private async captureObservation(): Promise<Observation> {
    const now = Date.now();
    const timeOfDay = new Date(now).getHours();

    return {
      timestamp: now,
      app: this.lastApp,
      focusedElement: null,
      screenChanged: true,
      elements: [],
      cursorPosition: { x: 0, y: 0 },
      recentActions: [],
      timeOfDay,
    };
  }

  async reflect(): Promise<Reflection | null> {
    if (!this.state.running) return null;

    try {
      const reflection = await this.performReflection();
      this.state.lastReflect = Date.now();
      this.state.reflectionCount++;

      this.notifyReflectionHandlers(reflection);

      return reflection;
    } catch (error) {
      console.warn("[CognitiveLoop] Reflection failed:", error);
      return null;
    }
  }

  private async performReflection(): Promise<Reflection> {
    const recent = this.recentEpisodes.slice(-this.config.maxRecentEpisodes);

    const patterns = await patternAnalyzer.findPatterns(recent);

    const anomalies = this.detectAnomalies(recent);

    const improvements = await this.identifyImprovements(recent);

    this.patterns = patterns;
    this.improvements = improvements;

    return {
      timestamp: Date.now(),
      recentEpisodes: recent,
      patterns,
      anomalies,
      improvements,
    };
  }

  private detectAnomalies(episodes: Episode[]): Improvement[] {
    if (!this.config.enableSelfLearning) return [];

    const anomalies: Improvement[] = [];

    const failedEpisodes = episodes.filter((e) => e.outcome === "failed");
    if (failedEpisodes.length > 3) {
      anomalies.push({
        id: `anomaly_${Date.now()}`,
        type: "precondition",
        description: `Multiple failures detected (${failedEpisodes.length} recent)`,
        confidence: 0.8,
        source: "failure",
        createdAt: Date.now(),
        applied: false,
      });
    }

    return anomalies;
  }

  private async identifyImprovements(episodes: Episode[]): Promise<Improvement[]> {
    if (!this.config.enableSelfLearning) return [];

    const improvements: Improvement[] = [];

    for (const episode of episodes) {
      if (episode.outcome === "failed" && episode.error) {
        improvements.push({
          id: `improvement_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
          type: "precondition",
          description: `Failure analysis: ${episode.error}`,
          confidence: 0.6,
          source: "failure",
          createdAt: Date.now(),
          applied: false,
        });
      }
    }

    return improvements;
  }

  recordEpisode(episode: Episode): void {
    this.recentEpisodes.push(episode);

    if (this.recentEpisodes.length > this.config.maxRecentEpisodes * 2) {
      this.recentEpisodes = this.recentEpisodes.slice(-this.config.maxRecentEpisodes);
    }
  }

  onObservation(handler: (obs: Observation) => void): () => void {
    this.observers.add(handler);
    return () => this.observers.delete(handler);
  }

  onReflection(handler: (ref: Reflection) => void): () => void {
    this.reflectionHandlers.add(handler);
    return () => this.reflectionHandlers.delete(handler);
  }

  private notifyObservers(observation: Observation): void {
    for (const handler of this.observers) {
      try {
        handler(observation);
      } catch (error) {
        console.warn("[CognitiveLoop] Observer error:", error);
      }
    }
  }

  private notifyReflectionHandlers(reflection: Reflection): void {
    for (const handler of this.reflectionHandlers) {
      try {
        handler(reflection);
      } catch (error) {
        console.warn("[CognitiveLoop] Reflection handler error:", error);
      }
    }
  }

  getState(): CognitiveState {
    return { ...this.state };
  }

  getRecentEpisodes(count?: number): Episode[] {
    return count
      ? this.recentEpisodes.slice(-count)
      : [...this.recentEpisodes];
  }

  getPatterns(): Pattern[] {
    return [...this.patterns];
  }

  getImprovements(includeApplied = false): Improvement[] {
    return includeApplied
      ? this.improvements
      : this.improvements.filter((i) => !i.applied);
  }

  markImprovementApplied(id: string): void {
    const improvement = this.improvements.find((i) => i.id === id);
    if (improvement) {
      improvement.applied = true;
    }
  }

  isRunning(): boolean {
    return this.state.running;
  }
}

export const cognitiveLoop = new CognitiveLoop();

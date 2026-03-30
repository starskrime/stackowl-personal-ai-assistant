import type {
  CanonicalAction,
  CanonicalTarget,
  VerificationCondition,
  AppInfo,
  ScreenBuffer,
  AccessibilityState,
  UIElement,
  ScreenGraph,
} from "./types.js";
import { macOSAdapter } from "./platform/adapters/macos.js";
import { TripleBufferPipeline } from "./perception/pipeline.js";
import { verificationEngine } from "./verification/micro-engine.js";
import { actionResolver } from "./action/semantic/resolver.js";
import {
  ScreenGraphObservatory,
  ObservationResult as ObservatoryResult,
} from "./perception/observatory.js";
import { QueryBuilder } from "./perception/query-engine.js";
import { IntentParser, IntentDecomposer, ParsedIntent, DecomposedStep } from "./intent/decomposer.js";
import { DAGValidator } from "./intent/dag-validator.js";
import { RecoveryController } from "./action/recovery-controller.js";
import type { FailureContext } from "./action/recovery-controller.js";
import { CheckpointManager } from "./action/checkpoint-manager.js";
import { VisualMemoryNetwork, visualMemoryNetwork } from "./memory/index.js";
import type { Affordance, Skill } from "./memory/types.js";
import { CognitionEngine, cognitionEngine } from "./cognition/index.js";

export interface OscarConfig {
  enableScreenPipeline?: boolean;
  pipelineIntervalMs?: number;
  defaultVerificationTimeout?: number;
  maxRetries?: number;
  enableObservatory?: boolean;
}

export interface ActionResult {
  success: boolean;
  error?: string;
  attempts?: number;
  screenshot?: ScreenBuffer;
}

export interface ObservationResult {
  screenBuffer: ScreenBuffer;
  accessibilityState: AccessibilityState;
  focusedApp: string | null;
  timestamp: number;
}

export interface GraphObservationResult extends ObservatoryResult {}

export class Oscar {
  private config: Required<OscarConfig>;
  private screenPipeline: TripleBufferPipeline;
  private observatory: ScreenGraphObservatory | null = null;
  private running = false;
  private intentParser: IntentParser;
  private intentDecomposer: IntentDecomposer;
  private dagValidator: DAGValidator;
  private recoveryController: RecoveryController;
  private checkpointManager: CheckpointManager;
  private memory: VisualMemoryNetwork;
  private cognition: CognitionEngine;

  constructor(config: OscarConfig = {}) {
    this.config = {
      enableScreenPipeline: config.enableScreenPipeline ?? true,
      pipelineIntervalMs: config.pipelineIntervalMs ?? 16,
      defaultVerificationTimeout: config.defaultVerificationTimeout ?? 2000,
      maxRetries: config.maxRetries ?? 3,
      enableObservatory: config.enableObservatory ?? true,
    };

    this.screenPipeline = new TripleBufferPipeline(this.config.pipelineIntervalMs);

    if (this.config.enableObservatory) {
      this.observatory = new ScreenGraphObservatory();
    }

    this.intentParser = new IntentParser();
    this.intentDecomposer = new IntentDecomposer();
    this.dagValidator = new DAGValidator();
    this.recoveryController = new RecoveryController();
    this.checkpointManager = new CheckpointManager();
    this.memory = visualMemoryNetwork;
    this.cognition = cognitionEngine;
  }

  async start(): Promise<void> {
    if (this.running) return;

    if (this.config.enableScreenPipeline) {
      this.screenPipeline.start();
    }

    if (this.observatory) {
      await this.observatory.start();
    }

    this.running = true;
    console.log("[Oscar] Started - Universal Computer Control Interface active");
  }

  stop(): void {
    if (!this.running) return;

    this.screenPipeline.stop();
    this.running = false;
    console.log("[Oscar] Stopped");
  }

  async observe(): Promise<ObservationResult> {
    const [screenBuffer, accessibilityState, focusedApp] = await Promise.all([
      this.captureScreen(),
      macOSAdapter.getAccessibilityTree(),
      macOSAdapter.getFocusedApp(),
    ]);

    return {
      screenBuffer,
      accessibilityState,
      focusedApp,
      timestamp: Date.now(),
    };
  }

  async captureScreen(): Promise<ScreenBuffer> {
    return this.screenPipeline.captureRegion({ x: 0, y: 0, width: 1920, height: 1080 });
  }

  async captureRegion(bounds: { x: number; y: number; width: number; height: number }): Promise<ScreenBuffer> {
    return this.screenPipeline.captureRegion(bounds);
  }

  async act(
    actionType: CanonicalAction["type"],
    target?: CanonicalTarget,
    params?: Record<string, unknown>,
    verification?: VerificationCondition
  ): Promise<ActionResult> {
    const action: CanonicalAction = {
      type: actionType,
      target: target || {},
      params: params || {},
      timestamp: Date.now(),
      traceId: this.generateTraceId(),
    };

    let lastError: string | undefined;

    for (let attempt = 0; attempt < this.config.maxRetries; attempt++) {
      const result = await macOSAdapter.executeAction(action);

      if (!result.success) {
        lastError = result.error;
        await this.delay(50 * (attempt + 1));
        continue;
      }

      if (verification) {
        const verifyResult = await verificationEngine.verify(action, [verification]);

        if (!verifyResult.success) {
          lastError = verifyResult.error;
          await this.delay(50 * (attempt + 1));
          macOSAdapter.invalidateCache();
          continue;
        }

        return {
          success: true,
          attempts: attempt + 1,
        };
      }

      return {
        success: true,
        attempts: attempt + 1,
      };
    }

    return {
      success: false,
      error: lastError || "Action failed after max retries",
      attempts: this.config.maxRetries,
    };
  }

  async click(
    target: CanonicalTarget,
    params?: { button?: string; clickCount?: number },
    verification?: VerificationCondition
  ): Promise<ActionResult> {
    return this.act("click", target, params, verification);
  }

  async type(
    text: string,
    target?: CanonicalTarget,
    verification?: VerificationCondition
  ): Promise<ActionResult> {
    return this.act("type", target, { text }, verification);
  }

  async hotkey(
    key: string,
    modifiers?: string[],
    verification?: VerificationCondition
  ): Promise<ActionResult> {
    return this.act("hotkey", {}, { key, modifiers }, verification);
  }

  async launch(appNameOrBundleId: string): Promise<ActionResult> {
    const result = await this.act("launch", {}, { application: appNameOrBundleId });

    if (result.success) {
      await this.delay(500);
    }

    return result;
  }

  async close(): Promise<ActionResult> {
    return this.act("close");
  }

  async scroll(
    direction: "up" | "down" | "left" | "right",
    amount?: number
  ): Promise<ActionResult> {
    return this.act("scroll", {}, { direction, amount: amount || 10 });
  }

  async drag(
    fromX: number,
    fromY: number,
    toX: number,
    toY: number
  ): Promise<ActionResult> {
    return this.act("drag", {}, { fromX, fromY, toX, toY });
  }

  async executeWithVerification(
    action: CanonicalAction,
    verification: VerificationCondition
  ): Promise<ActionResult> {
    const result = await macOSAdapter.executeAction(action);

    if (!result.success) {
      return {
        success: false,
        error: result.error,
      };
    }

    const verifyResult = await verificationEngine.verify(action, [verification]);

    return {
      success: verifyResult.success,
      error: verifyResult.error,
      attempts: verifyResult.attempts,
    };
  }

  async resolveAndExecute(
    actionType: string,
    target: CanonicalTarget,
    app?: AppInfo
  ): Promise<ActionResult> {
    const currentApp = app || (await this.getCurrentApp());

    if (!currentApp) {
      return {
        success: false,
        error: "No app specified and could not detect current app",
      };
    }

    const resolved = await actionResolver.resolve(actionType, target, currentApp);

    if (resolved.length === 0) {
      return {
        success: false,
        error: "Could not resolve action",
      };
    }

    const best = resolved[0];
    const result = await macOSAdapter.executeAction(best.action);

    actionResolver.recordActionResult(
      currentApp?.bundleId || "unknown",
      actionType,
      result.success
    );

    return {
      success: result.success,
      error: result.error,
      attempts: 1,
    };
  }

  async getCurrentApp(): Promise<AppInfo | null> {
    const appName = await macOSAdapter.getFocusedApp();
    if (!appName) return null;

    const bundleId = await this.getBundleId(appName);

    return {
      bundleId,
      name: appName,
      pid: 0,
    };
  }

  private async getBundleId(appName: string): Promise<string> {
    try {
      const { exec } = await import("child_process");
      const { promisify } = await import("util");
      const execAsync = promisify(exec);

      const { stdout } = await execAsync(
        `osascript -e 'tell application "${appName}" to return id'`,
        { timeout: 1000 }
      );
      return stdout.trim();
    } catch {
      return appName;
    }
  }

  async getWindows(): Promise<{ title: string; bundleId: string; bounds: { x: number; y: number; width: number; height: number } }[]> {
    const app = await this.getCurrentApp();
    if (!app) return [];

    const windows = await macOSAdapter.getWindowsForApp(app.bundleId);
    return windows.map((w) => ({
      title: w.title,
      bundleId: w.bundleId,
      bounds: w.bounds,
    }));
  }

  async waitForElement(
    selector: { role?: string; label?: string; index?: number },
    timeout = 5000
  ): Promise<boolean> {
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      const state = await macOSAdapter.getAccessibilityTree();

      for (const elem of state.elements.values()) {
        if (selector.role && elem.role !== selector.role) continue;
        if (selector.label && !elem.label?.includes(selector.label)) continue;
        return true;
      }

      await this.delay(50);
    }

    return false;
  }

  async waitForWindow(
    titlePattern: string,
    timeout = 5000
  ): Promise<boolean> {
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      const app = await this.getCurrentApp();
      if (!app) continue;

      const windows = await macOSAdapter.getWindowsForApp(app.bundleId);
      if (windows.some((w) => w.title.includes(titlePattern))) {
        return true;
      }

      await this.delay(50);
    }

    return false;
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private generateTraceId(): string {
    return `oscar_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  isRunning(): boolean {
    return this.running;
  }

  getScreenPipeline(): TripleBufferPipeline {
    return this.screenPipeline;
  }

  getObservatory(): ScreenGraphObservatory | null {
    return this.observatory;
  }

  getAdapter() {
    return macOSAdapter;
  }

  getResolver() {
    return actionResolver;
  }

  async observeGraph(): Promise<GraphObservationResult | null> {
    if (!this.observatory) {
      console.warn("[Oscar] Observatory not enabled");
      return null;
    }
    return this.observatory.observe();
  }

  queryGraph(): QueryBuilder | null {
    if (!this.observatory) {
      console.warn("[Oscar] Observatory not enabled");
      return null;
    }
    return this.observatory.query();
  }

  async findElementByLabel(label: string): Promise<UIElement | null> {
    const builder = this.queryGraph();
    if (!builder) return null;
    const result = builder.withLabel(label).limit(1).execute();
    return result[0] || null;
  }

  async findElementByRole(role: string): Promise<UIElement[]> {
    const builder = this.queryGraph();
    if (!builder) return [];
    return builder.withRole(role).execute();
  }

  async findClickableElements(): Promise<UIElement[]> {
    const builder = this.queryGraph();
    if (!builder) return [];
    return builder.clickable().execute();
  }

  async getRegions(): Promise<import("./types.js").Region[]> {
    const builder = this.queryGraph();
    if (!builder) return [];
    return builder.getRegions();
  }

  async getRegionAt(x: number, y: number): Promise<import("./types.js").Region | null> {
    const builder = this.queryGraph();
    if (!builder) return null;
    return builder.getRegionAt(x, y);
  }

  async getElementAtPoint(x: number, y: number): Promise<UIElement[]> {
    const builder = this.queryGraph();
    if (!builder) return [];
    return builder.atPoint(x, y);
  }

  async executeIntent(
    intent: string,
    options?: {
      maxRecoveryAttempts?: number;
      enableCheckpoints?: boolean;
      context?: {
        screenGraph?: ScreenGraph;
        currentApp?: AppInfo;
        availableElements?: UIElement[];
      };
    }
  ): Promise<{
    success: boolean;
    parsedIntent?: ParsedIntent;
    plan?: import("./intent/decomposer.js").ExecutionPlan;
    validation?: import("./intent/dag-validator.js").ValidationResult;
    stepsExecuted?: number;
    error?: string;
    recoveryAttempts?: number;
  }> {
    const parsedIntent = this.intentParser.parse(intent);

    if (parsedIntent.confidence < 0.3) {
      return {
        success: false,
        parsedIntent,
        error: `Low confidence (${parsedIntent.confidence}) for intent parsing`,
      };
    }

    const screenGraph = options?.context?.screenGraph || (this.observatory ? (await this.observatory.observe()).graph : undefined);
    const currentApp = options?.context?.currentApp || (await this.getCurrentApp()) || undefined;

    const plan = this.intentDecomposer.decompose(intent, {
      screenGraph,
      currentApp,
      availableElements: options?.context?.availableElements,
    });

    const validation = this.dagValidator.validate(plan);

    if (!validation.valid) {
      return {
        success: false,
        parsedIntent,
        plan,
        validation,
        error: `Plan validation failed: ${validation.errors.map((e) => e.message).join(", ")}`,
      };
    }

    const maxRecovery = options?.maxRecoveryAttempts ?? 3;
    const enableCheckpoints = options?.enableCheckpoints ?? true;
    let recoveryAttempts = 0;
    let stepsExecuted = 0;

    if (enableCheckpoints) {
      this.checkpointManager.create(plan, 0, {
        screenGraph,
        activeApps: currentApp ? [currentApp] : [],
      });
    }

    for (let i = 0; i < plan.steps.length; i++) {
      const step = plan.steps[i];

      if (step.dependsOn.length > 0 && i > 0) {
        const depsCompleted = plan.steps
          .slice(0, i)
          .every((s) => plan.steps.indexOf(s) < i && s.verification);
        if (!depsCompleted) {
          continue;
        }
      }

      const canonicalAction = this.stepToCanonicalAction(step);

      let stepSuccess = false;
      let stepError: string | undefined;

      for (let attempt = 0; attempt < maxRecovery; attempt++) {
        const result = await macOSAdapter.executeAction(canonicalAction);

        if (result.success) {
          stepSuccess = true;
          break;
        }

        stepError = result.error;
        recoveryAttempts++;

        if (attempt < maxRecovery - 1) {
          const failureCtx: FailureContext = {
            type: result.error?.includes("element") ? "element_not_found" : "action_failed",
            step,
            action: canonicalAction,
            error: result.error,
            alternatives: step.alternatives,
            attempts: attempt + 1,
            screenGraph,
          };

          const recoveryResult = await this.recoveryController.handleFailure(failureCtx, {
            plan,
            currentStepIndex: i,
            screenGraph,
            activeApps: currentApp ? [currentApp] : [],
            stepHistory: [],
            recoveryCount: recoveryAttempts,
          });

          if (!recoveryResult.success || recoveryResult.requiresUser) {
            return {
              success: false,
              parsedIntent,
              plan,
              validation,
              stepsExecuted: i,
              error: recoveryResult.message || stepError,
              recoveryAttempts,
            };
          }

          if (recoveryResult.replacementAction) {
            Object.assign(canonicalAction, recoveryResult.replacementAction);
          }
        }
      }

      stepsExecuted++;

      if (enableCheckpoints) {
        this.checkpointManager.create(plan, i + 1, {
          screenGraph,
          activeApps: currentApp ? [currentApp] : [],
        });
      }

      if (!stepSuccess) {
        return {
          success: false,
          parsedIntent,
          plan,
          validation,
          stepsExecuted,
          error: stepError || `Step ${step.id} failed after ${maxRecovery} attempts`,
          recoveryAttempts,
        };
      }
    }

    return {
      success: true,
      parsedIntent,
      plan,
      validation,
      stepsExecuted,
      recoveryAttempts,
    };
  }

  private stepToCanonicalAction(step: DecomposedStep): CanonicalAction {
    return {
      type: (step.action.split(".")[0] || "click") as CanonicalAction["type"],
      target: step.target || {},
      params: step.params,
      timestamp: Date.now(),
      traceId: `oscar_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    };
  }

  getIntentParser(): IntentParser {
    return this.intentParser;
  }

  getIntentDecomposer(): IntentDecomposer {
    return this.intentDecomposer;
  }

  getDAGValidator(): DAGValidator {
    return this.dagValidator;
  }

  getRecoveryController(): RecoveryController {
    return this.recoveryController;
  }

  getCheckpointManager(): CheckpointManager {
    return this.checkpointManager;
  }

  async restoreFromCheckpoint(checkpointId?: string): Promise<boolean> {
    const targetId = checkpointId || this.checkpointManager.getLatest()?.id;
    if (!targetId) return false;

    return this.checkpointManager.restoreAndApply(targetId, {
      restoreScreen: async (_graph) => {
      },
      restoreApp: async (_bundleId, _state) => {
      },
    });
  }

  getMemory(): VisualMemoryNetwork {
    return this.memory;
  }

  async recordExperience(
    actions: CanonicalAction[],
    outcome: "success" | "partial" | "failed",
    appBundleId: string,
    error?: string
  ): Promise<void> {
    const elements = await this.getCurrentElements();
    await this.memory.learnFromExperience(elements, actions, outcome, appBundleId, error);
  }

  async getLearnedAffordances(appBundleId: string): Promise<Affordance[]> {
    return this.memory.queryAffordances({ app: appBundleId });
  }

  async findSkillsForTask(taskName: string, appBundleId?: string): Promise<Skill[]> {
    return this.memory.findSkillsForTask(taskName, appBundleId);
  }

  async getRecommendations(appBundleId: string, actionType?: string): Promise<{
    affordances: Affordance[];
    skills: Skill[];
  }> {
    const result = await this.memory.getRecommendations(appBundleId, actionType);
    return {
      affordances: result.affordances,
      skills: result.skills,
    };
  }

  async transferKnowledgeToNewApp(
    targetApp: string,
    targetElement: UIElement,
    actionType?: string
  ): Promise<{ affordance: Affordance; confidence: number }[]> {
    const candidates = await this.memory.transferKnowledge(targetApp, targetElement, actionType);
    return candidates.map((c) => ({
      affordance: c.affordance,
      confidence: c.transferabilityScore,
    }));
  }

  startSkillRecording(app: { bundleId: string; name: string }, name?: string): string {
    return this.memory.startRecording(app, name);
  }

  async finishSkillRecording(recordingId: string): Promise<Skill | null> {
    const recording = await this.memory.finishRecording(recordingId);
    if (!recording) return null;
    const result = await this.memory.createSkillFromRecording(recording);
    return result.skill;
  }

  cancelSkillRecording(recordingId: string): void {
    this.memory.cancelRecording(recordingId);
  }

  async getMemoryStats(): Promise<{
    episodes: { total: number; byOutcome: Record<string, number> };
    affordances: { total: number; avgSuccessRate: number };
    skills: { total: number; avgSuccessRate: number; totalUsage: number };
  }> {
    const stats = await this.memory.getStats();
    return {
      episodes: { total: stats.episodes.total, byOutcome: stats.episodes.byOutcome },
      affordances: { total: stats.affordances.total, avgSuccessRate: stats.affordances.avgSuccessRate },
      skills: { total: stats.skills.total, avgSuccessRate: stats.skills.avgSuccessRate, totalUsage: stats.skills.totalUsage },
    };
  }

  private async getCurrentElements(): Promise<UIElement[]> {
    if (!this.observatory) return [];
    const result = await this.observatory.observe();
    return result.elements;
  }

  async startCognition(): Promise<void> {
    await this.cognition.start();
  }

  stopCognition(): void {
    this.cognition.stop();
  }

  getCognition(): CognitionEngine {
    return this.cognition;
  }

  async getCognitiveState(): Promise<{
    running: boolean;
    observationCount: number;
    reflectionCount: number;
  }> {
    return this.cognition.getCognitiveState();
  }

  async recordCognitiveEpisode(
    actions: CanonicalAction[],
    outcome: "success" | "partial" | "failed",
    app: string,
    error?: string
  ): Promise<void> {
    this.cognition.recordEpisode({
      id: `ep_${Date.now()}`,
      timestamp: Date.now(),
      actions,
      outcome,
      app,
      error,
    });
  }

  async learnFromFailure(
    episodeId: string,
    action: CanonicalAction
  ): Promise<{ success: boolean; message: string }> {
    const episode = {
      id: episodeId,
      timestamp: Date.now(),
      actions: [action],
      outcome: "failed" as const,
      app: action.target?.appBundleId || "unknown",
    };

    const result = await this.cognition.learnFromFailure(episode, action);
    return {
      success: !!result.analysis,
      message: result.analysis?.diagnosis.hypothesis || "Analyzed",
    };
  }

  async getProactiveSuggestions(): Promise<{
    suggestions: { type: string; message: string; confidence: number }[];
  }> {
    const currentApp = await this.getCurrentApp();
    const suggestions = await this.cognition.getSuggestions({
      currentApp: currentApp?.bundleId || null,
      recentActions: [],
    });

    return {
      suggestions: suggestions.map((s) => ({
        type: s.type,
        message: s.message,
        confidence: s.confidence,
      })),
    };
  }

  async getAnomalyAlerts(limit?: number): Promise<{
    alerts: { severity: string; message: string; acknowledged: boolean }[];
  }> {
    const alerts = this.cognition.getAlerts(false, limit);
    return {
      alerts: alerts.map((a) => ({
        severity: a.severity,
        message: a.message,
        acknowledged: a.acknowledged,
      })),
    };
  }
}

export const oscar = new Oscar();

export default Oscar;

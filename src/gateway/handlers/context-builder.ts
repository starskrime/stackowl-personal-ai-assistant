/**
 * StackOwl — Engine Context Builder (Thin Adapter over ContextPipeline)
 *
 * Task 19: Replaces the 762-line monolithic builder with a thin adapter that
 * delegates to the ContextPipeline. When `ctx.contextPipeline` is wired in
 * (Task 20), the pipeline runs and its output lands in `memoryContext`.
 * Until then, an empty `memoryContext` is returned so existing callers are
 * not broken.
 */

import type { Session } from "../../memory/store.js";
import type { GatewayContext, GatewayCallbacks } from "../types.js";
import type { EngineContext } from "../../engine/runtime.js";
import type { MicroLearner } from "../../learning/micro-learner.js";
import type { SkillContextInjector } from "../../skills/injector.js";
import type { AttemptLog } from "../../memory/attempt-log.js";
import type { UserMentalModel } from "../../cognition/user-mental-model.js";
import { computeTriage } from "../../context/triage.js";
import { resolveUserId } from "../../context/utils.js";
import { log } from "../../logger.js";

export class ContextBuilder {
  constructor(
    private ctx: GatewayContext,
    // Retained for API compatibility with core.ts — unused in thin adapter path
    _microLearner: MicroLearner | null,
    _skillInjector: SkillContextInjector | null,
    _userMentalModel: UserMentalModel | null = null,
  ) {}

  async build(
    session: Session,
    callbacks: GatewayCallbacks,
    skillsContext: string = "",
    isolatedTask: boolean = false,
    attemptLog?: AttemptLog,
    channelId?: string,
    userId?: string,
    continuityResult?:
      | import("../../cognition/continuity-engine.js").ContinuityResult
      | null,
  ): Promise<EngineContext> {
    const pipeline = this.ctx.contextPipeline;

    if (!pipeline) {
      log.engine.debug("[ContextBuilder] contextPipeline not set — returning base context");
      return {
        ...this.baseContext(session, callbacks, isolatedTask, attemptLog, channelId, userId),
        skillsContext: skillsContext || undefined,
      };
    }

    const effectiveUserId = resolveUserId(userId, session.id);

    const hasActiveItems =
      (this.ctx.intentStateMachine?.getActive().length ?? 0) > 0 ||
      (this.ctx.commitmentTracker?.getPending().length ?? 0) > 0 ||
      (this.ctx.goalGraph?.getStale(1).length ?? 0) > 0;

    const triage = computeTriage({
      userMessage: session.messages?.at(-1)?.content ?? "",
      sessionDepth: session.messages?.filter((m) => m.role === "user").length ?? 0,
      continuityClass: continuityResult?.classification ?? null,
      userId: effectiveUserId,
      sessionId: session.id,
      hasActiveItems,
    });

    const deps = {
      intelligenceRouter: this.ctx.intelligence,
      pelletStore: this.ctx.pelletStore,
      memoryBus: this.ctx.memoryBus,
      sessionStore: this.ctx.sessionStore,
      eventBus: this.ctx.eventBus,
      config: this.ctx.config,
      knowledgeGraph: this.ctx.knowledgeGraph,
      predictiveQueue: this.ctx.predictiveQueue,
    };

    const { output, trace } = await pipeline.run(
      {
        session,
        callbacks,
        channelId,
        userId,
        continuityResult: continuityResult ?? null,
        digest: null,
        deps,
      },
      triage,
      { globalTokenCeiling: (this.ctx.config as any).context?.globalTokenCeiling },
    );

    log.engine.debug(
      `[ContextBuilder] pipeline trace: ${trace.length} layers, ${trace.filter((e) => e.fired).length} fired`,
    );

    return {
      ...this.baseContext(session, callbacks, isolatedTask, attemptLog, channelId, userId),
      memoryContext: output || undefined,
      skillsContext: skillsContext || undefined,
    };
  }

  private baseContext(
    session: Session,
    callbacks: GatewayCallbacks,
    isolatedTask: boolean,
    attemptLog?: AttemptLog,
    channelId?: string,
    userId?: string,
  ): EngineContext {
    return {
      provider: this.ctx.provider,
      owl: this.ctx.owl,
      sessionHistory: session.messages,
      config: this.ctx.config,
      toolRegistry: this.ctx.toolRegistry,
      pelletStore: this.ctx.pelletStore,
      capabilityLedger: this.ctx.capabilityLedger,
      cwd: this.ctx.cwd,
      skillsRegistry: this.ctx.skillsLoader?.getRegistry(),
      isolatedTask,
      attemptLog,
      onProgress: callbacks.onProgress,
      onStreamEvent: callbacks.onStreamEvent,
      pendingFiles: [],
      channelName: channelId,
      providerRegistry: this.ctx.providerRegistry,
      memorySearcher: this.ctx.memorySearcher,
      echoChamberDetector: this.ctx.echoChamberDetector,
      journalGenerator: this.ctx.journalGenerator,
      questManager: this.ctx.questManager,
      capsuleManager: this.ctx.capsuleManager,
      innerLife: this.ctx.innerLife,
      factStore: this.ctx.factStore,
      memoryRepo: (this.ctx as any).memoryRepo,
      episodicMemory: (this.ctx as any).episodicMemory,
      unifiedMemory: (this.ctx as any).unifiedMemory,
      userId,
      db: this.ctx.db,
      sessionId: session.id,
      classifier: this.ctx.blockingClassifier,
      puppeteer: this.ctx.puppeteer,
      camofox: this.ctx.camofox,
      tavilyApiKey: this.ctx.tavilyApiKey,
      relationshipContext: this.ctx.relationshipContext,
      intelligence: this.ctx.intelligence,
    };
  }
}

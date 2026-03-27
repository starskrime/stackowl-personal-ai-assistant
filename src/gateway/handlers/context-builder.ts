/**
 * StackOwl — Engine Context Builder
 *
 * Extracted from gateway/core.ts. Assembles the EngineContext
 * that the ReAct loop needs — merging ambient signals, knowledge,
 * predictions, collaboration state, user profile, etc.
 */

import type { Session } from "../../memory/store.js";
import type { GatewayContext } from "../types.js";
import type { GatewayCallbacks } from "../types.js";
import type { EngineContext } from "../../engine/runtime.js";
import type { MicroLearner } from "../../learning/micro-learner.js";
import type { SkillContextInjector } from "../../skills/injector.js";
import type { AttemptLog } from "../../memory/attempt-log.js";

export class ContextBuilder {
  constructor(
    private ctx: GatewayContext,
    private microLearner: MicroLearner | null,
    private skillInjector: SkillContextInjector | null,
  ) {}

  async build(
    session: Session,
    callbacks: GatewayCallbacks,
    dynamicSkillsContext: string = "",
    isolatedTask: boolean = false,
    attemptLog?: AttemptLog,
  ): Promise<EngineContext> {
    const preferencesContext =
      this.ctx.preferenceStore?.toContextString() ?? "";

    // Always-include skills
    let skillsContext = "";
    if (this.ctx.skillsLoader) {
      const registry = this.ctx.skillsLoader.getRegistry();
      const alwaysSkills = registry
        .listEnabled()
        .filter((s) => s.metadata.openclaw?.always === true);
      if (alwaysSkills.length > 0) {
        skillsContext =
          "\n## Always-Available Skills\n" +
          alwaysSkills
            .map(
              (s) => `\n<skill name="${s.name}">\n${s.instructions}\n</skill>`,
            )
            .join("\n");
      }
    }

    const finalSkillsContext = skillsContext + dynamicSkillsContext;

    // Ambient context
    let ambientContext = "";
    if (this.ctx.contextMesh) {
      ambientContext = this.ctx.contextMesh.toContextBlock(5);
    }

    // Knowledge graph
    let knowledgeContext = "";
    if (this.ctx.knowledgeReasoner && session.messages.length > 0) {
      const lastUserMsg = [...session.messages]
        .reverse()
        .find((m) => m.role === "user");
      if (lastUserMsg) {
        const nodes = this.ctx.knowledgeGraph?.search(lastUserMsg.content, 3);
        if (nodes && nodes.length > 0) {
          knowledgeContext =
            "\n<knowledge_context>\n" +
            nodes
              .map(
                (n) =>
                  `  <fact domain="${n.domain}" confidence="${n.confidence}">${n.title}: ${n.content}</fact>`,
              )
              .join("\n") +
            "\n</knowledge_context>\n";
        }
      }
    }

    // Predictive queue
    let predictiveContext = "";
    if (this.ctx.predictiveQueue) {
      const ready = this.ctx.predictiveQueue.getReadyTasks();
      if (ready.length > 0) {
        predictiveContext =
          "\n<predicted_tasks>\n" +
          ready
            .map(
              (t) =>
                `  <task confidence="${t.confidence.toFixed(2)}">${t.action}</task>`,
            )
            .join("\n") +
          "\n</predicted_tasks>\n";
      }
    }

    // Collab context
    let collabContext = "";
    if (this.ctx.collabManager) {
      const userSessions = this.ctx.collabManager.getUserSessions(
        session.id.split(":")[1] || session.id,
      );
      if (userSessions.length > 0) {
        collabContext = this.ctx.collabManager.buildCollabContext(
          userSessions[0].id,
        );
      }
    }

    // User profile
    let userProfileContext = "";
    if (this.microLearner) {
      userProfileContext = this.microLearner.toContextString();
    }

    // Inferred preferences (behavioral)
    let inferredPrefsContext = "";
    if (this.ctx.preferenceModel) {
      inferredPrefsContext = this.ctx.preferenceModel.toContextString();
    }

    // Echo chamber awareness
    let echoChamberContext = "";
    if (this.ctx.echoChamberDetector) {
      echoChamberContext = this.ctx.echoChamberDetector.toContextString();
    }

    // Behavioral patches — rules learned from past mistakes (via ReflexionEngine)
    // These are injected so the owl doesn't repeat the same errors.
    let behavioralPatchContext = "";
    if (this.ctx.pelletStore) {
      try {
        const allPellets = await this.ctx.pelletStore.listAll();
        const patches = allPellets
          .filter((p) => p.tags?.includes("behavioral-patch"))
          .slice(0, 5); // Most recent 5 patches
        if (patches.length > 0) {
          behavioralPatchContext =
            "\n<learned_rules>\n" +
            "Rules learned from past mistakes — follow these to avoid repeating errors:\n" +
            patches
              .map((p) => `  <rule>${p.content.slice(0, 300)}</rule>`)
              .join("\n") +
            "\n</learned_rules>\n";
        }
      } catch {
        // Non-fatal — patches are supplementary context
      }
    }

    // Socratic mode
    let socraticContext = "";
    if (this.ctx.socraticEngine) {
      socraticContext = this.ctx.socraticEngine.toContextString(session.id);
    }

    // Active intents
    let intentContext = "";
    if (this.ctx.intentStateMachine) {
      intentContext = this.ctx.intentStateMachine.toContextString();
    }

    // Assistant vs Reactive mode
    const hasActiveItems =
      (this.ctx.intentStateMachine?.getActive().length ?? 0) > 0 ||
      (this.ctx.commitmentTracker?.getPending().length ?? 0) > 0 ||
      (this.ctx.goalGraph?.getStale(1).length ?? 0) > 0;

    const modeDirective = hasActiveItems
      ? `<mode>ASSISTANT</mode>

You have active tasks, commitments, or pending goals. Be proactive:
- Follow up on things you've promised to do
- Anticipate the user's needs based on current tasks
- Don't just answer and stop — think about what else might be helpful
- If you're waiting on something, let the user know`
      : `<mode>REACTIVE</mode>

The user has no active tasks right now. Be concise and helpful:
- Answer directly what they're asking
- Don't add unnecessary fluff
- If you complete a task, confirm completion briefly`;

    // ─── FactStore + EpisodicMemory retrieval ───────────────────
    const userMessage =
      [...session.messages]
        .reverse()
        .find((m) => m.role === "user")?.content ?? "";

    const MEMORY_TIMEOUT = 2_000;
    const withTimeout = <T>(promise: Promise<T>): Promise<T | null> =>
      Promise.race([
        promise,
        new Promise<null>((resolve) => setTimeout(() => resolve(null), MEMORY_TIMEOUT)),
      ]);

    let factContext = "";
    let episodicContext = "";

    if (userMessage) {
      const [factResults, episodeResults] = await Promise.all([
        this.ctx.factStore
          ? withTimeout(
              (async () => this.ctx.factStore!.search(userMessage, undefined, 5))(),
            ).catch(() => null)
          : Promise.resolve(null),
        this.ctx.episodicMemory
          ? withTimeout(
              this.ctx.episodicMemory.search(userMessage, 3),
            ).catch(() => null)
          : Promise.resolve(null),
      ]);

      if (factResults && factResults.length > 0) {
        factContext =
          "\n<remembered_facts>\n" +
          factResults
            .map(
              (f) =>
                `  <fact category="${f.category}" confidence="${f.confidence.toFixed(2)}">${f.fact}</fact>`,
            )
            .join("\n") +
          "\n</remembered_facts>\n";
      }

      if (episodeResults && episodeResults.length > 0) {
        episodicContext =
          "\n<past_episodes>\n" +
          episodeResults
            .map(
              (ep) =>
                `  <episode date="${new Date(ep.date).toLocaleDateString()}" sentiment="${ep.sentiment ?? "neutral"}">${ep.summary}</episode>`,
            )
            .join("\n") +
          "\n</past_episodes>\n";
      }
    }

    // Merge all context signals
    const enrichedMemoryContext = [
      this.ctx.memoryContext ?? "",
      ambientContext,
      knowledgeContext,
      predictiveContext,
      collabContext,
      userProfileContext,
      inferredPrefsContext,
      echoChamberContext,
      behavioralPatchContext,
      socraticContext,
      intentContext,
      factContext,
      episodicContext,
      modeDirective,
    ]
      .filter(Boolean)
      .join("\n");

    return {
      provider: this.ctx.provider,
      owl: this.ctx.owl,
      sessionHistory: session.messages,
      config: this.ctx.config,
      toolRegistry: this.ctx.toolRegistry,
      pelletStore: this.ctx.pelletStore,
      capabilityLedger: this.ctx.capabilityLedger,
      cwd: this.ctx.cwd,
      memoryContext: enrichedMemoryContext || undefined,
      preferencesContext: preferencesContext || undefined,
      skillsContext: finalSkillsContext || undefined,
      skillsRegistry: this.ctx.skillsLoader?.getRegistry(),
      skillTracker: this.skillInjector?.getTracker(),
      isolatedTask,
      attemptLog,
      onProgress: callbacks.onProgress,
      onStreamEvent: callbacks.onStreamEvent,
      sendFile: callbacks.onFile,
      providerRegistry: this.ctx.providerRegistry,
      memorySearcher: this.ctx.memorySearcher,
      echoChamberDetector: this.ctx.echoChamberDetector,
      journalGenerator: this.ctx.journalGenerator,
      questManager: this.ctx.questManager,
      capsuleManager: this.ctx.capsuleManager,
      innerLife: this.ctx.innerLife,
    };
  }
}

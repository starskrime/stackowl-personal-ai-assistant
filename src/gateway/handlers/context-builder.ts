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

  build(
    session: Session,
    callbacks: GatewayCallbacks,
    dynamicSkillsContext: string = "",
    isolatedTask: boolean = false,
    attemptLog?: AttemptLog,
  ): EngineContext {
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
            .map((s) => `\n<skill name="${s.name}">\n${s.instructions}\n</skill>`)
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
      const lastUserMsg = [...session.messages].reverse().find(m => m.role === "user");
      if (lastUserMsg) {
        const nodes = this.ctx.knowledgeGraph?.search(lastUserMsg.content, 3);
        if (nodes && nodes.length > 0) {
          knowledgeContext =
            "\n<knowledge_context>\n" +
            nodes.map(n =>
              `  <fact domain="${n.domain}" confidence="${n.confidence}">${n.title}: ${n.content}</fact>`,
            ).join("\n") +
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
          ready.map(t =>
            `  <task confidence="${t.confidence.toFixed(2)}">${t.action}</task>`,
          ).join("\n") +
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
        collabContext = this.ctx.collabManager.buildCollabContext(userSessions[0].id);
      }
    }

    // User profile
    let userProfileContext = "";
    if (this.microLearner) {
      userProfileContext = this.microLearner.toContextString();
    }

    // Echo chamber awareness
    let echoChamberContext = "";
    if (this.ctx.echoChamberDetector) {
      echoChamberContext = this.ctx.echoChamberDetector.toContextString();
    }

    // Socratic mode
    let socraticContext = "";
    if (this.ctx.socraticEngine) {
      socraticContext = this.ctx.socraticEngine.toContextString(session.id);
    }

    // Merge all context signals
    const enrichedMemoryContext = [
      this.ctx.memoryContext ?? "",
      ambientContext,
      knowledgeContext,
      predictiveContext,
      collabContext,
      userProfileContext,
      echoChamberContext,
      socraticContext,
    ].filter(Boolean).join("\n");

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
    };
  }
}

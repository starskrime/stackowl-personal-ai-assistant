/**
 * StackOwl — Post-Processor
 *
 * Extracted from gateway/core.ts. Runs background tasks after every response:
 * learning, evolution, micro-learning, anticipation, knowledge extraction,
 * pattern analysis, trust chain persistence.
 *
 * Now uses TaskQueue instead of fire-and-forget promises.
 */

import type { ChatMessage } from "../../providers/base.js";
import type { GatewayContext } from "../types.js";
import type { TaskQueue } from "../../queue/task-queue.js";
import type { EventBus } from "../../events/bus.js";
import type { CostTracker } from "../../costs/tracker.js";
import type { MicroLearner } from "../../learning/micro-learner.js";
import type { ProactiveAnticipator } from "../../learning/anticipator.js";
import { log } from "../../logger.js";

export class PostProcessor {
  private messageCount = 0;

  constructor(
    private ctx: GatewayContext,
    private taskQueue: TaskQueue,
    private eventBus: EventBus | null,
    private microLearner: MicroLearner | null,
    private anticipator: ProactiveAnticipator | null,
    private costTracker: CostTracker | null,
  ) {}

  /**
   * Run all post-processing tasks after a response.
   * Tasks are enqueued into the TaskQueue for bounded parallel execution.
   */
  process(
    messages: ChatMessage[],
    sessionId?: string,
    metadata?: {
      channelId?: string;
      userId?: string;
      owlName?: string;
      toolsUsed?: string[];
      usage?: { promptTokens: number; completionTokens: number };
      model?: string;
      provider?: string;
    },
  ): void {
    this.messageCount++;

    // Emit event
    if (this.eventBus && sessionId && metadata) {
      this.eventBus.emit("message:responded", {
        sessionId,
        channelId: metadata.channelId ?? "",
        userId: metadata.userId ?? "",
        content: messages[messages.length - 1]?.content ?? "",
        owlName: metadata.owlName ?? "",
        toolsUsed: metadata.toolsUsed ?? [],
        usage: metadata.usage
          ? { ...metadata.usage, totalTokens: (metadata.usage.promptTokens + metadata.usage.completionTokens) }
          : undefined,
        messages: messages.map(m => ({ role: m.role, content: m.content })),
      });
    }

    // Track costs
    if (this.costTracker && metadata?.usage && metadata?.provider && metadata?.model && sessionId) {
      this.costTracker.record(
        metadata.provider,
        metadata.model,
        metadata.usage.promptTokens,
        metadata.usage.completionTokens,
        sessionId,
        metadata.userId ?? "unknown",
      );
    }

    // Learning engine
    if (this.ctx.learningEngine) {
      this.taskQueue.enqueue(
        "learning",
        () => this.ctx.learningEngine!.processConversation(messages),
      );
    }

    // DNA evolution (every N messages)
    const evolutionInterval = this.ctx.config.owlDna?.evolutionBatchSize ?? 10;
    if (this.messageCount % evolutionInterval === 0 && this.ctx.evolutionEngine) {
      this.taskQueue.enqueue(
        `dna-evolve(${this.ctx.owl.persona.name})`,
        () => this.ctx.evolutionEngine!.evolve(this.ctx.owl.persona.name),
      );
    }

    // Micro-learning (every message, zero LLM cost)
    if (this.microLearner) {
      const lastUserMsg = [...messages].reverse().find(m => m.role === "user");
      if (lastUserMsg) {
        const lastAssistantMsg = [...messages].reverse().find(m => m.role === "assistant");
        const toolsUsed: string[] = [];
        if (lastAssistantMsg?.content) {
          const toolMatches = lastAssistantMsg.content.match(
            /\btool[_\s]?(?:call|use|execute)[:\s]+["']?(\w+)/gi,
          );
          if (toolMatches) {
            for (const match of toolMatches) {
              const name = match.replace(/.*?["']?(\w+)["']?$/, "$1");
              if (name) toolsUsed.push(name);
            }
          }
        }
        this.microLearner.processMessage(
          lastUserMsg.content,
          toolsUsed.length > 0 ? toolsUsed : undefined,
        );
      }

      if (this.messageCount % 5 === 0) {
        this.taskQueue.enqueue("micro-learner-save", () => this.microLearner!.save());
      }
    }

    // Proactive anticipation (every 20 messages)
    if (this.anticipator && this.messageCount % 20 === 0) {
      const existingSkills = this.ctx.skillsLoader?.getRegistry()?.listEnabled() ?? [];
      this.taskQueue.enqueue("anticipation", async () => {
        const anticipations = await this.anticipator!.anticipate(existingSkills);
        if (anticipations.length > 0) {
          log.engine.info(
            `[Anticipator] ${anticipations.length} anticipations: ` +
            anticipations.map(a => `${a.capability} (${(a.confidence * 100).toFixed(0)}%)`).join(", "),
          );
        }
      });
    }

    // Timeline auto-snapshot (every 10 messages)
    if (this.ctx.timelineManager && sessionId) {
      const snapshot = this.ctx.timelineManager.autoSnapshot(
        sessionId, messages, this.ctx.owl.persona.name,
      );
      if (snapshot) {
        this.taskQueue.enqueue("timeline-snapshot", () => this.ctx.timelineManager!.save());
      }
    }

    // Knowledge extraction (every 5 messages)
    if (this.ctx.knowledgeReasoner && messages.length > 0 && this.messageCount % 5 === 0) {
      this.taskQueue.enqueue("knowledge-extract", async () => {
        await this.ctx.knowledgeReasoner!.extractFromConversation(messages);
        await this.ctx.knowledgeGraph?.save();
      });
    }

    // Pattern recording
    if (this.ctx.patternAnalyzer) {
      const lastUserMsg = [...messages].reverse().find(m => m.role === "user");
      if (lastUserMsg) {
        this.ctx.patternAnalyzer.recordAction(lastUserMsg.content.slice(0, 100), []);
      }

      if (this.microLearner && this.messageCount % 15 === 0) {
        const profile = this.microLearner.getProfile();
        this.ctx.patternAnalyzer.enrichFromProfile(profile);
      }
    }

    // Periodic persistence (every 10 messages)
    if (this.messageCount % 10 === 0) {
      if (this.ctx.patternAnalyzer) {
        this.taskQueue.enqueue("pattern-save", () => this.ctx.patternAnalyzer!.save());
      }
      if (this.ctx.trustChain) {
        this.taskQueue.enqueue("trust-save", () => this.ctx.trustChain!.save());
      }
      if (this.ctx.predictiveQueue) {
        this.taskQueue.enqueue("predictive-prep", async () => {
          const newTasks = await this.ctx.predictiveQueue!.generatePredictions();
          for (const task of newTasks) {
            await this.ctx.predictiveQueue!.prepareTask(task.id);
          }
        });
      }
    }
  }

  getMessageCount(): number {
    return this.messageCount;
  }
}

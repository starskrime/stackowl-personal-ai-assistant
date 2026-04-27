/**
 * StackOwl — Event-Based Pellet Generator
 *
 * Generates pellets from significant events (not just Parliament sessions).
 * Subscribes to the event bus and evaluates significance before creating pellets.
 */

import type { EventBus } from "../events/bus.js";
import type { PelletStore, Pellet } from "./store.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import { PelletGenerator } from "./generator.js";
import { log } from "../logger.js";

// ─── Significance Criteria ─────────────────────────────────────

interface SignificanceConfig {
  minMessagesForSession: number;
  maxGapAgeDays: number;
  dedupSimilarityThreshold: number;
}

// ─── Event Payload Extractors ────────────────────────────────────

interface ExtractedEventData {
  sourceName: string;
  sourceMaterial: string;
  tags: string[];
  owlsInvolved: string[];
}

function extractSessionEndedData(payload: {
  sessionId: string;
  messageCount: number;
}): ExtractedEventData | null {
  if (payload.messageCount < 3) return null;
  return {
    sourceName: `session:${payload.sessionId}`,
    sourceMaterial: `Session ended with ${payload.messageCount} messages. Key topics and decisions to capture.`,
    tags: ["session-summary"],
    owlsInvolved: [],
  };
}

function extractToolResultData(payload: {
  name: string;
  success: boolean;
  result: string;
  sessionId: string;
  durationMs: number;
}): ExtractedEventData | null {
  if (payload.success) return null;
  return {
    sourceName: `tool:${payload.name}`,
    sourceMaterial: `Tool "${payload.name}" failed after ${payload.durationMs}ms. Error: ${payload.result.slice(0, 500)}. Lesson to capture.`,
    tags: ["lesson-learned", "error-recovery"],
    owlsInvolved: [],
  };
}

function extractCapabilityGapData(payload: {
  description: string;
  toolName?: string;
  sessionId: string;
}): ExtractedEventData {
  return {
    sourceName: `capability-gap:${payload.sessionId}`,
    sourceMaterial: `Capability gap identified: ${payload.description}${payload.toolName ? ` (tool: ${payload.toolName})` : ""}. Analysis and potential solution approach.`,
    tags: ["capability-gap", "gap-analysis"],
    owlsInvolved: [],
  };
}

function extractEvolutionData(payload: {
  owlName: string;
  generation: number;
}): ExtractedEventData {
  return {
    sourceName: `evolution:${payload.owlName}:gen${payload.generation}`,
    sourceMaterial: `Owl "${payload.owlName}" evolved to generation ${payload.generation}. Key changes and new capabilities captured.`,
    tags: ["evolution-insight", "owl-evolution"],
    owlsInvolved: [payload.owlName],
  };
}

function extractDecisionData(payload: {
  sessionId: string;
  channelId: string;
  userId: string;
  content: string;
  owlName: string;
  toolsUsed: string[];
}): ExtractedEventData | null {
  const hasDecision = payload.content.includes("decision") ||
    payload.content.includes("decided") ||
    payload.content.includes("conclusion") ||
    payload.content.includes("recommendation");
  if (!hasDecision || !payload.toolsUsed?.length) return null;
  return {
    sourceName: `decision:${payload.sessionId}`,
    sourceMaterial: `Owl "${payload.owlName}" made a decision using tools [${payload.toolsUsed.join(", ")}]. Decision: ${payload.content.slice(0, 1000)}.`,
    tags: ["decision-capture", "tool-driven"],
    owlsInvolved: [payload.owlName],
  };
}

// ─── Event-Based Pellet Generator ───────────────────────────────

export class EventBasedPelletGenerator {
  private generator: PelletGenerator;
  private recentErrors = new Set<string>();

  private handleSessionEndedBound = this.handleSessionEnded.bind(this);
  private handleToolResultBound = this.handleToolResult.bind(this);
  private handleCapabilityGapBound = this.handleCapabilityGap.bind(this);
  private handleEvolutionBound = this.handleEvolution.bind(this);
  private handleMessageRespondedBound = this.handleMessageResponded.bind(this);

  constructor(
    private eventBus: EventBus,
    private pelletStore: PelletStore,
    private provider: ModelProvider,
    private owl: OwlInstance,
    private config: StackOwlConfig,
    significanceConfig?: Partial<SignificanceConfig>,
  ) {
    this.generator = new PelletGenerator();
    const _significanceConfig = {
      minMessagesForSession: 3,
      maxGapAgeDays: 30,
      dedupSimilarityThreshold: 0.85,
      ...significanceConfig,
    };
    void _significanceConfig;
  }

  /**
   * Subscribe to relevant events on the event bus.
   */
  subscribe(): void {
    this.eventBus.on("session:ended", this.handleSessionEndedBound);
    this.eventBus.on("tool:result", this.handleToolResultBound);
    this.eventBus.on("capability:gap", this.handleCapabilityGapBound);
    this.eventBus.on("evolution:triggered", this.handleEvolutionBound);
    this.eventBus.on("message:responded", this.handleMessageRespondedBound);

    log.engine.info(
      "[EventBasedPelletGenerator] Subscribed to pellet-triggering events",
    );
  }

  /**
   * Unsubscribe from all events.
   */
  unsubscribe(): void {
    this.eventBus.off("session:ended", this.handleSessionEndedBound);
    this.eventBus.off("tool:result", this.handleToolResultBound);
    this.eventBus.off("capability:gap", this.handleCapabilityGapBound);
    this.eventBus.off("evolution:triggered", this.handleEvolutionBound);
    this.eventBus.off("message:responded", this.handleMessageRespondedBound);

    log.engine.info(
      "[EventBasedPelletGenerator] Unsubscribed from pellet-triggering events",
    );
  }

  private async handleSessionEnded(payload: {
    sessionId: string;
    messageCount: number;
  }): Promise<void> {
    const data = extractSessionEndedData(payload);
    if (!data) return;

    log.engine.info(
      `[EventBasedPelletGenerator] Session ended (${payload.messageCount} msgs) — generating pellet`,
    );

    await this.generateFromEvent(data, "session-summary");
  }

  private async handleToolResult(payload: {
    name: string;
    success: boolean;
    result: string;
    sessionId: string;
    durationMs: number;
  }): Promise<void> {
    if (payload.success) return;

    const errorKey = `${payload.name}:${payload.result.slice(0, 100)}`;
    if (this.recentErrors.has(errorKey)) return;
    this.recentErrors.add(errorKey);

    if (this.recentErrors.size > 100) {
      const first = this.recentErrors.values().next().value;
      if (first !== undefined) this.recentErrors.delete(first);
    }

    const data = extractToolResultData(payload);
    if (!data) return;

    log.engine.info(
      `[EventBasedPelletGenerator] Tool "${payload.name}" failed — generating pellet`,
    );

    await this.generateFromEvent(data, "lesson-learned");
  }

  private async handleCapabilityGap(payload: {
    description: string;
    toolName?: string;
    sessionId: string;
  }): Promise<void> {
    const data = extractCapabilityGapData(payload);

    log.engine.info(
      `[EventBasedPelletGenerator] Capability gap detected — generating pellet`,
    );

    await this.generateFromEvent(data, "gap-analysis");
  }

  private async handleEvolution(payload: {
    owlName: string;
    generation: number;
  }): Promise<void> {
    const data = extractEvolutionData(payload);

    log.engine.info(
      `[EventBasedPelletGenerator] Evolution triggered for "${payload.owlName}" — generating pellet`,
    );

    await this.generateFromEvent(data, "evolution-insight");
  }

  private async handleMessageResponded(payload: {
    sessionId: string;
    channelId: string;
    userId: string;
    content: string;
    owlName: string;
    toolsUsed: string[];
  }): Promise<void> {
    const data = extractDecisionData(payload);
    if (!data) return;

    log.engine.info(
      `[EventBasedPelletGenerator] Decision detected — generating pellet`,
    );

    await this.generateFromEvent(data, "decision-capture");
  }

  /**
   * Generate a pellet from extracted event data.
   */
  async generateFromEvent(
    data: ExtractedEventData,
    _pelletType: string,
  ): Promise<Pellet | null> {
    try {
      const pellet = await this.generator.generate(
        data.sourceMaterial,
        data.sourceName,
        {
          provider: this.provider,
          owl: this.owl,
          config: this.config,
        },
      );

      pellet.tags = [...new Set([...pellet.tags, ...data.tags])];
      if (data.owlsInvolved.length > 0) {
        pellet.owls = [...new Set([...pellet.owls, ...data.owlsInvolved])];
      }

      const result = await this.pelletStore.save(pellet);

      log.engine.info(
        `[EventBasedPelletGenerator] Pellet "${pellet.id}" saved (verdict: ${result.verdict})`,
      );

      return pellet;
    } catch (err) {
      log.engine.warn(
        `[EventBasedPelletGenerator] Failed to generate pellet: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }
}
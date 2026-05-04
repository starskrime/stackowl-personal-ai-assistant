/**
 * StackOwl — Element 15 — MemoryWriter
 *
 * Goal-conditioned ingestion pipeline.
 *
 * Pipeline:
 *   1. Trivial-turn short-circuit (length-only guard, no keyword classification).
 *   2. Classify via IntelligenceRouter cheap-tier — extract zero-or-more
 *      memory records as JSON.
 *   3. (Task 13) contradiction check + ADD/UPDATE/DELETE/NOOP reconciler.
 *   4. Persist via MemoryRepository.insertBatch (emits memory:written events).
 *
 * No hardcoded keyword arrays anywhere — content classification is delegated
 * to the LLM.
 */

import { randomUUID } from "node:crypto";
import type { MemoryRepository, MemoryInsert } from "./repository.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ProviderRegistry } from "../providers/registry.js";

export interface WriterTurn {
  sessionId: string;
  turnId: string;
  channel: string;
  userMessage: string;
  assistantResponse: string;
  verdict: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL";
  goalId: string | null;
  subGoalId: string | null;
}

export interface IngestResult {
  skipped: boolean;
  reason?: string;
  written?: number;
  invalidated?: number;
}

export interface WriterDeps {
  repo: MemoryRepository;
  bus: GatewayEventBus;
  router: IntelligenceRouter;
  providerRegistry: ProviderRegistry;
}

interface ExtractionResult {
  extractions: Array<{
    kind: "semantic" | "episodic" | "working" | "procedural";
    content: string;
    importance: number;
  }>;
}

const MIN_MESSAGE_LEN_NEUTRAL = 12;

export class MemoryWriter {
  constructor(private readonly deps: WriterDeps) {}

  async ingest(turn: WriterTurn): Promise<IngestResult> {
    if (this.isTrivial(turn)) {
      return { skipped: true, reason: "trivial-turn" };
    }

    const extraction = await this.classify(turn);
    if (!extraction) {
      return { skipped: true, reason: "classify-failed" };
    }
    if (!extraction.extractions || extraction.extractions.length === 0) {
      return { skipped: true, reason: "empty-extraction" };
    }

    const records: MemoryInsert[] = extraction.extractions.map((e) => ({
      id: randomUUID(),
      kind: e.kind,
      content: e.content,
      importance: clamp01(e.importance),
      goal_id: turn.goalId ?? undefined,
      subgoal_id: turn.subGoalId ?? undefined,
      verdict: turn.verdict,
      source_turn_id: turn.turnId,
      source_channel: turn.channel,
    }));

    this.deps.repo.insertBatch(records);
    return { skipped: false, written: records.length };
  }

  private async classify(turn: WriterTurn): Promise<ExtractionResult | null> {
    try {
      const resolved = this.deps.router.resolve("classification");
      const provider = this.deps.providerRegistry.get(resolved.provider);
      const response = await provider.chat(
        [
          { role: "system", content: "You are a precise JSON-only memory extractor." },
          { role: "user", content: this.buildClassifyPrompt(turn) },
        ],
        resolved.model,
        { temperature: 0.2, maxTokens: 600 },
      );
      const text = response.content.trim();
      const parsed = JSON.parse(text) as ExtractionResult;
      if (!parsed || !Array.isArray(parsed.extractions)) {
        throw new Error("classifier response missing extractions array");
      }
      return parsed;
    } catch (err) {
      this.deps.bus.emit({
        type: "memory:classify_failed",
        turnId: turn.turnId,
        reason: (err as Error).message,
      });
      return null;
    }
  }

  private buildClassifyPrompt(turn: WriterTurn): string {
    return `You are a memory extractor. Read the turn and emit zero or more memory records as JSON.

User message: ${JSON.stringify(turn.userMessage)}
Assistant response: ${JSON.stringify(turn.assistantResponse)}
Active goal id: ${turn.goalId ?? "none"}
Verdict: ${turn.verdict}

Respond with strict JSON of shape:
{ "extractions": [ { "kind": "semantic"|"episodic"|"working"|"procedural", "content": "...", "importance": 0.0-1.0 } ] }

Rules:
- "semantic": user preferences, durable facts about the user/world.
- "episodic": time-bound events ("user worked on X today").
- "working": ephemeral active-task state, valid for hours not days.
- "procedural": learned procedures / how-to knowledge.
- Return { "extractions": [] } if nothing worth remembering.
- Importance 0.8+ only for facts the user would correct you about.

JSON only, no prose.`;
  }

  private isTrivial(turn: WriterTurn): boolean {
    const msg = (turn.userMessage ?? "").trim();
    if (msg.length === 0) return true;
    if (turn.verdict === "NEUTRAL" && msg.length < MIN_MESSAGE_LEN_NEUTRAL) return true;
    return false;
  }
}

function clamp01(v: number): number {
  if (Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

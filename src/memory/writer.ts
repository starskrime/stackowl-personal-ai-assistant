/**
 * StackOwl — Element 15 — MemoryWriter
 *
 * Goal-conditioned ingestion pipeline.
 *
 * Pipeline:
 *   1. Trivial-turn short-circuit (length-only).
 *   2. Classify via IntelligenceRouter cheap-tier.
 *   3. Reconcile each extraction against top-K similar existing memories
 *      (ADD / UPDATE / DELETE / NOOP) — invalidations applied first, then inserts.
 *   4. Persist via MemoryRepository.insertBatch.
 *
 * Bus listeners:
 *   - engine:turn_complete → expireWorkingMemories(24h)
 *
 * Helpers:
 *   - recordReflexive(observation) — engine self-observations as `reflexive` kind.
 */

import { randomUUID } from "node:crypto";
import type { MemoryRepository, MemoryInsert, MemoryKind } from "./repository.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { IntelligenceRouter, ResolvedModel } from "../intelligence/router.js";
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

interface Extraction {
  kind: MemoryKind;
  content: string;
  importance: number;
}

interface ExtractionResult {
  extractions: Extraction[];
}

interface ReconcileDecision {
  action: "ADD" | "UPDATE" | "DELETE" | "NOOP";
  target_id?: string;
  reason?: string;
}

const MIN_MESSAGE_LEN_NEUTRAL = 12;
const WORKING_MEMORY_TTL_HOURS = 24;

export class MemoryWriter {
  constructor(private readonly deps: WriterDeps) {}

  attachBusListeners(): void {
    this.deps.bus.on("engine:turn_complete", () => {
      try {
        this.deps.repo.expireWorkingMemories(WORKING_MEMORY_TTL_HOURS);
      } catch (err) {
        this.deps.bus.emit({ type: "memory:write_failed", reason: (err as Error).message });
      }
    });
  }

  async recordReflexive(input: {
    sessionId: string;
    observation: string;
    importance?: number;
    goalId?: string;
  }): Promise<void> {
    this.deps.repo.insertBatch([
      {
        id: randomUUID(),
        kind: "reflexive",
        content: input.observation,
        importance: input.importance ?? 0.5,
        goal_id: input.goalId,
        source_channel: "engine-reflexive",
      },
    ]);
  }

  async ingest(turn: WriterTurn): Promise<IngestResult> {
    if (this.isTrivial(turn)) {
      return { skipped: true, reason: "trivial-turn" };
    }

    const resolved = this.deps.router.resolve("classification");
    const extraction = await this.classify(turn, resolved);
    if (!extraction) {
      return { skipped: true, reason: "classify-failed" };
    }
    if (!extraction.extractions || extraction.extractions.length === 0) {
      return { skipped: true, reason: "empty-extraction" };
    }

    const { insert, invalidate } = await this.reconcile(extraction.extractions, resolved);

    for (const id of invalidate) {
      this.deps.repo.invalidate(id, {
        reason: "writer-reconcile DELETE",
        invalidatedBy: "writer",
      });
    }

    if (insert.length === 0 && invalidate.length === 0) {
      return { skipped: true, reason: "noop" };
    }

    const records: MemoryInsert[] = insert.map((e) => ({
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

    try {
      if (records.length > 0) this.deps.repo.insertBatch(records);
    } catch (err) {
      this.deps.bus.emit({ type: "memory:write_failed", reason: (err as Error).message });
      return { skipped: true, reason: "write-failed" };
    }

    return { skipped: false, written: records.length, invalidated: invalidate.length };
  }

  private async classify(
    turn: WriterTurn,
    resolved: ResolvedModel,
  ): Promise<ExtractionResult | null> {
    try {
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

  private async reconcile(
    extractions: Extraction[],
    resolved: ResolvedModel,
  ): Promise<{ insert: Extraction[]; invalidate: string[] }> {
    const insert: Extraction[] = [];
    const invalidate: string[] = [];

    for (const ext of extractions) {
      const candidates = await this.deps.repo.search(ext.content, {
        kinds: [ext.kind],
        topK: 5,
      });
      if (candidates.length === 0) {
        insert.push(ext);
        continue;
      }

      const decisions = await this.runReconciler(ext, candidates, resolved);
      if (decisions === null) {
        insert.push(ext);
        continue;
      }

      let didAdd = false;
      let didNoop = false;
      for (const d of decisions) {
        if (d.action === "ADD") {
          insert.push(ext);
          didAdd = true;
        } else if (d.action === "DELETE" && d.target_id) {
          invalidate.push(d.target_id);
          this.deps.bus.emit({
            type: "memory:contradiction_detected",
            memoryId: d.target_id,
            contradictsId: ext.content,
            reason: d.reason ?? "writer-reconcile",
          });
        } else if (d.action === "UPDATE" && d.target_id) {
          invalidate.push(d.target_id);
          if (!didAdd) {
            insert.push(ext);
            didAdd = true;
          }
        } else if (d.action === "NOOP") {
          didNoop = true;
        }
      }
      // Prevent silent loss: if neither add nor noop nor delete fired, fall back to ADD.
      if (!didAdd && !didNoop && invalidate.length === 0) {
        insert.push(ext);
      }
    }
    return { insert, invalidate };
  }

  private async runReconciler(
    ext: Extraction,
    candidates: Awaited<ReturnType<MemoryRepository["search"]>>,
    resolved: ResolvedModel,
  ): Promise<ReconcileDecision[] | null> {
    const prompt = `New memory candidate:
${JSON.stringify(ext)}

Existing similar memories:
${candidates.map((c) => `- id=${c.id}: ${c.content} (importance=${c.importance})`).join("\n")}

Decide: emit JSON { "decisions": [ { "action": "ADD"|"UPDATE"|"DELETE"|"NOOP", "target_id"?: "...", "reason": "..." } ] }
- ADD: insert candidate as new memory.
- UPDATE: candidate refines existing (specify target_id) — old gets invalidated, new gets inserted.
- DELETE: candidate contradicts existing (specify target_id) — invalidate, do not insert candidate.
- NOOP: candidate is duplicate of existing.

JSON only.`;

    try {
      const provider = this.deps.providerRegistry.get(resolved.provider);
      const resp = await provider.chat(
        [
          { role: "system", content: "JSON-only memory reconciler." },
          { role: "user", content: prompt },
        ],
        resolved.model,
        { temperature: 0.2, maxTokens: 400 },
      );
      const parsed = JSON.parse(resp.content.trim()) as { decisions?: ReconcileDecision[] };
      return parsed.decisions ?? [];
    } catch (err) {
      this.deps.bus.emit({
        type: "memory:contradict_failed",
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

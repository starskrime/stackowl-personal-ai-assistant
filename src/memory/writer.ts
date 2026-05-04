/**
 * StackOwl — Element 15 — MemoryWriter
 *
 * Goal-conditioned ingestion pipeline. v1 ships:
 *   • Trivial-turn short-circuit (length + verdict only — no keyword
 *     classification; substantive content classification is delegated
 *     to IntelligenceRouter cheap-tier in Task 12).
 *
 * Subsequent tasks add LLM classification, contradiction check, and
 * ADD/UPDATE/DELETE/NOOP reconciliation.
 */

import type { MemoryRepository } from "./repository.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { IntelligenceRouter } from "../intelligence/router.js";

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
}

const MIN_MESSAGE_LEN_NEUTRAL = 12;

export class MemoryWriter {
  constructor(private readonly deps: WriterDeps) {}

  async ingest(turn: WriterTurn): Promise<IngestResult> {
    if (this.isTrivial(turn)) {
      return { skipped: true, reason: "trivial-turn" };
    }
    return { skipped: true, reason: "not-implemented-yet" };
  }

  private isTrivial(turn: WriterTurn): boolean {
    const msg = (turn.userMessage ?? "").trim();
    if (msg.length === 0) return true;
    if (turn.verdict === "NEUTRAL" && msg.length < MIN_MESSAGE_LEN_NEUTRAL) return true;
    return false;
  }
}

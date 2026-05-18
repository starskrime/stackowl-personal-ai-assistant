/**
 * CognitivePipeline — orchestrates the 3-call Dispatch → Execute → Consolidate
 * architecture, replacing the per-message classifier chain.
 *
 * Integration points:
 *   - ContextBuilder.build() calls prepareContext() to get cached + filtered context
 *   - Gateway calls postProcess() after delivering the response (fire-and-forget)
 *   - GatewayContext holds one CognitivePipeline instance shared across sessions
 *
 * Cold-start bypass:
 *   When warmth < 2 (new user, empty Symbol Table) the pipeline skips Dispatch
 *   and returns full context so first turns are never degraded.
 */

import { join } from "node:path";
import type { ModelProvider } from "../providers/base.js";
import type { SlotKey, SymbolTable } from "./symbol-table.js";
import { symbolTableRegistry, SymbolTable as SymbolTableClass } from "./symbol-table.js";
import { CognitiveDispatch, type DispatchResult } from "./dispatch.js";
import { CognitiveConsolidate, type ConsolidateTurn, type ConsolidateStores } from "./consolidate.js";
import { SymbolTableSeeder, type SeederDependencies } from "./symbol-table-seeder.js";
import { TurnJournal } from "./turn-journal.js";
import { log } from "../logger.js";

const WARMTH_THRESHOLD = 2;   // warmth score ≥ 2 → use compiled pipeline
const DISPATCH_TIMEOUT_MS = 800; // max wait for Dispatch LLM call; exceeded → cold-start fallback

// ─── Types ────────────────────────────────────────────────────────

export interface PostProcessInput {
  sessionId: string;
  userMessage: string;
  assistantResponse: string;
  toolsUsed: string[];
  dispatch: DispatchResult | null;
  /** Per-request user identifier — passed through to Consolidate for correct write-back scoping */
  userId?: string;
  /** Per-request channel identifier — passed through to Consolidate for correct write-back scoping */
  channelId?: string;
}

// ─── CognitivePipeline ────────────────────────────────────────────

/**
 * Persistent store references passed in from GatewayContext.
 * Optional — pipeline degrades gracefully without them (in-memory only).
 */
export interface CognitiveStores extends SeederDependencies {
  owlName: string;
  userId: string;
  channelId: string;
  /** Workspace root — used to co-locate turn-journal.jsonl with the SQLite DB */
  dataDir?: string;
}

export class CognitivePipeline {
  private readonly dispatch: CognitiveDispatch;
  private readonly consolidate: CognitiveConsolidate;
  private readonly lastDispatch = new Map<string, DispatchResult>();
  private seeder: SymbolTableSeeder | null = null;
  private journal: TurnJournal | null = null;

  constructor(provider: ModelProvider) {
    this.dispatch = new CognitiveDispatch(provider);
    this.consolidate = new CognitiveConsolidate(provider);
  }

  /**
   * Wire persistent memory stores. Call once after construction (in gateway init).
   * After this, Symbol Tables are seeded from and written back to SQLite/LanceDB/Kuzu.
   */
  setStores(stores: CognitiveStores): void {
    this.seeder = new SymbolTableSeeder(stores);
    this.consolidate.setStores({
      db: stores.db,
      memoryManager: stores.memoryManager,
      preferenceStore: stores.preferenceStore,
      owlName: stores.owlName,
      userId: stores.userId,
      channelId: stores.channelId,
    } satisfies ConsolidateStores);

    // ── WAL: create journal + replay crash-recovered turns ─────────────
    if (stores.dataDir) {
      const walDir = join(stores.dataDir, "memory");
      this.journal = new TurnJournal(walDir);
      this.consolidate.setJournal(this.journal);
      this.replayIncomplete();
    }

    log.cognition.info("cognitive-pipeline.stores.wired", {
      userId: stores.userId,
      owlName: stores.owlName,
      walEnabled: !!this.journal,
    });
  }

  /** Replay any turns that were enqueued but not committed before last crash. */
  private replayIncomplete(): void {
    if (!this.journal) return;
    const incomplete = this.journal.getIncomplete();
    this.journal.prune(7);

    if (incomplete.length === 0) return;

    log.cognition.warn("cognitive-pipeline.replay: found incomplete turns", {
      count: incomplete.length,
    });

    for (const entry of incomplete) {
      if (!entry.assistantResponse) {
        // Crash happened during Execute — no response to consolidate; just commit
        this.journal.commit(entry.id);
        log.cognition.info("cognitive-pipeline.replay: skipped (no response)", {
          id: entry.id,
          sessionId: entry.sessionId,
        });
        continue;
      }

      // Replay through Consolidate using a throwaway SymbolTable
      // (session is gone; we only need the persistent write-back to fire)
      const replayTable = new SymbolTableClass(`replay:${entry.id}`);
      const turn: ConsolidateTurn = {
        userMessage: entry.userMessage,
        assistantResponse: entry.assistantResponse,
        toolsUsed: entry.toolsUsed,
        executionPlan: entry.executionPlan,
        sessionId: entry.sessionId,
        turnIndex: entry.turnIndex,
        userId: entry.userId,
        channelId: entry.channelId,
      };
      this.consolidate.enqueue(turn, replayTable);
      log.cognition.info("cognitive-pipeline.replay: enqueued", {
        id: entry.id,
        sessionId: entry.sessionId,
        turnIndex: entry.turnIndex,
      });
    }
  }

  // ─── Seed SymbolTable from ContextPipeline output ──────────────

  /**
   * Called by ContextBuilder after running the full ContextPipeline.
   * Caches the expensive pipeline output so subsequent turns skip it.
   */
  seedFromPipelineOutput(
    sessionId: string,
    pipelineOutput: string,
    sessionContext?: { userId: string; owlName: string; channelId: string },
  ): SymbolTable {
    const table = symbolTableRegistry.getOrCreate(sessionId);
    const isNew = table.turnIndex === 0;

    if (pipelineOutput && table.isStale("contextOutput")) {
      table.set("contextOutput", pipelineOutput);
      log.cognition.info("cognitive-pipeline.seeded", {
        sessionId,
        outputLen: pipelineOutput.length,
        warmth: table.warmth(),
      });
    }

    // Seed from persistent stores on first session creation
    if (isNew && this.seeder && sessionContext) {
      this.seeder.seed(
        table,
        sessionContext.userId,
        sessionContext.owlName,
        sessionContext.channelId,
      ).catch((err) => {
        log.cognition.error("cognitive-pipeline.seed.failed", err as Error, { sessionId });
      });
    }

    return table;
  }

  // ─── Per-message Dispatch (intent + tool routing) ────────────

  /**
   * Runs CognitiveDispatch for this session turn and stores the result
   * so postProcess() can use it without re-running. Returns null on cold-start
   * (low warmth) so the gateway skips tool filtering.
   */
  async runDispatch(sessionId: string, userMessage: string): Promise<DispatchResult | null> {
    const table = symbolTableRegistry.getOrCreate(sessionId);
    table.onTurnStart();

    if (table.warmth() < WARMTH_THRESHOLD) {
      log.cognition.debug("cognitive-pipeline.dispatch.skipped — cold start", {
        sessionId, warmth: table.warmth(),
      });
      this.lastDispatch.delete(sessionId);
      return null;
    }

    try {
      const timeout = new Promise<null>((resolve) =>
        setTimeout(() => resolve(null), DISPATCH_TIMEOUT_MS),
      );
      const result = await Promise.race([
        this.dispatch.dispatch(userMessage, table),
        timeout,
      ]);
      if (!result) {
        log.cognition.warn("cognitive-pipeline.dispatch.timeout", {
          sessionId,
          limitMs: DISPATCH_TIMEOUT_MS,
        });
        this.lastDispatch.delete(sessionId);
        return null;
      }
      this.lastDispatch.set(sessionId, result);
      return result;
    } catch (err) {
      log.cognition.error("cognitive-pipeline.runDispatch.failed", err as Error, { sessionId });
      this.lastDispatch.delete(sessionId);
      return null;
    }
  }

  // ─── Context enrichment from Symbol Table slots ──────────────

  /**
   * Enrich a base memoryContext string with supplementary Symbol Table slots
   * selected by Dispatch. Called from the gateway after runDispatch() so the
   * engine receives user preferences, named entities, and memory digest on
   * turns where Dispatch identifies them as relevant.
   *
   * Returns the enriched context, or the original base if no slots are
   * populated or selected (cold-start, timeout, empty slots).
   */
  enrichContext(sessionId: string, contextSlots: SlotKey[], baseContext: string): string {
    const table = symbolTableRegistry.get(sessionId);
    if (!table || contextSlots.length === 0) return baseContext;
    return this.buildContextFromSlots(table, contextSlots, baseContext);
  }

  // ─── Post-response consolidation (fire-and-forget) ────────────

  postProcess(input: PostProcessInput): void {
    const table = symbolTableRegistry.get(input.sessionId);
    if (!table) {
      log.cognition.warn("cognitive-pipeline.postProcess: no symbol table for session", {
        sessionId: input.sessionId,
      });
      return;
    }

    // Use the stored dispatch result if not provided explicitly
    const dispatch = input.dispatch ?? this.lastDispatch.get(input.sessionId) ?? null;
    const executionPlan = dispatch?.executionPlan ?? {
      consolidationDepth: "light" as const,
      needsDnaMutation: false,
      needsPelletGen: false,
      skipInnerLife: true,
    };

    const turn: ConsolidateTurn = {
      userMessage: input.userMessage,
      assistantResponse: input.assistantResponse,
      toolsUsed: input.toolsUsed,
      executionPlan,
      sessionId: input.sessionId,
      turnIndex: table.turnIndex,
      userId: input.userId,
      channelId: input.channelId,
    };

    this.consolidate.enqueue(turn, table);
    this.lastDispatch.delete(input.sessionId); // consumed — clear for next turn

    log.cognition.debug("cognitive-pipeline.consolidate.enqueued", {
      sessionId: input.sessionId,
      depth: executionPlan.consolidationDepth,
      toolsUsed: input.toolsUsed.length,
    });
  }

  // ─── Invalidation bridge (call from gateway event handlers) ───

  invalidate(sessionId: string, event: import("./symbol-table.js").InvalidationEvent): void {
    const table = symbolTableRegistry.get(sessionId);
    if (table) {
      const affected = table.invalidate(event);
      log.cognition.info("cognitive-pipeline.invalidated", { sessionId, event, affected });
    }
  }

  // ─── Session lifecycle ────────────────────────────────────────

  dropSession(sessionId: string): void {
    symbolTableRegistry.drop(sessionId);
  }

  getSymbolTable(sessionId: string): SymbolTable | undefined {
    return symbolTableRegistry.get(sessionId);
  }

  // ─── Context assembly from slots ─────────────────────────────

  private buildContextFromSlots(
    table: SymbolTable,
    slots: SlotKey[],
    fallback: string,
  ): string {
    // Always include the base context (full pipeline output if available)
    const base = table.get("contextOutput") || fallback;

    if (slots.length === 0) return base;

    const extras: string[] = [];

    if (slots.includes("preferences")) {
      const prefs = table.get("preferences");
      if (prefs) extras.push(`## User Preferences\n${prefs}`);
    }
    if (slots.includes("namedEntities")) {
      const raw = table.get("namedEntities");
      if (raw) {
        try {
          const parsed = JSON.parse(raw) as Record<string, string[]>;
          const lines = Object.entries(parsed)
            .filter(([, v]) => v.length > 0)
            .map(([k, v]) => `${k}: ${v.join(", ")}`);
          if (lines.length > 0) extras.push(`## Known People & Projects\n${lines.join("\n")}`);
        } catch { /* skip malformed */ }
      }
    }
    if (slots.includes("memoryDigest")) {
      const digest = table.get("memoryDigest");
      if (digest) extras.push(`## Recent Memory\n${digest}`);
    }
    if (slots.includes("histSummary")) {
      const hist = table.get("histSummary");
      if (hist) extras.push(`## Session History\n${hist}`);
    }
    if (slots.includes("owlState")) {
      const raw = table.get("owlState");
      if (raw) {
        try {
          const state = JSON.parse(raw) as Record<string, unknown>;
          extras.push(`## Owl State\nmood: ${state["mood"] ?? "neutral"}, focus: ${state["focus"] ?? "general"}`);
        } catch { /* skip */ }
      }
    }

    return extras.length > 0 ? `${base}\n\n${extras.join("\n\n")}` : base;
  }
}

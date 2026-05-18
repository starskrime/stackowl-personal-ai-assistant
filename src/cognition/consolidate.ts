/**
 * CognitiveConsolidate — async post-response structured extraction.
 *
 * Runs AFTER the response is delivered to the user (never blocks).
 * Replaces: detectPreferences(), analyzeBehavior(), groundState.refresh(),
 *           innerLife.thinkInBackground() as separate LLM calls.
 *
 * All of those become a single structured JSON extraction, serialized per
 * session to avoid lost-update races (version stampede failure mode).
 */

import { randomUUID } from "node:crypto";
import type { ModelProvider } from "../providers/base.js";
import type { SymbolTable } from "./symbol-table.js";
import type { ExecutionPlan } from "./dispatch.js";
import type { TurnJournal } from "./turn-journal.js";
import { AlwaysOnSignalExtractor } from "./signal-extractor.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface ConsolidateTurn {
  userMessage: string;
  /**
   * The assistant's full response for this turn. Stored in the WAL so that
   * crash-recovered turns can be replayed through Consolidate on next startup.
   */
  assistantResponse: string;
  toolsUsed: string[];
  executionPlan: ExecutionPlan;
  sessionId: string;
  turnIndex: number;
  /** Per-request user identifier — overrides ConsolidateStores.userId for write-back */
  userId?: string;
  /** Per-request channel identifier — overrides ConsolidateStores.channelId for write-back */
  channelId?: string;
}

export interface ConsolidateOutput {
  preferenceUpdates: Array<{ raw: string; category: string }>;
  pelletCandidates: Array<{ title: string; content: string }>;
  owlStateUpdate?: { mood?: string; focus?: string; energyLevel?: string };
  memoryPatch?: string;
  dnaMutationSignal: boolean;
}

/**
 * Optional persistent stores for Consolidate write-back.
 * When provided, extracted preferences and entities are durably persisted
 * in addition to being written to the in-memory Symbol Table.
 */
export interface ConsolidateStores {
  db: import("../memory/db.js").MemoryDatabase;
  memoryManager: import("../memory/memory-manager.js").MemoryManager;
  preferenceStore: import("../preferences/store.js").PreferenceStore;
  owlName: string;
  userId: string;
  channelId: string;
}

// ─── Prompts ──────────────────────────────────────────────────────

function fullPrompt(turn: ConsolidateTurn): string {
  return `Analyze this conversation turn and extract structured knowledge for the AI assistant's long-term memory.

## User Message
${turn.userMessage.slice(0, 800)}

## Assistant Response
${turn.assistantResponse.slice(0, 1200)}

## Tools Used
${turn.toolsUsed.join(", ") || "none"}

Output ONLY a JSON object (no markdown, no commentary):
{
  "preferenceUpdates": [{"raw": "the preference statement", "category": "dietary|style|identity|avoid|prefer|schedule"}],
  "pelletCandidates": [{"title": "concise title", "content": "knowledge worth storing for future retrieval"}],
  "owlStateUpdate": {"mood": "curious|focused|playful|neutral|supportive", "focus": "brief topic", "energyLevel": "high|medium|low"},
  "memoryPatch": "one factual sentence summarizing what happened this turn, or null",
  "dnaMutationSignal": false
}

Rules:
- preferenceUpdates: ONLY for clear user preferences ("I prefer X", "I am vegan", "never Y") — not assistant opinions
- pelletCandidates: ONLY for reusable knowledge (technical decisions, research findings, code patterns) — not casual chat
- owlStateUpdate: estimate mood/focus from conversation tone — always provide
- memoryPatch: always provide a neutral factual summary
- dnaMutationSignal: true ONLY for strong explicit style/personality feedback from user`;
}

function lightPrompt(turn: ConsolidateTurn): string {
  return `Briefly analyze this conversation turn.

User: ${turn.userMessage.slice(0, 300)}
Assistant: ${turn.assistantResponse.slice(0, 300)}

Output ONLY JSON:
{
  "preferenceUpdates": [],
  "pelletCandidates": [],
  "owlStateUpdate": {"mood": "neutral|curious|focused|playful", "energyLevel": "high|medium|low"},
  "memoryPatch": "one sentence summary",
  "dnaMutationSignal": false
}`;
}

// ─── CognitiveConsolidate ─────────────────────────────────────────

export class CognitiveConsolidate {
  private readonly sessionQueues = new Map<string, Promise<void>>();
  private readonly extractor = new AlwaysOnSignalExtractor();
  private stores: ConsolidateStores | null = null;
  private journal: TurnJournal | null = null;

  constructor(private readonly provider: ModelProvider) {}

  /** Wire persistent stores for durable write-back. Call once after construction. */
  setStores(stores: ConsolidateStores): void {
    this.stores = stores;
    log.cognition.info("consolidate.stores.wired", {
      userId: stores.userId,
      owlName: stores.owlName,
    });
  }

  /** Wire WAL journal. Call once after construction alongside setStores(). */
  setJournal(journal: TurnJournal): void {
    this.journal = journal;
    log.cognition.info("consolidate.journal.wired");
  }

  /**
   * Enqueue a consolidation job for this session. Returns immediately —
   * the job runs async after the caller yields. Jobs per session are
   * serialized (no concurrent writes to the same SymbolTable).
   *
   * WAL: appends a journal entry BEFORE queuing so a crash between enqueue
   * and run completion can be detected and replayed on next startup.
   */
  enqueue(turn: ConsolidateTurn, symbolTable: SymbolTable): void {
    // WAL: record before queuing — crash-gap protection
    const journalId = this.journal?.append({
      sessionId: turn.sessionId,
      turnIndex: turn.turnIndex,
      userId: turn.userId,
      channelId: turn.channelId,
      userMessage: turn.userMessage,
      assistantResponse: turn.assistantResponse,
      toolsUsed: turn.toolsUsed,
      executionPlan: turn.executionPlan,
    }) ?? null;

    const prev = this.sessionQueues.get(turn.sessionId) ?? Promise.resolve();
    const next = prev
      .then(() => this.run(turn, symbolTable, journalId))
      .catch((err) => {
        log.cognition.error("consolidate.queue.unhandled", err as Error, {
          sessionId: turn.sessionId,
          turnIndex: turn.turnIndex,
        });
      });
    this.sessionQueues.set(turn.sessionId, next);
  }

  // ─── Core execution ──────────────────────────────────────────

  private async run(turn: ConsolidateTurn, symbolTable: SymbolTable, journalId: string | null): Promise<void> {
    const start = Date.now();
    log.cognition.info("consolidate.entry", {
      sessionId: turn.sessionId,
      turnIndex: turn.turnIndex,
      depth: turn.executionPlan.consolidationDepth,
      toolsUsed: turn.toolsUsed,
    });

    // ── Step 1: Always-on deterministic extraction (no LLM, no skip) ──
    this.runAlwaysOnExtraction(turn, symbolTable);

    // ── Step 2: LLM-based structured extraction ────────────────────────
    const output = await this.runLLMExtraction(turn, start);

    // ── Step 3: Apply output to Symbol Table ──────────────────────────
    this.applyToSymbolTable(output, turn, symbolTable);

    // WAL: commit — Consolidate completed, no replay needed
    if (journalId) this.journal?.commit(journalId);

    log.cognition.info("consolidate.exit", {
      sessionId: turn.sessionId,
      turnIndex: turn.turnIndex,
      prefUpdates: output.preferenceUpdates.length,
      pellets: output.pelletCandidates.length,
      dnaMutationSignal: output.dnaMutationSignal,
      durationMs: Date.now() - start,
    });
  }

  private runAlwaysOnExtraction(turn: ConsolidateTurn, symbolTable: SymbolTable): void {
    const userSignals = this.extractor.extract(turn.userMessage);
    const responseSignals = this.extractor.extract(turn.assistantResponse);

    // Merge entities (person/project/place/date)
    const allEntities = [...userSignals.entities, ...responseSignals.entities];
    if (allEntities.length > 0) {
      const current = symbolTable.get("namedEntities");
      symbolTable.set("namedEntities", this.extractor.mergeEntitiesIntoSlot(current, allEntities));
    }

    // Merge deterministic preferences from user message
    if (userSignals.preferences.length > 0) {
      const current = symbolTable.get("preferences");
      symbolTable.set("preferences", this.extractor.mergePreferencesIntoSlot(current, userSignals.preferences));
    }
  }

  private async runLLMExtraction(turn: ConsolidateTurn, _start: number): Promise<ConsolidateOutput> {
    const prompt = turn.executionPlan.consolidationDepth === "full"
      ? fullPrompt(turn)
      : lightPrompt(turn);
    const maxTokens = turn.executionPlan.consolidationDepth === "full" ? 600 : 250;

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { temperature: 0, maxTokens },
      );
      return this.parse(response.content ?? "");
    } catch (err) {
      log.cognition.error("consolidate.llm.failed", err as Error, {
        sessionId: turn.sessionId,
        depth: turn.executionPlan.consolidationDepth,
      });
      return { preferenceUpdates: [], pelletCandidates: [], dnaMutationSignal: false };
    }
  }

  private parse(raw: string): ConsolidateOutput {
    try {
      const jsonMatch = raw.match(/\{[\s\S]*\}/);
      const json = JSON.parse(jsonMatch?.[0] ?? raw) as Record<string, unknown>;
      return {
        preferenceUpdates: Array.isArray(json["preferenceUpdates"])
          ? json["preferenceUpdates"] as Array<{ raw: string; category: string }>
          : [],
        pelletCandidates: Array.isArray(json["pelletCandidates"])
          ? json["pelletCandidates"] as Array<{ title: string; content: string }>
          : [],
        owlStateUpdate: json["owlStateUpdate"] as ConsolidateOutput["owlStateUpdate"] ?? undefined,
        memoryPatch: typeof json["memoryPatch"] === "string" ? json["memoryPatch"] : undefined,
        dnaMutationSignal: json["dnaMutationSignal"] === true,
      };
    } catch {
      return { preferenceUpdates: [], pelletCandidates: [], dnaMutationSignal: false };
    }
  }

  private applyToSymbolTable(output: ConsolidateOutput, turn: ConsolidateTurn, symbolTable: SymbolTable): void {
    // Merge LLM-extracted preferences
    if (output.preferenceUpdates.length > 0) {
      const current = symbolTable.get("preferences");
      const lines = current ? current.split("\n").filter(Boolean) : [];
      const lineSet = new Set(lines);
      for (const { raw, category } of output.preferenceUpdates) {
        const line = `${category}: ${raw}`;
        if (!lineSet.has(line)) { lineSet.add(line); lines.push(line); }
      }
      symbolTable.set("preferences", lines.join("\n"));

      // Durable write: persist to PreferenceStore + SQLite facts
      this.persistPreferences(output.preferenceUpdates, turn);
    }

    // Owl state
    if (output.owlStateUpdate) {
      let current: Record<string, unknown> = {};
      try { if (symbolTable.get("owlState")) current = JSON.parse(symbolTable.get("owlState")); } catch { /* ok */ }
      symbolTable.set("owlState", JSON.stringify({
        ...current,
        ...output.owlStateUpdate,
        lastUpdatedTurn: turn.turnIndex,
      }));
    }

    // Rolling memory digest
    if (output.memoryPatch) {
      const MAX_LEN = 3000;
      const current = symbolTable.get("memoryDigest");
      const patch = `[T${turn.turnIndex}] ${output.memoryPatch}`;
      const updated = current ? `${current}\n${patch}`.slice(-MAX_LEN) : patch;
      symbolTable.set("memoryDigest", updated);
    }

    // Durable write: pellet candidates → MemoryManager (LanceDB + Kuzu)
    if (output.pelletCandidates.length > 0) {
      this.persistPellets(output.pelletCandidates, turn);
    }
  }

  // ─── Durable write-back ──────────────────────────────────────

  private persistPreferences(
    updates: Array<{ raw: string; category: string }>,
    turn: ConsolidateTurn,
  ): void {
    const stores = this.stores;
    if (!stores) return;

    const userId = turn.userId ?? stores.userId;
    const channelId = turn.channelId ?? stores.channelId;

    for (const { raw, category } of updates) {
      // 1. PreferenceStore (JSON file — for structured named preferences)
      const prefKey = this.toPrefKey(raw, category);
      if (prefKey) {
        stores.preferenceStore.set(prefKey, raw, `cognitive-consolidate:${turn.sessionId}`, channelId)
          .catch((err) => {
            log.cognition.error("consolidate.pref.persist.failed", err as Error, { prefKey });
          });
      }

      // 2. SQLite facts (category: "preference") — for FTS search
      try {
        stores.db.facts.add({
          userId,
          owlName: stores.owlName,
          fact: raw,
          category: "preference",
          confidence: 0.8,
          source: "inferred",
        });
      } catch (err) {
        log.cognition.error("consolidate.pref.db.failed", err as Error, { raw });
      }
    }
  }

  private persistPellets(
    candidates: Array<{ title: string; content: string }>,
    turn: ConsolidateTurn,
  ): void {
    const stores = this.stores;
    if (!stores) return;

    const userId = turn.userId ?? stores.userId;

    for (const { title, content } of candidates) {
      // Write to LanceDB + Kuzu via MemoryWorker (includes embedding generation)
      const factRecord = {
        fact_id: randomUUID(),
        type: "project_context" as const,
        content: `${title}: ${content}`.slice(0, 2000),
        confidence: 0.75,
        source: turn.sessionId,
        confirmation_count: 0,
        contradictions: "[]",
        owl_name: stores.owlName,
        user_id: userId,
        created_at: new Date().toISOString(),
        vector: [],   // MemoryWorker fills this in
      };
      stores.memoryManager.writeFact(factRecord as any);

      // Also write to SQLite for FTS search
      try {
        stores.db.facts.add({
          userId,
          owlName: stores.owlName,
          fact: `${title}: ${content}`.slice(0, 500),
          entity: title,
          category: "project_detail",
          confidence: 0.75,
          source: "inferred",
        });
      } catch (err) {
        log.cognition.error("consolidate.pellet.db.failed", err as Error, { title });
      }
    }
  }

  private toPrefKey(raw: string, category: string): string | null {
    // Map category + text to a PreferenceStore key when obvious
    const lower = raw.toLowerCase();
    if (category === "dietary" || lower.includes("vegan") || lower.includes("vegetarian")) {
      return "dietary_restriction";
    }
    if (category === "style" || lower.includes("always respond") || lower.includes("format")) {
      return "message_style";
    }
    if (category === "schedule" || lower.includes("timezone") || lower.includes("working hours")) {
      return "schedule";
    }
    return null; // Generic preferences stay in facts DB only
  }

  // ─── Expose pellet candidates for the gateway to act on ──────────

  getPelletCandidates(sessionId: string): Promise<Array<{ title: string; content: string }>> {
    // Drain the queue then return accumulated candidates
    // For now: gateway reads from consolidation output via events
    void sessionId;
    return Promise.resolve([]);
  }
}

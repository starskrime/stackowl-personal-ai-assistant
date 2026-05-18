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

import type { ModelProvider } from "../providers/base.js";
import type { SymbolTable } from "./symbol-table.js";
import type { ExecutionPlan } from "./dispatch.js";
import { AlwaysOnSignalExtractor } from "./signal-extractor.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface ConsolidateTurn {
  userMessage: string;
  assistantResponse: string;
  toolsUsed: string[];
  executionPlan: ExecutionPlan;
  sessionId: string;
  turnIndex: number;
}

export interface ConsolidateOutput {
  preferenceUpdates: Array<{ raw: string; category: string }>;
  pelletCandidates: Array<{ title: string; content: string }>;
  owlStateUpdate?: { mood?: string; focus?: string; energyLevel?: string };
  memoryPatch?: string;
  dnaMutationSignal: boolean;
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

  constructor(private readonly provider: ModelProvider) {}

  /**
   * Enqueue a consolidation job for this session. Returns immediately —
   * the job runs async after the caller yields. Jobs per session are
   * serialized (no concurrent writes to the same SymbolTable).
   */
  enqueue(turn: ConsolidateTurn, symbolTable: SymbolTable): void {
    const prev = this.sessionQueues.get(turn.sessionId) ?? Promise.resolve();
    const next = prev
      .then(() => this.run(turn, symbolTable))
      .catch((err) => {
        log.cognition.error("consolidate.queue.unhandled", err as Error, {
          sessionId: turn.sessionId,
          turnIndex: turn.turnIndex,
        });
      });
    this.sessionQueues.set(turn.sessionId, next);
  }

  // ─── Core execution ──────────────────────────────────────────

  private async run(turn: ConsolidateTurn, symbolTable: SymbolTable): Promise<void> {
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
  }

  // ─── Expose pellet candidates for the gateway to act on ──────────

  getPelletCandidates(sessionId: string): Promise<Array<{ title: string; content: string }>> {
    // Drain the queue then return accumulated candidates
    // For now: gateway reads from consolidation output via events
    void sessionId;
    return Promise.resolve([]);
  }
}

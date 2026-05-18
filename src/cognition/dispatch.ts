/**
 * CognitiveDispatch — one fast LLM call per user message that replaces
 * the per-message classifier chain (continuity, mental-model, skill-intent,
 * preference-detect). Produces a structured DispatchResult that drives:
 *
 *   - Which tools Execute loads (toolHints, always above a safety floor)
 *   - Which Symbol Table slots Execute receives (contextSlots)
 *   - How deep Consolidate runs post-response (executionPlan)
 */

import type { ModelProvider } from "../providers/base.js";
import type { SymbolTable, SlotKey } from "./symbol-table.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export type IntentClass =
  | "conversational"
  | "recall"
  | "task"
  | "research"
  | "creative"
  | "preference_update"
  | "system";

export interface ExecutionPlan {
  consolidationDepth: "light" | "full";
  needsDnaMutation: boolean;
  needsPelletGen: boolean;
  skipInnerLife: boolean;
}

export interface DispatchResult {
  intent: IntentClass;
  toolHints: string[];     // advisory; always ∪ CORE_FLOOR
  contextSlots: SlotKey[]; // which symbol table slots Execute should receive
  executionPlan: ExecutionPlan;
  durationMs: number;
  fromFallback: boolean;
}

// ─── Safety floor — always loaded regardless of toolHints ─────────

const CORE_TOOL_FLOOR: string[] = [
  "read_file",
  "write_file",
  "search_memory",
  "web_search",
];

// ─── Prompt ───────────────────────────────────────────────────────

function buildPrompt(userMessage: string, symbolSummary: string): string {
  return `You are a routing classifier for an AI assistant. Analyze the user message and output a JSON routing decision.

## Session Context
${symbolSummary}

## User Message
"${userMessage.replace(/"/g, '\\"').slice(0, 1000)}"

## Output (JSON only — no markdown, no commentary)
{
  "intent": "<conversational|recall|task|research|creative|preference_update|system>",
  "toolHints": ["<tool names most likely needed — max 8>"],
  "contextSlots": ["<symbol table slots needed: contextOutput|preferences|namedEntities|owlState|histSummary|memoryDigest>"],
  "executionPlan": {
    "consolidationDepth": "<light|full>",
    "needsDnaMutation": false,
    "needsPelletGen": false,
    "skipInnerLife": true
  }
}

Classification rules:
- "conversational": greetings, small talk, quick questions → toolHints: [], contextSlots: []
- "recall": asking about previous conversations → toolHints: ["search_memory"], contextSlots: ["memoryDigest","namedEntities"]
- "task": file ops, calendar, code, email → toolHints: [specific tools], contextSlots: ["preferences","namedEntities"]
- "research": web searches, multi-source → toolHints: ["web_search","read_file"], contextSlots: ["histSummary"]
- "creative": writing, brainstorming → contextSlots: ["preferences","owlState"]
- "preference_update": "I prefer/I am/never suggest" → toolHints: [], contextSlots: ["preferences"]
- "system": /commands, settings → toolHints: [], contextSlots: []

consolidationDepth "full" only for: task, research, creative, preference_update
needsPelletGen: true only when task produces significant reusable knowledge
needsDnaMutation: true only if strong explicit style/personality feedback`;
}

// ─── CognitiveDispatch ────────────────────────────────────────────

export class CognitiveDispatch {
  constructor(private readonly provider: ModelProvider) {}

  async dispatch(
    userMessage: string,
    symbolTable: SymbolTable,
  ): Promise<DispatchResult> {
    const start = Date.now();

    log.cognition.info("dispatch.entry", {
      sessionId: symbolTable.sessionId,
      msgLen: userMessage.length,
      warmth: symbolTable.warmth(),
      staleness: symbolTable.staleness(),
    });

    const symbolSummary = symbolTable.summaryForDispatch();
    const prompt = buildPrompt(userMessage, symbolSummary);

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { temperature: 0, maxTokens: 350 },
      );

      const raw = (response.content ?? "").trim();
      const parsed = this.parse(raw);
      const result: DispatchResult = {
        ...parsed,
        toolHints: this.mergeFloor(parsed.toolHints),
        durationMs: Date.now() - start,
        fromFallback: false,
      };

      log.cognition.info("dispatch.exit", {
        sessionId: symbolTable.sessionId,
        intent: result.intent,
        toolHints: result.toolHints,
        contextSlots: result.contextSlots,
        consolidationDepth: result.executionPlan.consolidationDepth,
        durationMs: result.durationMs,
      });

      return result;
    } catch (err) {
      log.cognition.error("dispatch.failed — safe fallback", err as Error, {
        sessionId: symbolTable.sessionId,
        durationMs: Date.now() - start,
      });
      return this.safeFallback(Date.now() - start);
    }
  }

  private parse(raw: string): Omit<DispatchResult, "toolHints" | "durationMs" | "fromFallback"> & { toolHints: string[] } {
    try {
      const jsonMatch = raw.match(/\{[\s\S]*\}/);
      const json = JSON.parse(jsonMatch?.[0] ?? raw) as Record<string, unknown>;
      return {
        intent: this.parseIntent(json["intent"]),
        toolHints: Array.isArray(json["toolHints"]) ? (json["toolHints"] as string[]) : [],
        contextSlots: this.parseSlots(json["contextSlots"]),
        executionPlan: this.parsePlan(json["executionPlan"]),
      };
    } catch {
      return this.safeFallback(0);
    }
  }

  private parseIntent(raw: unknown): IntentClass {
    const valid: IntentClass[] = ["conversational", "recall", "task", "research", "creative", "preference_update", "system"];
    return valid.includes(raw as IntentClass) ? raw as IntentClass : "conversational";
  }

  private parseSlots(raw: unknown): SlotKey[] {
    const valid: SlotKey[] = ["contextOutput", "preferences", "namedEntities", "owlState", "histSummary", "memoryDigest", "skillList", "toolManifest"];
    if (!Array.isArray(raw)) return [];
    return (raw as unknown[]).filter((s): s is SlotKey => valid.includes(s as SlotKey));
  }

  private parsePlan(raw: unknown): ExecutionPlan {
    const r = (raw as Record<string, unknown>) ?? {};
    return {
      consolidationDepth: r["consolidationDepth"] === "full" ? "full" : "light",
      needsDnaMutation: r["needsDnaMutation"] === true,
      needsPelletGen: r["needsPelletGen"] === true,
      skipInnerLife: r["skipInnerLife"] !== false,
    };
  }

  private mergeFloor(hints: string[]): string[] {
    const set = new Set(hints);
    for (const t of CORE_TOOL_FLOOR) set.add(t);
    return [...set];
  }

  private safeFallback(durationMs: number): DispatchResult {
    return {
      intent: "conversational",
      toolHints: [...CORE_TOOL_FLOOR],
      contextSlots: ["preferences", "histSummary", "namedEntities"],
      executionPlan: {
        consolidationDepth: "light",
        needsDnaMutation: false,
        needsPelletGen: false,
        skipInnerLife: true,
      },
      durationMs,
      fromFallback: true,
    };
  }
}

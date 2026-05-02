import { OwlEngine } from "./runtime.js";
import { HealthMonitor } from "./health-monitor.js";
import { decide } from "./recovery-orchestrator.js";
import { QualityEvaluator } from "./quality-evaluator.js";
import { OutcomeJournal } from "./outcome-journal.js";
import { UserFacingStatusNarrator } from "./user-facing-narrator.js";
import { TaskLedgerStore } from "./task-ledger.js";
import { log } from "../logger.js";
import type {
  TurnRequest, TurnResult, TaskLedger, Decision,
  OrchestratorResponse, DegradationTier, HitlChannel,
} from "./types.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { ModelProvider } from "../providers/base.js";
import type { GoalVerifier } from "../tools/goal-verifier.js";

// Use `any` for owl/config since their shapes vary by project setup
interface OrchestratorDeps {
  owl: any;       // OwlInstance — has owl.persona.name, owl.persona.emoji, owl.dna
  provider: ModelProvider;
  config: any;    // StackOwlConfig — accessed via (config as any)
  db: MemoryDatabase;
  toolRegistry?: any;
  hitlChannel?: HitlChannel;
  sessionHistory?: import("../providers/base.js").ChatMessage[];
  goalVerifier?: GoalVerifier;
}

interface RunContext {
  sessionId: string;
  userId: string;
  memoryContext?: string;
  onProgress?: (msg: string) => Promise<void>;
  onStreamEvent?: (event: import("../providers/base.js").StreamEvent) => Promise<void>;
}

const TOKEN_BUDGET = 8000;

const SIMPLE_PATTERNS = [
  /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|sure|yes|no|bye|goodbye)[!?.]*$/i,
  /^what (is|are) (the )?(time|date|day|weather)/i,
  /^who are you/i,
];

function classifyComplexity(message: string): "simple" | "medium" | "complex" {
  if (message.length < 80 && SIMPLE_PATTERNS.some(p => p.test(message.trim()))) return "simple";
  if (message.length > 300 || /\b(research|analyze|compare|plan|build|create|write a|investigate)\b/i.test(message)) return "complex";
  return "medium";
}

export class OwlOrchestrator {
  private engine: OwlEngine;
  private qualityEvaluator: QualityEvaluator;
  private narrator: UserFacingStatusNarrator;
  private journal: OutcomeJournal;
  private ledgerStore: TaskLedgerStore;
  private deps: OrchestratorDeps;

  constructor(deps: OrchestratorDeps) {
    this.deps = deps;
    this.engine = new OwlEngine();
    this.qualityEvaluator = new QualityEvaluator();
    this.narrator = new UserFacingStatusNarrator();
    this.journal = new OutcomeJournal(deps.db);
    this.ledgerStore = new TaskLedgerStore(deps.db);

    // Wire GoalVerifier into ToolRegistry if both are provided
    if (deps.goalVerifier && deps.toolRegistry && typeof deps.toolRegistry.setGoalVerifier === "function") {
      deps.toolRegistry.setGoalVerifier(deps.goalVerifier);
    }
  }

  async run(userMessage: string, ctx: RunContext): Promise<OrchestratorResponse> {
    const complexity = classifyComplexity(userMessage);
    const tokenBudget = { total: TOKEN_BUDGET, used: 0, remaining: TOKEN_BUDGET };
    const sessionHistory = this.deps.sessionHistory ?? [];

    // Phase 1 — PLAN
    const ledger = await this._plan(userMessage, complexity, ctx);

    // Check for incomplete task from a prior session
    const incomplete = await this.ledgerStore.loadIncomplete(ctx.userId);
    if (incomplete && incomplete.taskId !== ledger.id) {
      const resumeMsg = `Picking up your task from a prior session — I was on step ${incomplete.subgoalIndex + 1}: "${incomplete.subgoalText}". Continuing now.`;
      await ctx.onProgress?.(resumeMsg);
    }

    const monitor = new HealthMonitor(TOKEN_BUDGET);
    let lastTurn: TurnResult | null = null;
    let iteration = 0;
    let finalDecision: Decision = "CONTINUE";
    const dna = {
      riskTolerance: (this.deps.owl.dna?.riskTolerance ?? "balanced") as "cautious" | "balanced" | "aggressive",
      challengeLevel: (this.deps.owl.dna?.challengeLevel ?? "medium") as "low" | "medium" | "high",
    };

    const messages = [
      ...sessionHistory,
      { role: "user" as const, content: userMessage },
    ];

    const planBlock = this._buildPlanBlock(ledger);
    const runMessages = complexity === "simple" || !planBlock
      ? messages
      : [{ role: "system" as const, content: planBlock }, ...messages];

    // Phase 2–4 main loop
    while (monitor.shouldContinue()) {
      const turnRequest: TurnRequest = {
        messages: runMessages,
        tools: [],
        modelName: this.deps.provider.name,
        providerName: this.deps.provider.name,
        sessionId: ctx.sessionId,
        turnBudget: { ...tokenBudget },
        _resolvedProvider: this.deps.provider,
        toolRegistry: this.deps.toolRegistry,
        onStreamEvent: ctx.onStreamEvent,
        onProgress: ctx.onProgress,
      };

      // Phase 2 — EXECUTE
      lastTurn = await this.engine.runTurn(turnRequest, this.deps.provider);
      tokenBudget.used += lastTurn.tokensUsed;
      tokenBudget.remaining = Math.max(0, tokenBudget.total - tokenBudget.used);

      // Phase 3 — ASSESS
      monitor.observe(lastTurn, ledger, iteration++);

      // Phase 4 — DECIDE
      finalDecision = decide(monitor.getHealth(), lastTurn, ledger, dna);
      log.engine.debug(`[Orchestrator] i=${iteration} decision=${finalDecision}`);

      if (finalDecision === "REPLAN") {
        // TODO: call LLM to produce revised TaskLedger (goal + subGoals) — deferred pending TrajectoryStore integration
        await this.ledgerStore.addRevision(ledger.id, "stall detected", ledger.goal);
        const updated = await this.ledgerStore.load(ledger.id);
        if (updated) ledger.revisions = updated.revisions;
        continue;
      }
      if (finalDecision === "HITL") {
        if (!this.deps.hitlChannel) { finalDecision = "SYNTHESIZE"; break; }
        // TODO: call this.deps.hitlChannel.pause(request) and handle response — deferred pending channel-specific HITL adapters
        finalDecision = "SYNTHESIZE";
        break;
      }
      if (finalDecision === "CONTINUE") continue;
      break; // SYNTHESIZE or DEGRADE
    }

    // Phase 6 — SYNTHESIZE / DEGRADE
    const rawContent = lastTurn?.content ?? "";
    const { score, cleanContent } = this.qualityEvaluator.evaluateAndStrip({
      content: rawContent,
      loopExhausted: lastTurn?.budgetExhausted ?? false,
      toolCallCount: lastTurn?.toolCalls.length ?? 0,
      toolFailureCount: lastTurn?.failedTools.length ?? 0,
      taskComplexity: complexity,
      hasStructuredOutput: /\|.+\|/.test(rawContent) || rawContent.includes("```"),
    });

    let finalContent = cleanContent;
    let degradationTier: DegradationTier = 1;

    if (finalDecision === "DEGRADE" || score < 0.3) {
      degradationTier = score < 0.1 ? 4 : score < 0.3 ? 3 : 2;
      finalContent = this.narrator.buildDegradation(
        degradationTier, cleanContent, lastTurn?.pendingCapabilityGap, undefined,
      );
    }

    // Phase 7 — NARRATE
    finalContent = this.narrator.postProcess(finalContent, score);

    // Record outcome
    try {
      await this.journal.record({
        sessionId: ctx.sessionId,
        owlName: this.deps.owl.persona.name,
        userId: ctx.userId,
        userMessage,
        totalTurns: iteration,
        toolsUsed: lastTurn?.toolCalls.map(tc => tc.name) ?? [],
        outcome: score > 0.6 ? "success" : score > 0.3 ? "partial" : "failure",
        reward: score * 2 - 1,
        qualityScore: score,
        qualityFlags: lastTurn?.budgetExhausted ? ["budget_exhausted"] : [],
        taskCategory: "general",
        taskComplexity: complexity,
        degradationTier,
        recoveryActions: ledger.revisions.map(r => r.reason),
      });
    } catch (e) {
      log.engine.warn(`[Orchestrator] Journal record failed: ${e}`);
    }

    return {
      content: finalContent,
      owlName: this.deps.owl.persona.name,
      owlEmoji: this.deps.owl.persona.emoji ?? "🦉",
      toolsUsed: lastTurn?.toolCalls.map(tc => tc.name) ?? [],
      qualityScore: score,
      degradationTier,
      taskCategory: "general",
      complexity,
      ledgerId: ledger.id,
      evolutionSignals: { qualityScore: score, taskCategory: "general" },
    };
  }

  private async _plan(
    userMessage: string,
    complexity: "simple" | "medium" | "complex",
    ctx: RunContext,
  ): Promise<import("./task-ledger.js").LedgerWithMeta> {
    const ledger = this.ledgerStore.create(ctx.sessionId, ctx.userId, {
      goal: userMessage,
      subGoals: [],
      expectedOutput: "a complete, helpful response",
      complexity,
      estimatedTurns: complexity === "simple" ? 1 : complexity === "medium" ? 3 : 7,
      behavioralConstraints: [],
      approachPatterns: [],
      revisions: [],
    });
    try { await this.ledgerStore.save(ledger); } catch { /* non-fatal */ }
    return ledger;
  }

  private _buildPlanBlock(ledger: TaskLedger): string {
    if (ledger.complexity === "simple") return "";
    const done = ledger.subGoals.filter(sg => sg.status === "done").length;
    return [
      "[Current Plan]",
      `Goal: ${ledger.goal}`,
      `Progress: ${done}/${ledger.subGoals.length} steps complete`,
      `Expected output: ${ledger.expectedOutput}`,
    ].join("\n");
  }
}

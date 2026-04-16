/**
 * StackOwl — Task Orchestrator
 *
 * Executes strategies produced by the classifier: DIRECT, STANDARD,
 * SPECIALIST, PLANNED (wave-based parallel), PARLIAMENT (smart owl
 * selection), and SWARM (parallel specialist owls).
 *
 * All strategies fall back to STANDARD on failure.
 */

import type { OwlInstance } from "../owls/persona.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { ModelProvider } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { PelletStore } from "../pellets/store.js";
import type { EngineContext, EngineResponse } from "../engine/runtime.js";
import type { GatewayCallbacks } from "../gateway/types.js";
import type { ParliamentCallbacks } from "../parliament/protocol.js";
import { OwlEngine } from "../engine/runtime.js";
import { ParliamentOrchestrator } from "../parliament/orchestrator.js";
import type { TaskStrategy, OrchestrationResult, SubTask } from "./types.js";
import { log } from "../logger.js";
import { SwarmBlackboard } from "../swarm/blackboard.js";

// ─── Helpers ─────────────────────────────────────────────────

function toOrchResult(
  r: EngineResponse,
  strategy: TaskStrategy["strategy"],
): OrchestrationResult {
  return {
    content: r.content,
    owlName: r.owlName,
    owlEmoji: r.owlEmoji,
    toolsUsed: r.toolsUsed,
    strategy,
    usage: r.usage,
  };
}

/**
 * Lightweight task-completion detector.
 * Returns false ONLY when the runtime has explicitly signalled failure via
 * EXHAUSTION_MARKER. All other responses — including short ones — are treated
 * as completed, because the runtime's self-correction layer already handles
 * partial results before marking exhaustion.
 *
 * Deliberately conservative: we only escalate when we have a hard signal,
 * not based on heuristics like response length (which breaks short valid answers).
 */
function isTaskComplete(content: string): boolean {
  // Empty content is a clear failure
  if (!content || content.trim().length === 0) return false;

  // EXHAUSTION_MARKER is the only reliable programmatic signal from the runtime
  // that the ReAct loop genuinely gave up after self-correction also failed.
  if (content.includes("__STACKOWL_EXHAUSTED__")) return false;

  // Everything else is considered a completed (or at least partial) answer.
  // The self-correction handler in runtime.ts is responsible for recovery
  // before we ever reach this point.
  return true;
}

// ─── Orchestrator ────────────────────────────────────────────

export class TaskOrchestrator {
  private engine: OwlEngine;

  constructor(
    private owlRegistry: OwlRegistry,
    private provider: ModelProvider,
    private config: StackOwlConfig,
    private pelletStore: PelletStore,
    private toolRegistry?: ToolRegistry,
  ) {
    this.engine = new OwlEngine();
  }

  /**
   * Execute a strategy with automatic STANDARD fallback on failure.
   */
  async executeWithFallback(
    strategy: TaskStrategy,
    userMessage: string,
    baseContext: EngineContext,
    callbacks: GatewayCallbacks,
  ): Promise<OrchestrationResult> {
    try {
      return await this.execute(strategy, userMessage, baseContext, callbacks);
    } catch (err) {
      log.engine.warn(
        `[Orchestrator] ${strategy.strategy} failed, falling back to STANDARD: ` +
          `${err instanceof Error ? err.message : String(err)}`,
      );
      if (callbacks.onProgress) {
        await callbacks.onProgress(
          "Strategy failed, falling back to standard processing...",
        );
      }
      return this.executeStandard(userMessage, baseContext);
    }
  }

  /**
   * Execute a classified strategy.
   */
  async execute(
    strategy: TaskStrategy,
    userMessage: string,
    baseContext: EngineContext,
    callbacks: GatewayCallbacks,
  ): Promise<OrchestrationResult> {
    // Deep research path — pre-injects pellets, uses extended iterations,
    // and runs evaluator-optimizer synthesis at the end.
    if (strategy.depth === "deep") {
      return this.executeDeepResearch(
        userMessage,
        baseContext,
        strategy,
        callbacks,
      );
    }

    switch (strategy.strategy) {
      case "DIRECT":
        return this.executeDirect(userMessage, baseContext);

      case "STANDARD":
        return this.executeStandard(userMessage, baseContext);

      case "SPECIALIST":
        return this.executeSpecialist(
          userMessage,
          baseContext,
          strategy,
          callbacks,
        );

      case "PLANNED":
        return this.executePlanned(
          userMessage,
          baseContext,
          strategy,
          callbacks,
        );

      case "PARLIAMENT":
        return this.executeParliament(
          userMessage,
          baseContext,
          strategy,
          callbacks,
        );

      case "SWARM":
        return this.executeSwarm(userMessage, baseContext, strategy, callbacks);

      default:
        return this.executeStandard(userMessage, baseContext);
    }
  }

  // ─── DEEP RESEARCH ─────────────────────────────────────────────

  /**
   * Deep research execution — pre-injects pellets, uses extended iterations,
   * and runs an evaluator-optimizer synthesis at the end.
   */
  async executeDeepResearch(
    userMessage: string,
    baseContext: EngineContext,
    strategy: TaskStrategy,
    callbacks: GatewayCallbacks,
  ): Promise<OrchestrationResult> {
    const researchConfig = this.config.research ?? {};
    const maxIterations = researchConfig.maxIterations ?? 40;
    const subtopics = strategy.researchSignal?.subtopics ?? [];

    if (callbacks.onProgress) {
      const reason = strategy.researchSignal?.reason ?? "deep research";
      await callbacks.onProgress(
        `🔬 **Deep Research Mode** — ${reason}${subtopics.length > 0 ? ` (${subtopics.length} subtopics)` : ""}`,
      );
    }

    // ── Pre-inject relevant pellets ───────────────────────────
    let pelletContext = "";
    if (this.pelletStore) {
      try {
        const query = subtopics.length > 0 ? subtopics.join(" ") : userMessage;
        const results = await this.pelletStore.searchWithGraph(query, 3);
        if (results.length > 0) {
          pelletContext = results
            .map((r) => `## ${r.title}\n${r.content}\n`)
            .join("\n");
          if (callbacks.onProgress) {
            await callbacks.onProgress(
              `📚 Pre-injecting ${results.length} relevant pellet(s) from prior knowledge`,
            );
          }
        }
      } catch (err) {
        log.engine.warn(`[DeepResearch] Pellet pre-injection failed: ${err}`);
      }
    }

    // ── Build research context ─────────────────────────────────
    const researchPrompt =
      (pelletContext
        ? `## Prior Knowledge (read-only, do not repeat)\n${pelletContext}\n\n`
        : "") +
      `## Your Task\n${userMessage}\n\n` +
      `You are conducting deep research. Use the tools available to find comprehensive information. ` +
      `Check your findings against multiple sources where possible. ` +
      `When you have gathered sufficient evidence, write a thorough, well-structured response.`;

    // ── Execute with extended iteration budget ───────────────
    // NOTE: pelletContext is already embedded in researchPrompt above.
    // Do NOT also inject it into sessionHistory — that causes double injection
    // (model sees the same pellets twice, wasting tokens).
    const deepContext: EngineContext = {
      ...baseContext,
      maxIterations,
      depth: "deep",
      sessionHistory: baseContext.sessionHistory,
      skipGapDetection: true,
    };

    const response = await this.engine.run(researchPrompt, deepContext);

    // ── Evaluator-optimizer: ask "what's missing?" ───────────
    if (
      strategy.researchSignal?.subtopics &&
      strategy.researchSignal.subtopics.length > 1
    ) {
      try {
        if (callbacks.onProgress) {
          await callbacks.onProgress(`🔍 **Evaluator: checking for gaps...**`);
        }
        const gapsPrompt =
          `You just completed research on: ${userMessage}\n\n` +
          `Your findings covered: ${strategy.researchSignal.subtopics.join(", ")}\n\n` +
          `Review your answer and identify: what aspects are NOT covered? What questions would make this research more complete?\n` +
          `Respond with a brief gap analysis (2-3 sentences max) or "none" if you covered the topic thoroughly.`;

        const gapsResponse = await this.provider.chat(
          [{ role: "user", content: gapsPrompt }],
          undefined,
          { temperature: 0, maxTokens: 200 },
        );

        const gapsText = gapsResponse.content.trim().toLowerCase();
        if (gapsText !== "none" && gapsText.length > 10) {
          if (callbacks.onProgress) {
            await callbacks.onProgress(
              `📋 **Gap Analysis:** ${gapsResponse.content.slice(0, 200)}`,
            );
          }
        }
      } catch {
        // Non-fatal
      }
    }

    // ── Proactive "while I was in there" offer ──────────────
    if (callbacks.onProgress) {
      const proactivePrompt =
        `Based on the research just completed about "${userMessage}", identify one surprising or interesting finding that the user might want to know about but didn't explicitly ask.\n` +
        `Respond with ONLY the finding in 1-2 sentences, or "none" if nothing notable was found.`;

      try {
        const proactiveResponse = await this.provider.chat(
          [{ role: "user", content: proactivePrompt }],
          undefined,
          { temperature: 0.3, maxTokens: 100 },
        );
        const finding = proactiveResponse.content.trim();
        if (finding !== "none" && finding.length > 10) {
          await callbacks.onProgress(
            `💡 **While I was researching:** ${finding}\n_(Reply with "tell me more" to explore this)_`,
          );
        }
      } catch {
        // Non-fatal
      }
    }

    return toOrchResult(response, "STANDARD");
  }

  // ─── DIRECT ──────────────────────────────────────────────────

  private async executeDirect(
    userMessage: string,
    baseContext: EngineContext,
  ): Promise<OrchestrationResult> {
    const ctx: EngineContext = {
      ...baseContext,
      skipGapDetection: true,
    };
    const response = await this.engine.run(userMessage, ctx);
    return toOrchResult(response, "DIRECT");
  }

  // ─── STANDARD (with goal loop + strategy escalation) ─────────
  //
  // Instead of calling engine.run() once and returning whatever comes back,
  // we run up to MAX_GOAL_ATTEMPTS times, escalating the strategy each time
  // the previous attempt didn't produce a completed answer:
  //
  //   Attempt 1 → STANDARD  (normal single-engine run)
  //   Attempt 2 → PLANNED   (task planner decomposes into steps)
  //   Attempt 3 → SWARM     (parallel specialist agents)
  //
  // The escalation stops as soon as isTaskComplete() returns true.

  private async executeStandard(
    userMessage: string,
    baseContext: EngineContext,
  ): Promise<OrchestrationResult> {
    const MAX_GOAL_ATTEMPTS = 3;
    const escalationLadder: Array<"STANDARD" | "PLANNED" | "SWARM"> = [
      "STANDARD",
      "PLANNED",
      "SWARM",
    ];

    let lastResult: OrchestrationResult | null = null;

    for (let attempt = 0; attempt < MAX_GOAL_ATTEMPTS; attempt++) {
      const currentStrategy = escalationLadder[attempt];

      if (attempt > 0) {
        log.engine.info(
          `[GoalLoop] Attempt ${attempt + 1}/${MAX_GOAL_ATTEMPTS} — ` +
          `previous attempt incomplete, escalating to ${currentStrategy}`,
        );
        if (baseContext.onProgress) {
          await baseContext.onProgress(
            `🔄 **Retrying with ${currentStrategy} strategy** (attempt ${attempt + 1}/${MAX_GOAL_ATTEMPTS})...`,
          );
        }
      }

      let result: OrchestrationResult;

      if (currentStrategy === "STANDARD") {
        // Plain engine run
        const response = await this.engine.run(userMessage, baseContext);
        result = toOrchResult(response, "STANDARD");

      } else if (currentStrategy === "PLANNED") {
        // Escalate to wave-based planner — let executePlanned decompose the task
        const plannedStrategy: TaskStrategy = {
          strategy: "PLANNED",
          reasoning: `Auto-escalated from failed STANDARD attempt (goal loop attempt ${attempt + 1})`,
          confidence: 0.85,
          depth: baseContext.depth ?? "quick",
          owlAssignments: [
            {
              owlName: baseContext.owl.persona.name,
              role: "lead",
              reasoning: "Goal loop escalation",
            },
          ],
          // No pre-built subtasks — executePlanned will call TaskPlanner to generate them
          subtasks: [],
        };
        const callbacks = { onProgress: baseContext.onProgress };
        result = await this.executePlanned(
          userMessage,
          baseContext,
          plannedStrategy,
          callbacks,
        );

      } else {
        // SWARM: two parallel specialists — one analytical, one executional
        const swarmStrategy: TaskStrategy = {
          strategy: "SWARM",
          reasoning: `Final escalation — parallel specialist attempt (goal loop attempt ${attempt + 1})`,
          confidence: 0.75,
          depth: baseContext.depth ?? "quick",
          owlAssignments: [
            {
              owlName: baseContext.owl.persona.name,
              role: "lead",
              reasoning: "Goal loop escalation",
            },
          ],
          subtasks: [
            {
              id: 1,
              description: `Analyze and plan: ${userMessage}`,
              assignedOwl: baseContext.owl.persona.name,
              dependsOn: [],
              toolsNeeded: [],
            },
            {
              id: 2,
              description: `Execute and deliver: ${userMessage}`,
              assignedOwl: baseContext.owl.persona.name,
              dependsOn: [],
              toolsNeeded: [],
            },
          ],
        };
        const callbacks = { onProgress: baseContext.onProgress };
        result = await this.executeSwarm(
          userMessage,
          baseContext,
          swarmStrategy,
          callbacks,
        );
      }

      lastResult = result;

      if (isTaskComplete(result.content)) {
        if (attempt > 0) {
          log.engine.info(
            `[GoalLoop] Task completed on attempt ${attempt + 1} using ${currentStrategy} strategy.`,
          );
        }
        return result;
      }

      log.engine.warn(
        `[GoalLoop] Attempt ${attempt + 1}/${MAX_GOAL_ATTEMPTS} (${currentStrategy}) ` +
        `did not produce a complete answer.` +
        (attempt + 1 < MAX_GOAL_ATTEMPTS
          ? ` Escalating to ${escalationLadder[attempt + 1]}.`
          : ` All attempts exhausted — returning last result.`),
      );
    }

    // All attempts exhausted — return whatever we have
    return lastResult!;
  }

  // ─── SPECIALIST ──────────────────────────────────────────────

  private async executeSpecialist(
    userMessage: string,
    baseContext: EngineContext,
    strategy: TaskStrategy,
    callbacks: GatewayCallbacks,
  ): Promise<OrchestrationResult> {
    const assignment = strategy.owlAssignments[0];
    const specialistOwl = this.resolveOwl(assignment?.owlName);

    if (
      specialistOwl &&
      specialistOwl.persona.name !== baseContext.owl.persona.name
    ) {
      if (callbacks.onProgress) {
        await callbacks.onProgress(
          `${specialistOwl.persona.emoji} Routing to **${specialistOwl.persona.name}** (${specialistOwl.persona.type}) — ${assignment?.reasoning ?? "specialist match"}`,
        );
      }
    }

    const ctx: EngineContext = {
      ...baseContext,
      owl: specialistOwl ?? baseContext.owl,
    };

    const response = await this.engine.run(userMessage, ctx);
    return toOrchResult(response, "SPECIALIST");
  }

  // ─── PLANNED (wave-based parallel) ──────────────────────────

  private async executePlanned(
    userMessage: string,
    baseContext: EngineContext,
    strategy: TaskStrategy,
    callbacks: GatewayCallbacks,
  ): Promise<OrchestrationResult> {
    let subtasks = strategy.subtasks;

    // Fallback: use TaskPlanner if classifier didn't provide subtasks
    if (!subtasks || subtasks.length === 0) {
      const { TaskPlanner } = await import("../engine/planner.js");
      const planner = new TaskPlanner(this.provider);
      const tools = baseContext.toolRegistry?.getAllDefinitions() ?? [];
      const plan = await planner.createPlan(userMessage, tools);

      subtasks = plan.steps.map((s) => ({
        id: s.id,
        description: s.description,
        assignedOwl: baseContext.owl.persona.name,
        dependsOn: s.dependsOn,
        toolsNeeded: s.toolsNeeded,
      }));
    }

    if (subtasks.length <= 1) {
      // Single step — just run normally
      return this.executeStandard(userMessage, baseContext);
    }

    // Build waves from dependency graph
    const waves = this.buildWaves(subtasks);

    if (callbacks.onProgress) {
      await callbacks.onProgress(
        `📋 **Planned execution:** ${subtasks.length} subtasks in ${waves.length} wave(s)`,
      );
    }

    const allToolsUsed: string[] = [];
    const subtaskResults: OrchestrationResult["subtaskResults"] = [];
    const completedResults = new Map<number, string>();

    for (let w = 0; w < waves.length; w++) {
      const wave = waves[w];

      if (callbacks.onProgress) {
        const taskNames = wave
          .map((t) => t.description.slice(0, 50))
          .join(", ");
        await callbacks.onProgress(
          `⚡ **Wave ${w + 1}/${waves.length}** (${wave.length} parallel): ${taskNames}`,
        );
      }

      // Execute all tasks in this wave in parallel
      const results = await Promise.allSettled(
        wave.map(async (task) => {
          const owl = this.resolveOwl(task.assignedOwl) ?? baseContext.owl;

          // Build context with completed results from prior waves
          const priorContext = Array.from(completedResults.entries())
            .map(
              ([id, result]) => `[Step ${id} result]: ${result.slice(0, 300)}`,
            )
            .join("\n");

          const stepPrompt =
            `[TASK PLAN — Step ${task.id}/${subtasks!.length}]\n` +
            (priorContext ? `Prior results:\n${priorContext}\n\n` : "") +
            `CURRENT STEP: ${task.description}\n` +
            `Focus ONLY on completing this step.`;

          const ctx: EngineContext = {
            ...baseContext,
            owl,
            sessionHistory: [
              { role: "system", content: stepPrompt },
              ...baseContext.sessionHistory.slice(-4),
            ],
            skipGapDetection: true,
            isolatedTask: true,
          };

          return {
            task,
            response: await this.engine.run(task.description, ctx),
          };
        }),
      );

      // Process results
      for (const result of results) {
        if (result.status === "fulfilled") {
          const { task, response } = result.value;
          completedResults.set(task.id, response.content);
          allToolsUsed.push(...response.toolsUsed);
          subtaskResults.push({
            id: task.id,
            owlName: response.owlName,
            status: "done",
            content: response.content,
          });

          if (callbacks.onProgress) {
            await callbacks.onProgress(
              `✅ Step ${task.id} complete: ${task.description.slice(0, 60)}`,
            );
          }
        } else {
          const task = wave[results.indexOf(result)];
          subtaskResults.push({
            id: task.id,
            owlName: task.assignedOwl,
            status: "failed",
            content: result.reason?.message ?? String(result.reason),
          });

          if (callbacks.onProgress) {
            await callbacks.onProgress(
              `❌ Step ${task.id} failed: ${task.description.slice(0, 60)}`,
            );
          }
        }
      }
    }

    // Final synthesis
    const completedSteps = subtaskResults.filter((r) => r.status === "done");
    if (completedSteps.length === 0) {
      return {
        content:
          "All planned steps failed. Please try rephrasing your request.",
        owlName: baseContext.owl.persona.name,
        owlEmoji: baseContext.owl.persona.emoji,
        toolsUsed: allToolsUsed,
        strategy: "PLANNED",
        subtaskResults,
      };
    }

    const synthesisPrompt =
      `You executed a multi-step plan for the user. Combine the results into a clear, cohesive response.\n\n` +
      `Original request: ${userMessage}\n\n` +
      subtaskResults
        .map(
          (r) =>
            `Step ${r.id} (${r.status}, ${r.owlName}): ${r.content.slice(0, 500)}`,
        )
        .join("\n\n") +
      `\n\nProvide a clear summary. If any steps failed, mention what couldn't be completed.`;

    const synthesisResponse = await this.engine.run(synthesisPrompt, {
      ...baseContext,
      skipGapDetection: true,
      isolatedTask: true,
    });

    return {
      content: synthesisResponse.content,
      owlName: synthesisResponse.owlName,
      owlEmoji: synthesisResponse.owlEmoji,
      toolsUsed: [...new Set(allToolsUsed)],
      strategy: "PLANNED",
      subtaskResults,
      usage: synthesisResponse.usage,
    };
  }

  // ─── PARLIAMENT ──────────────────────────────────────────────

  private async executeParliament(
    userMessage: string,
    baseContext: EngineContext,
    strategy: TaskStrategy,
    callbacks: GatewayCallbacks,
  ): Promise<OrchestrationResult> {
    // Resolve participants from classifier assignments
    const participants: OwlInstance[] = [];
    for (const assignment of strategy.owlAssignments) {
      const owl = this.resolveOwl(assignment.owlName);
      if (owl) participants.push(owl);
    }

    // Ensure at least 2 participants
    if (participants.length < 2) {
      const allOwls = this.owlRegistry.listOwls();
      if (allOwls.length < 2) {
        return this.executeStandard(userMessage, baseContext);
      }
      participants.length = 0;
      participants.push(...allOwls.slice(0, 4));
    }

    const topic = strategy.parliamentConfig?.topic ?? userMessage;

    if (callbacks.onProgress) {
      const owlNames = participants
        .map((o) => `${o.persona.emoji} ${o.persona.name}`)
        .join(", ");
      await callbacks.onProgress(
        `🏛️ Convening Parliament with ${participants.length} owls: ${owlNames}`,
      );
    }

    // Build streaming callbacks
    const onProgress = callbacks.onProgress;
    const parliamentCallbacks: ParliamentCallbacks | undefined = onProgress
      ? {
          onRoundStart: async (_round, phase) => {
            const labels: Record<string, string> = {
              round1_position: "📢 Round 1: Initial Positions",
              round2_challenge: "⚔️ Round 2: Cross-Examination",
              round3_synthesis: "🔮 Round 3: Synthesis",
            };
            await onProgress(
              `\n🏛️ **Parliament** — ${labels[phase] || `Round ${_round}`}`,
            );
          },
          onPositionReady: async (position) => {
            await onProgress(
              `${position.owlEmoji} **${position.owlName}** [${position.position}]: ${position.argument}`,
            );
          },
          onChallengeReady: async (challenge) => {
            await onProgress(
              `⚔️ **${challenge.owlName}** challenges ${challenge.targetOwl}: ${challenge.challengeContent}`,
            );
          },
          onSynthesisReady: async (synthesis, verdict) => {
            await onProgress(`📋 **Verdict: [${verdict}]**\n${synthesis}`);
          },
        }
      : undefined;

    const orchestrator = new ParliamentOrchestrator(
      this.provider,
      this.config,
      this.pelletStore,
      this.toolRegistry,
      baseContext.db,
    );

    const session = await orchestrator.convene({
      topic,
      participants,
      contextMessages: baseContext.sessionHistory.map((m) => ({
        role: m.role,
        content: m.content ?? "",
      })),
      callbacks: parliamentCallbacks,
    });

    const content = orchestrator.formatSessionMarkdown(session);

    return {
      content,
      owlName: baseContext.owl.persona.name,
      owlEmoji: baseContext.owl.persona.emoji,
      toolsUsed: ["summon_parliament"],
      strategy: "PARLIAMENT",
    };
  }

  // ─── SWARM (parallel specialist owls) ────────────────────────

  private async executeSwarm(
    userMessage: string,
    baseContext: EngineContext,
    strategy: TaskStrategy,
    callbacks: GatewayCallbacks,
  ): Promise<OrchestrationResult> {
    const subtasks = strategy.subtasks;
    if (!subtasks || subtasks.length === 0) {
      return this.executeStandard(userMessage, baseContext);
    }

    // Degenerate case: single subtask → SPECIALIST
    if (subtasks.length === 1) {
      const owl = this.resolveOwl(subtasks[0].assignedOwl);
      if (owl) {
        return this.executeSpecialist(
          userMessage,
          baseContext,
          strategy,
          callbacks,
        );
      }
      return this.executeStandard(userMessage, baseContext);
    }

    if (callbacks.onProgress) {
      const assignments = subtasks.map((t) => {
        const owl = this.resolveOwl(t.assignedOwl);
        return `${owl?.persona.emoji ?? "🦉"} ${t.assignedOwl}: ${t.description.slice(0, 50)}`;
      });
      await callbacks.onProgress(
        `🐝 **Swarm activated** — ${subtasks.length} parallel tasks:\n${assignments.join("\n")}`,
      );
    }

    // Create shared blackboard for inter-agent communication
    const blackboard = new SwarmBlackboard();

    // Run all subtasks in parallel with blackboard access
    const results = await Promise.allSettled(
      subtasks.map(async (task) => {
        const owl = this.resolveOwl(task.assignedOwl) ?? baseContext.owl;

        // Inject blackboard context into the prompt
        const sharedContext = blackboard.toSummary();
        const focusedPrompt =
          `[SWARM TASK] You are ${owl.persona.name} (${owl.persona.type}). ` +
          `Focus exclusively on this subtask:\n\n` +
          `${task.description}\n\n` +
          `Original user request for context: "${userMessage}"\n\n` +
          (sharedContext
            ? `Shared context from other agents:\n${sharedContext}\n\n`
            : "") +
          `Provide your specialist analysis. Be thorough but concise.`;

        const ctx: EngineContext = {
          ...baseContext,
          owl,
          sessionHistory: baseContext.sessionHistory.slice(-4),
          skipGapDetection: true,
          isolatedTask: true,
        };

        const response = await this.engine.run(focusedPrompt, ctx);

        // Write result to blackboard for other agents to see
        blackboard.write(
          `step_${task.id}_result`,
          response.content.slice(0, 500),
          owl.persona.name,
        );

        if (callbacks.onProgress) {
          await callbacks.onProgress(
            `${owl.persona.emoji} **${owl.persona.name}** completed: ${task.description.slice(0, 50)}`,
          );
        }

        return { task, response };
      }),
    );

    // Collect results
    const allToolsUsed: string[] = [];
    const subtaskResults: OrchestrationResult["subtaskResults"] = [];

    for (const result of results) {
      if (result.status === "fulfilled") {
        const { task, response } = result.value;
        allToolsUsed.push(...response.toolsUsed);
        subtaskResults.push({
          id: task.id,
          owlName: response.owlName,
          status: "done",
          content: response.content,
        });
      } else {
        const idx = results.indexOf(result);
        const task = subtasks[idx];
        subtaskResults.push({
          id: task.id,
          owlName: task.assignedOwl,
          status: "failed",
          content: result.reason?.message ?? String(result.reason),
        });
      }
    }

    // Synthesis: Noctua merges all specialist results
    const completedResults = subtaskResults.filter((r) => r.status === "done");
    if (completedResults.length === 0) {
      return {
        content: "All swarm tasks failed. Please try rephrasing your request.",
        owlName: baseContext.owl.persona.name,
        owlEmoji: baseContext.owl.persona.emoji,
        toolsUsed: allToolsUsed,
        strategy: "SWARM",
        subtaskResults,
      };
    }

    const synthesisPrompt =
      `Multiple specialist agents worked on parts of the user's request in parallel. ` +
      `Combine their results into a single cohesive response.\n\n` +
      `Original request: "${userMessage}"\n\n` +
      completedResults
        .map((r) => `--- ${r.owlName}'s analysis ---\n${r.content}`)
        .join("\n\n") +
      `\n\nShared blackboard context from execution:\n${blackboard.toSummary()}` +
      `\n\nSynthesize these into a clear, unified answer. Credit each specialist's contribution where relevant.`;

    // Clean up blackboard
    blackboard.clear();

    if (callbacks.onProgress) {
      await callbacks.onProgress(
        "🔮 Synthesizing results from all specialists...",
      );
    }

    const synthesisResponse = await this.engine.run(synthesisPrompt, {
      ...baseContext,
      skipGapDetection: true,
      isolatedTask: true,
    });

    return {
      content: synthesisResponse.content,
      owlName: synthesisResponse.owlName,
      owlEmoji: synthesisResponse.owlEmoji,
      toolsUsed: [...new Set(allToolsUsed)],
      strategy: "SWARM",
      subtaskResults,
      usage: synthesisResponse.usage,
    };
  }

  // ─── Helpers ─────────────────────────────────────────────────

  /**
   * Build execution waves from subtask dependency graph.
   * Each wave contains tasks whose dependencies are ALL in prior waves.
   */
  private buildWaves(subtasks: SubTask[]): SubTask[][] {
    const waves: SubTask[][] = [];
    const completed = new Set<number>();
    const remaining = [...subtasks];

    while (remaining.length > 0) {
      const wave = remaining.filter((t) =>
        t.dependsOn.every((dep) => completed.has(dep)),
      );

      if (wave.length === 0) {
        // Circular dependency or orphaned tasks — force-add remaining
        log.engine.warn(
          `[Orchestrator] Circular dependency detected in ${remaining.length} remaining tasks, forcing execution`,
        );
        waves.push([...remaining]);
        break;
      }

      waves.push(wave);
      for (const t of wave) {
        completed.add(t.id);
        const idx = remaining.indexOf(t);
        if (idx >= 0) remaining.splice(idx, 1);
      }
    }

    return waves;
  }

  /**
   * Resolve an owl by name from the registry.
   */
  private resolveOwl(name: string): OwlInstance | undefined {
    try {
      return this.owlRegistry.get(name);
    } catch {
      // Try case-insensitive
      const all = this.owlRegistry.listOwls();
      return all.find(
        (o) => o.persona.name.toLowerCase() === name.toLowerCase(),
      );
    }
  }
}

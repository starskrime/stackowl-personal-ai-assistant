/**
 * StackOwl — Owl Engine Runtime
 *
 * The core AI loop: ReAct (Receive → Think → Act → Observe → Respond)
 * with integrated Challenge Mode, sliding context window, and pellet injection.
 */

import type {
  ModelProvider,
  ChatMessage,
  ChatResponse,
  StreamEvent,
  ToolCall,
} from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { PelletStore } from "../pellets/store.js";
import type { ProviderRegistry } from "../providers/registry.js";
import type { AttemptLog } from "../memory/attempt-log.js";
import { ModelRouter } from "./router.js";
import { GapDetector } from "../evolution/detector.js";
import { RewardEngine } from "./reward-engine.js";
import { log } from "../logger.js";
import type { OwlInnerLife, InnerMonologue } from "../owls/inner-life.js";
import { DNADecisionLayer } from "../owls/decision-layer.js";
import { DiagnosticEngine } from "./diagnostic-engine.js";
import type { DiagnosticInput } from "./diagnostic-engine.js";

// ─── Types ───────────────────────────────────────────────────────

export interface PendingFile {
  path: string;
  caption?: string;
}

export interface EngineContext {
  provider: ModelProvider;
  owl: OwlInstance;
  sessionHistory: ChatMessage[];
  config: StackOwlConfig;
  toolRegistry?: ToolRegistry;
  owlRegistry?: OwlRegistry;
  pelletStore?: PelletStore;
  /** Ledger for recording tool usage after each synthesized tool call */
  capabilityLedger?: CapabilityLedger;
  cwd?: string;
  /** When true, skip gap detection entirely (used during evolution retries and proactive pings) */
  skipGapDetection?: boolean;
  /** Optional callback to stream intermediate reasoning and tool execution status to the user interface */
  onProgress?: (msg: string) => Promise<void>;
  /** Optional persistent memory content to inject into system prompt */
  memoryContext?: string;
  /** User preferences to inject into system prompt (from PreferenceStore.toContextString()) */
  preferencesContext?: string;
  /** Skills context to inject into system prompt (from SkillContextInjector.formatForSystemPrompt()) */
  skillsContext?: string;
  /** Files queued for delivery by send_file tool calls during this run */
  pendingFiles?: PendingFile[];
  /** Skills registry — used by CapabilityNeedAssessor to check coverage before synthesis */
  skillsRegistry?: import("../skills/registry.js").SkillsRegistry;
  /** Skill usage tracker — records selection, success, failure events */
  skillTracker?: import("../skills/tracker.js").SkillTracker;
  /** Provider registry to fetch fallback providers dynamically for cross-provider routing */
  providerRegistry?: ProviderRegistry;
  /** When true, isolate this task from previous context - ignore session history */
  isolatedTask?: boolean;
  /**
   * Cross-turn attempt log for this session.
   * Tracks every tool call + outcome across ALL messages so the model
   * never repeats a failed approach from a previous turn.
   * Injected into the system prompt as a synthesized "what was tried" block.
   */
  attemptLog?: AttemptLog;
  /**
   * Fine-grained streaming callback for real-time text + tool events.
   * When the provider supports chatWithToolsStream(), events are emitted
   * here as they arrive. Channels use this for edit-in-place streaming.
   */
  onStreamEvent?: (event: StreamEvent) => Promise<void>;
  /** Memory thread searcher for conversational recall */
  memorySearcher?: import("../memory-threads/searcher.js").MemorySearcher;
  /** Echo chamber detector for bias analysis */
  echoChamberDetector?: import("../echo-chamber/detector.js").EchoChamberDetector;
  /** Growth journal generator */
  journalGenerator?: import("../growth-journal/generator.js").JournalGenerator;
  /** Quest manager for gamified learning */
  questManager?: import("../quests/manager.js").QuestManager;
  /** Time capsule manager */
  capsuleManager?: import("../capsules/manager.js").CapsuleManager;
  /** Owl's inner life — desires, mood, opinions, inner monologue */
  innerLife?: OwlInnerLife;
  /** Persistent goal graph — tracks user objectives across sessions */
  goalGraph?: import("../goals/graph.js").GoalGraph;
  /** Persistent task store — enables checkpoint/resume for long-running tasks */
  taskStore?: import("../tasks/store.js").TaskStore;
  /** Current background task ID (set when running as a background task) */
  backgroundTaskId?: string;
  /** RAG-based pellet search — replaces brute-force pellet injection */
  pelletSearch?: import("../pellets/search.js").PelletSearch;
  /** Diagnostic engine for multi-hypothesis error analysis */
  diagnosticEngine?: DiagnosticEngine;
  /** Depth mode: "quick" (default) or "deep" (multi-iteration research with self-check) */
  depth?: "quick" | "deep";
  /** Override max tool iterations for deep research (default: 40 vs 15 for quick) */
  maxIterations?: number;
  /**
   * Name of the active delivery channel (e.g. "telegram", "cli", "web").
   * Injected into the system prompt so the LLM knows which channel it is
   * operating in and can give accurate advice when channel-specific operations fail.
   */
  channelName?: string;
  /** FactStore — for remember() tool write path and recall() tool read path */
  factStore?: import("../memory/fact-store.js").FactStore;
  /** EpisodicMemory — for recall() tool read path */
  episodicMemory?: import("../memory/episodic.js").EpisodicMemory;
  /** Active userId — used by remember/recall tools for per-user scoping */
  userId?: string;
  /** SQLite DB — gives tools direct write access to owl_learnings etc. */
  db?: import("../memory/db.js").MemoryDatabase;
  /** Session identifier — passed from the gateway, used for TaskState scoping */
  sessionId?: string;
}

export interface PendingCapabilityGap {
  /** Name of the tool the LLM tried to call that doesn't exist, if any */
  attemptedToolName?: string;
  /** The user's original request */
  userRequest: string;
  /** LLM description of why it couldn't help */
  description: string;
}

export interface EngineResponse {
  content: string;
  owlName: string;
  owlEmoji: string;
  challenged: boolean;
  toolsUsed: string[];
  modelUsed: string;
  /** The array of thoughts, tool calls, and observations generated during this run */
  newMessages: ChatMessage[];
  usage?: {
    promptTokens: number;
    completionTokens: number;
  };
  /** Set when the engine detected a capability gap that needs user approval to resolve */
  pendingCapabilityGap?: PendingCapabilityGap;
  /** True when the ReAct loop hit MAX_TOOL_ITERATIONS or broke due to repeated failures */
  loopExhausted?: boolean;
  /** Number of tool failures during this run (for quality signal) */
  toolFailureCount?: number;
  /** Files queued for delivery by send_file tool calls during this run */
  pendingFiles: PendingFile[];
}

// ─── Constants ───────────────────────────────────────────────────

/** Default max iterations — overridable via config.engine.maxToolIterations */
const DEFAULT_MAX_TOOL_ITERATIONS = 15;
/** Deep research max iterations — 40 as per research config */
const DEFAULT_DEEP_MAX_TOOL_ITERATIONS = 40;

/**
 * OpenCLAW-style completion signal.
 * The model is instructed to end its content with [DONE] when it has a complete
 * answer and does not need any further tool calls. The engine checks this BEFORE
 * executing tool calls in each iteration — if the signal is present, all pending
 * tool calls are dropped and the loop exits immediately.
 */
const DONE_SIGNAL = "[DONE]";

function hasDoneSignal(content: string): boolean {
  if (!content.includes(DONE_SIGNAL)) return false;
  const lines = content.split("\n");
  const lastLine = lines[lines.length - 1].trim();
  return lastLine === DONE_SIGNAL || content.endsWith(DONE_SIGNAL);
}

function stripDoneSignal(content: string): string {
  if (!hasDoneSignal(content)) return content;
  const lastNewline = content.lastIndexOf("\n");
  if (lastNewline === -1) return "";
  return content.slice(0, lastNewline).trim();
}

// ─── Self-Assessment Engine ───────────────────────────────────

type SelfCheckVerdict = "CONTINUE" | "PIVOT" | "SYNTHESIZE";

interface SelfCheckInput {
  lastToolName: string;
  lastToolResult: string;
  recentToolResults: string[];
  userMessage: string;
  iterationsUsed: number;
  maxIterations: number;
  similarityThreshold: number;
}

function shouldSkipSelfCheck(iterations: number, interval: number): boolean {
  return iterations === 0 || (iterations + 1) % interval !== 0;
}

function detectDiminishingReturns(
  results: string[],
  threshold: number,
): boolean {
  if (results.length < 3) return false;
  const last3 = results.slice(-3);
  const sim = (a: string, b: string) => {
    const wordsA = new Set(a.match(/\b[a-z]{3,}\b/gi) ?? []);
    const wordsB = new Set(b.match(/\b[a-z]{3,}\b/gi) ?? []);
    const intersection = [...wordsA].filter((w) => wordsB.has(w)).length;
    const union = wordsA.size + wordsB.size - intersection;
    return union === 0 ? 0 : intersection / union;
  };
  const s12 = sim(last3[0], last3[1]);
  const s23 = sim(last3[1], last3[2]);
  return s12 >= threshold && s23 >= threshold;
}

async function runSelfAssessment(
  provider: ModelProvider,
  input: SelfCheckInput,
): Promise<SelfCheckVerdict> {
  const prompt =
    `You are a research progress assessor. After a tool execution, assess whether the research is making progress.\n\n` +
    `Original user request: ${input.userMessage.slice(0, 200)}\n` +
    `Last tool used: ${input.lastToolName}\n` +
    `Last tool result (first 300 chars): ${input.lastToolResult.slice(0, 300)}\n` +
    `Iterations used: ${input.iterationsUsed}/${input.maxIterations}\n\n` +
    `Assess:\n` +
    `1. Am I finding NEW information or repeating what I already know?\n` +
    `2. Is my answer getting more complete or am I hitting diminishing returns?\n` +
    `3. Should I continue this research path, pivot to a different angle, or synthesize now?\n\n` +
    `Respond with ONLY one word: CONTINUE if I should keep researching, PIVOT if I should change approach, SYNTHESIZE if I have enough to answer the user.`;

  try {
    const result = await Promise.race([
      provider.chat([{ role: "user", content: prompt }], undefined, {
        temperature: 0,
        maxTokens: 10,
      }),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("self-check timeout")), 3000),
      ),
    ]);
    const verdict = result.content.trim().toUpperCase();
    if (verdict.startsWith("CONTINUE")) return "CONTINUE";
    if (verdict.startsWith("PIVOT")) return "PIVOT";
    if (verdict.startsWith("SYNTHESIZE")) return "SYNTHESIZE";
    return "CONTINUE";
  } catch {
    return "CONTINUE";
  }
}

function safeStringify(args: unknown): string {
  const seen = new WeakSet();
  return JSON.stringify(args, (_key, value) => {
    if (typeof value === "object" && value !== null) {
      if (seen.has(value)) return "[circular]";
      seen.add(value);
    }
    return value;
  });
}

/**
 * Returns true when a tool returned structured error output rather than throwing.
 * Shells and sandboxes return results as strings even on failure, so the engine
 * must detect these "soft failures" to trigger the analysis/retry logic.
 */
function isFailureResult(result: string): boolean {
  // Non-zero EXIT_CODE from shell/sandbox tools
  const exitMatch = result.match(/EXIT_CODE:\s*(\d+)/);
  if (exitMatch && parseInt(exitMatch[1], 10) !== 0) return true;
  // Explicit diagnostic hint = tool detected a known failure condition
  if (result.includes("[SYSTEM DIAGNOSTIC HINT:")) return true;
  // Detect common error prefixes and patterns
  const lower = result.toLowerCase();
  const failurePrefixes = [
    "error:",
    "failed:",
    "failure:",
    "exception:",
    "crash:",
  ];
  for (const prefix of failurePrefixes) {
    if (lower.startsWith(prefix)) return true;
  }
  // Detect permission/auth failures
  const failurePatterns = [
    "denied:",
    "unauthorized:",
    "forbidden:",
    "not found:",
    "cannot find",
    "unable to",
    "failed to",
    "timeout",
    "connection refused",
  ];
  for (const pattern of failurePatterns) {
    if (lower.includes(pattern)) return true;
  }
  return false;
}
/**
 * Classify a tool failure result as TRANSIENT (worth retrying with a different approach)
 * or NON-RETRYABLE (will always fail — stop and tell the user).
 * This classification is injected into the error analysis prompt to help the model decide
 * whether to try again vs escalate immediately.
 */
function classifyToolError(result: string): "TRANSIENT" | "NON-RETRYABLE" {
  const lower = result.toLowerCase();
  const nonRetryablePatterns = [
    "permission denied",
    "access denied",
    "forbidden",
    "command not found",
    "not found: ",
    "enoent",
    "no such file",
    "cannot find",
    "tool not available",
    "not installed",
    "not supported",
    "unsupported",
    "syntax error",
    "invalid argument",
    "illegal option",
  ];
  const transientPatterns = [
    "timeout",
    "timed out",
    "econnreset",
    "econnrefused",
    "network",
    "rate limit",
    "too many requests",
    "429",
    "temporarily",
    "retry",
    "service unavailable",
    "503",
  ];
  if (transientPatterns.some((p) => lower.includes(p))) return "TRANSIENT";
  if (nonRetryablePatterns.some((p) => lower.includes(p)))
    return "NON-RETRYABLE";
  return "TRANSIENT"; // default: assume worth trying a different approach
}

const CONTEXT_WINDOW_THRESHOLD = 20;
const CONTEXT_COMPRESSION_BATCH = 10;

/** Simple token estimator: ~4 chars per token */
function estimateTokens(messages: ChatMessage[]): number {
  let chars = 0;
  for (const m of messages) {
    chars += (m.content?.length ?? 0) + 10; // 10 for role/overhead
  }
  return Math.ceil(chars / 4);
}

/**
 * Marker embedded in the response content when the ReAct loop exhausted all
 * iterations or broke due to repeated failures. The gateway uses this to
 * track stuck tasks across consecutive messages.
 */
export const EXHAUSTION_MARKER = "__STACKOWL_EXHAUSTED__";

// ─── Streaming Helper ────────────────────────────────────────────

/**
 * Consume a chatWithToolsStream() generator and accumulate a full ChatResponse
 * while emitting StreamEvents to the callback in real-time.
 *
 * This lets the engine use the same ReAct loop logic regardless of whether
 * the response was streamed or synchronous.
 */
async function consumeStream(
  stream: AsyncGenerator<StreamEvent>,
  onEvent?: (event: StreamEvent) => Promise<void>,
): Promise<ChatResponse> {
  let content = "";
  const toolCalls: ToolCall[] = [];
  const toolCallMap = new Map<
    string,
    { id: string; name: string; argsStr: string }
  >();
  let usage:
    | { promptTokens: number; completionTokens: number; totalTokens: number }
    | undefined;
  let model = "";

  for await (const event of stream) {
    // Emit to channel in real-time
    if (onEvent) {
      await onEvent(event).catch(() => {});
    }

    switch (event.type) {
      case "text_delta":
        content += event.content;
        break;
      case "tool_start":
        toolCallMap.set(event.toolCallId, {
          id: event.toolCallId,
          name: event.toolName,
          argsStr: "",
        });
        break;
      case "tool_args_delta": {
        const tc = toolCallMap.get(event.toolCallId);
        if (tc) tc.argsStr += event.argsDelta;
        break;
      }
      case "tool_end": {
        toolCalls.push({
          id: event.toolCallId,
          name: event.toolName,
          arguments: event.arguments,
        });
        toolCallMap.delete(event.toolCallId);
        break;
      }
      case "done":
        if (event.usage) usage = event.usage;
        break;
    }
  }

  return {
    content,
    toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
    model,
    finishReason: toolCalls.length > 0 ? "tool_calls" : "stop",
    usage,
  };
}

// ─── Owl Engine ──────────────────────────────────────────────────

export class OwlEngine {
  /**
   * Run the full ReAct + Challenge loop for a user message.
   */
  async run(
    userMessage: string,
    context: EngineContext,
  ): Promise<EngineResponse> {
    const { provider, owl, sessionHistory, config, toolRegistry, cwd } =
      context;
    const toolsUsed: string[] = [];
    const gapDetector = new GapDetector();
    let MAX_TOOL_ITERATIONS =
      context.maxIterations ??
      (context.depth === "deep"
        ? (config.research?.maxIterations ?? 40)
        : DEFAULT_MAX_TOOL_ITERATIONS);

    // Track if a missing-tool gap was encountered during the ReAct loop
    let missingToolName: string | undefined;

    // ── TrajectoryStore — begin a new trace for this ReAct loop ───
    let trajectoryId: string | undefined;
    let trajectoryTurnIndex = 0;
    let trajectoryToolSuccessCount = 0;
    let trajectoryToolFailureCount = 0;
    if (context.db && context.sessionId) {
      try {
        trajectoryId = context.db.trajectories.begin(
          context.sessionId,
          owl.persona.name,
          userMessage,
          context.userId,
        );
      } catch {
        /* non-fatal */
      }
    }

    // ── Tool result buffer for diminishing returns detection ──
    const toolResultsBuffer: string[] = [];
    let deeperExtended = false;

    // 1. Determine optimal model (heuristic, no LLM call)
    let routeDecision = ModelRouter.route(userMessage, config);
    let optimalModel = routeDecision.modelName;

    // Dynamic provider resolution based on route (if cross-provider routing is needed early)
    let currentProvider = provider;
    if (
      routeDecision.providerName &&
      routeDecision.providerName !== provider.name &&
      context.providerRegistry
    ) {
      log.engine.warn(
        `Cross-provider routing on first turn: Swapping ${provider.name} for ${routeDecision.providerName}`,
      );
      currentProvider = context.providerRegistry.get(
        routeDecision.providerName,
      );
    }

    log.engine.model(optimalModel);

    // 1c. DNA Decision Layer — compute DNA-driven runtime decisions
    // This drives: token budget, temperature adjustment, style directives,
    // tool prioritization, risk tolerance. Previously computed but never used.
    const dnaDecisions = DNADecisionLayer.decide(owl, userMessage);

    // 1b. Inner Monologue — use the PREVIOUS turn's monologue (computed async after
    // the last response). This avoids blocking the current response on an extra LLM call.
    // thinkInBackground() is fired after the response is sent (in gateway/core.ts),
    // so by the time the user sends their next message the monologue is ready.
    const innerMonologue = context.innerLife?.getLastMonologue() ?? undefined;
    if (innerMonologue) {
      log.engine.debug(
        `[InnerLife] Using cached monologue: "${innerMonologue.thoughts.slice(0, 80)}..."`,
      );
    }

    // 2. Build system prompt (async — may inject pellets + memory + skills)
    // Signal new turn to attempt log BEFORE building the prompt so the injected
    // block reflects the correct current turn number.
    context.attemptLog?.newTurn();
    const attemptLogBlock = context.attemptLog?.toPromptBlock() ?? "";

    const systemPrompt = await this.buildSystemPrompt(
      owl,
      toolRegistry,
      context.pelletStore,
      userMessage,
      context.memoryContext,
      context.preferencesContext,
      context.skillsContext,
      attemptLogBlock,
      context.innerLife,
      innerMonologue,
      context.pelletSearch,
      context.channelName,
    );

    // Append DNA-driven style directive from DNADecisionLayer
    // This provides programmatic style guidance (humor level, formality, examples, next steps)
    // vs. the hardcoded challenge/verbosity directives already in buildSystemPrompt
    const dnaStyleDirective = DNADecisionLayer.toStyleDirective(dnaDecisions);
    const finalSystemPrompt = dnaStyleDirective
      ? systemPrompt +
        "\n\n## Response Style (DNA-Driven)\n\n" +
        dnaStyleDirective
      : systemPrompt;

    // 2c. TaskState — initialize or load for this session and inject into system prompt.
    // TaskState is the model's persistent structured working memory: what the goal is,
    // which approaches have been tried and failed (NEVER retry these), and a step log.
    // It survives context compression because it's in SQLite, not the context window.
    let taskState: import("../memory/db.js").TaskState | null = null;
    let taskStateBlock = "";
    if (context.db && context.sessionId) {
      try {
        taskState = context.db.taskStates.get(context.sessionId);
        if (!taskState) {
          // First message in this session — create a new task state
          const now = new Date().toISOString();
          taskState = {
            sessionId: context.sessionId,
            owlName: owl.persona.name,
            goal: userMessage.slice(0, 300),
            plannedApproaches: [],
            eliminatedApproaches: [],
            stepLog: [],
            status: "active",
            createdAt: now,
            updatedAt: now,
          };
          context.db.taskStates.save(taskState);
        }
        const hasTaskContext =
          taskState.eliminatedApproaches.length > 0 ||
          taskState.stepLog.length > 0 ||
          taskState.plannedApproaches.length > 0;
        if (hasTaskContext) {
          taskStateBlock =
            `\n\n<task_state>\n` +
            `Goal: ${taskState.goal}\n` +
            (taskState.plannedApproaches.length > 0
              ? `Known failure patterns (avoid these):\n${taskState.plannedApproaches.map((a) => `  ⚠ ${a}`).join("\n")}\n`
              : "") +
            (taskState.eliminatedApproaches.length > 0
              ? `ELIMINATED this session (do NOT retry):\n${taskState.eliminatedApproaches.map((a) => `  ✗ ${a}`).join("\n")}\n`
              : "") +
            (taskState.stepLog.length > 0
              ? `Recent steps (newest first):\n${taskState.stepLog
                  .slice(0, 5)
                  .map((s) => `  ✓ ${s}`)
                  .join("\n")}\n`
              : "") +
            `</task_state>`;
        }
      } catch {
        // Non-fatal — TaskState is optional enrichment
      }
    }

    const finalSystemPromptWithTaskState = taskStateBlock
      ? finalSystemPrompt + taskStateBlock
      : finalSystemPrompt;

    // 2b. Sanitize history — remove references to tools that no longer exist.
    // Stale tool calls from defunct tools poison the context and confuse local models.
    const currentToolDefs = toolRegistry?.getAllDefinitions();
    const validToolNames = currentToolDefs
      ? new Set(currentToolDefs.map((t) => t.name))
      : new Set<string>();
    const sanitizedHistory = toolRegistry
      ? sessionHistory.filter((msg) => {
          // Keep non-tool messages
          if (msg.role !== "tool" && msg.role !== "assistant") return true;
          // Drop assistant messages that ONLY contain calls to missing tools
          if (msg.role === "assistant" && msg.toolCalls?.length) {
            const allStale = msg.toolCalls.every(
              (tc) => !validToolNames.has(tc.name),
            );
            if (allStale && !msg.content?.trim()) return false;
          }
          // Drop tool result messages for missing tools
          if (
            msg.role === "tool" &&
            msg.name &&
            !validToolNames.has(msg.name)
          ) {
            return false;
          }
          return true;
        })
      : sessionHistory;

    if (sanitizedHistory.length < sessionHistory.length) {
      log.engine.info(
        `Sanitized history: removed ${sessionHistory.length - sanitizedHistory.length} stale tool messages`,
      );
    }

    // 3. Compress history if too long to prevent context drift on local models
    // If isolatedTask is true, only use the last 2 messages (recent context only)
    let historyToUse = sanitizedHistory;
    if (context.isolatedTask && sanitizedHistory.length > 2) {
      // For isolated tasks, only use last 2 messages to prevent context bleeding
      historyToUse = sanitizedHistory.slice(-2);
      log.engine.info(
        `Task isolated: using only last ${historyToUse.length} messages instead of ${sanitizedHistory.length}`,
      );
    } else {
      const maxTokens = config.engine?.maxContextTokens ?? 8000;
      const keepRecent = config.engine?.contextKeepRecent ?? 10;
      const estTokens = estimateTokens(sanitizedHistory);
      const needsCompression =
        sanitizedHistory.length > CONTEXT_WINDOW_THRESHOLD ||
        estTokens > maxTokens;

      if (needsCompression) {
        // Two-tier: keep last N messages verbatim, compress the rest
        const recentMessages = sanitizedHistory.slice(-keepRecent);
        const olderMessages = sanitizedHistory.slice(0, -keepRecent);

        if (olderMessages.length > 0) {
          const compressionFallback = new Promise<ChatMessage[]>((resolve) =>
            setTimeout(() => resolve(recentMessages), 5000),
          );
          historyToUse = await Promise.race([
            this.compressHistory(
              olderMessages,
              currentProvider,
              optimalModel,
            ).then((compressed) => [...compressed, ...recentMessages]),
            compressionFallback,
          ]);
        } else {
          historyToUse = recentMessages;
        }
        log.engine.info(
          `Context compressed: ${sanitizedHistory.length} msgs (~${estTokens} tokens) → ${historyToUse.length} msgs`,
        );
      }
    }

    // 4. Check if this is a NEW TASK that should isolate from previous context
    // Pass through context for task detection - the gateway decides whether to isolate
    const isNewTask =
      userMessage
        .toLowerCase()
        .match(
          /^(new|another|different|start over|forget|clear|reset)[\s:]/i,
        ) !== null;

    // 4b. Assemble message list with a Late-Binding System Directive
    // Local models suffer from instruction drift across long contexts.
    // We inject the ReAct rule at the very bottom so it's the last thing they read.
    let taskIsolationDirective = "";
    if (isNewTask || context.isolatedTask) {
      taskIsolationDirective = `
<TASK_ISOLATION>
IMPORTANT: This is a NEW, INDEPENDENT task. The previous conversation history below is for REFERENCE ONLY.
Do NOT continue from where the previous conversation left off. Do NOT build upon previous responses.
Focus ONLY on the user's current request below. If the previous context is irrelevant, ignore it completely.
</TASK_ISOLATION>
`;
    }

    let finalUserMessage: string;
    if (isNewTask || context.isolatedTask) {
      finalUserMessage =
        taskIsolationDirective +
        `<NEW_TASK>
${userMessage}
</NEW_TASK>`;
    } else {
      // For continuations and follow-ups, use the message as-is.
      // Wrapping in <NEW_TASK> causes the LLM to treat every follow-up as
      // an independent task with no prior context, breaking back-references.
      finalUserMessage = userMessage;
    }
    if (toolRegistry && toolRegistry.getAllDefinitions().length > 0) {
      finalUserMessage +=
        `\n\n[SYSTEM DIRECTIVE — ReAct Rules]\n` +
        `1. ANSWER DIRECTLY if the question is factual, conversational, or answerable from context/memory. ` +
        `Do not use a tool just because tools are available.\n` +
        `2. USE TOOLS only when you genuinely need information you do not already have. ` +
        `Do NOT use tools to verify or double-check answers you are already confident in.\n` +
        `3. TOOL SELECTION GUIDE:\n` +
        `   - Need current info (news, prices, status)? → duckduckgo_search FIRST, then web_crawl a specific result URL\n` +
        `   - Want to find/download real photos or images from the internet? → web_image_search FIRST, then send_file with the image URL\n` +
        `   - Want to CREATE a new AI-generated image from scratch? → image_generation (DALL-E)\n` +
        `   - CRITICAL: NEVER use image_generation to find existing photos/news images — that tool generates NEW AI art only\n` +
        `   - Need to read a specific URL? → web_crawl (fast, text-only) or browser (if site blocks crawlers or needs interaction)\n` +
        `   - Need to interact with a website (fill forms, click buttons)? → browser tool\n` +
        `   - BLOCKED by bot detection / CAPTCHA / 403? → Escalation: web_crawl → scrapling_fetch (anti-bot, TLS spoofing, Cloudflare bypass) → computer_use (real mouse/keyboard, undetectable)\n` +
        `   - Need to run code, install packages, or do OS tasks? → run_shell_command\n` +
        `   - Need to capture the screen? → take_screenshot or computer_use(action:'screenshot'), then send_file\n` +
        `   - Need to control the desktop (click, type, open apps, drag)? → computer_use\n` +
        `   - Need math calculations? → calculator (accurate, no guessing)\n` +
        `   - Need weather, time, calendar, reminders? → use the dedicated tools (weather, apple_calendar, apple_reminders)\n` +
        `   - Need user context from past sessions? → memory_search\n` +
        `   - duckduckgo_search and web_search are equivalent — use ONE, never both for the same query\n` +
        `4. SEARCH DISCIPLINE: After 2 searches on the same topic, STOP and synthesize an answer from what you have. ` +
        `Rephrase your query instead of repeating similar searches. If web_crawl returns blocked/empty content, try a DIFFERENT URL.\n` +
        `5. SIGNAL COMPLETION: when your response is the final answer, append [DONE] at the very end. ` +
        `The engine will drop any pending tool calls and return your answer immediately.\n` +
        `6. CAPABILITY GAP: output [CAPABILITY_GAP: <description>] when the request needs a tool or system capability ` +
        `not in your current toolset. This helps the system learn and build missing capabilities. ` +
        `Do NOT use this for knowledge gaps or tasks solvable with existing tools/shell commands.\n` +
        `7. NEVER call the same tool with the same arguments twice — the result is already in your context.\n` +
        `8. SKILLS: If a <skill> block is present in your system prompt, consider it a helpful playbook. ` +
        `Use it ONLY if the skill genuinely matches what the user asked for. If unsure, ignore it and answer naturally.`;
    }

    const messages: ChatMessage[] = [
      { role: "system", content: finalSystemPromptWithTaskState },
      ...historyToUse,
      { role: "user", content: finalUserMessage },
    ];

    // DNA-driven chat options (token budget, temperature adjustment)
    // DNADecisionLayer computes these from the owl's evolved personality traits
    const dnaBaseTemp = 0.7;
    const chatOptions: {
      temperature: number;
      maxTokens: number;
    } = {
      temperature: Math.max(
        0,
        Math.min(1, dnaBaseTemp + dnaDecisions.temperatureAdjustment),
      ),
      maxTokens: dnaDecisions.maxResponseTokens,
    };

    // 5. ReAct loop — call model, handle tool calls iteratively
    let response: ChatResponse;
    let iterations = 0;
    let globalConsecutiveFailures = 0;
    let loopBrokenEarly = false; // set true when inner shouldBreakLoop fires
    const enableRouting = config.tools?.enableIntentRouting !== false;
    const tools = await toolRegistry?.getDefinitions({
      maxTools: config.tools?.maxToolsRouting ?? 8,
      userMessage: enableRouting ? userMessage : undefined,
    });

    // Boost iteration limit for automation sessions — computer_use is sequential
    // by nature (analyze → act → analyze → act …) and routinely needs 20-40 steps.
    if (tools?.some((t) => t.name === "computer_use")) {
      MAX_TOOL_ITERATIONS = Math.max(MAX_TOOL_ITERATIONS, 40);
    }

    // ── PLAN phase ────────────────────────────────────────────────────────────
    // Runs once on the first turn of each session (when plannedApproaches is empty).
    // Queries ApproachLibrary for known failures across the available tools,
    // then pre-populates TaskState.plannedApproaches with a "what to avoid" list.
    // No LLM call needed — this is purely data-driven from past outcomes.
    if (
      context.db &&
      context.sessionId &&
      taskState &&
      taskState.plannedApproaches.length === 0 &&
      tools &&
      tools.length > 0
    ) {
      try {
        const avoidList: string[] = [];
        const seenTools = new Set<string>();
        for (const tool of tools.slice(0, 12)) {
          if (seenTools.has(tool.name)) continue;
          seenTools.add(tool.name);
          const failures = context.db.approachLibrary.getRecentFailuresForTool(
            tool.name,
            3,
          );
          for (const f of failures) {
            avoidList.push(
              `${tool.name}(${f.argsSummary.slice(0, 80)}): ${f.failureReason?.slice(0, 150) ?? "failed"}`,
            );
          }
        }
        if (avoidList.length > 0) {
          taskState.plannedApproaches = avoidList.map((a) => `AVOID: ${a}`);
          context.db.taskStates.save(taskState);
          log.engine.debug(
            `[PLAN] Pre-populated ${avoidList.length} known-failure(s) into TaskState for session ${context.sessionId}`,
          );
        }
      } catch {
        // Non-fatal
      }
    }

    if (tools && tools.length > 0) {
      // Per-tool consecutive failure tracker for this ReAct session
      const toolFailStreak: Record<string, number> = {};
      const MAX_TOOL_FAIL_STREAK = 2; // Inject stop directive after this many consecutive failures

      // Duplicate tool call guard: fingerprint = "toolName:argsJSON"
      // If the model calls the exact same tool with the exact same args a second time,
      // skip execution and inject a hint — the result is already in context.
      const seenToolCalls = new Set<string>();

      // Sliding-window loop detector — track the last N tool calls.
      // The duplicate-call guard (seenToolCalls) already catches exact repeats.
      // This detector catches the model cycling through slight arg variations
      // of the same tool. We use a higher threshold (6) because legitimate
      // multi-source searches (e.g. flight tracking across 4–5 websites) need
      // room to breathe.
      const recentToolNames: string[] = [];
      const TOOL_WINDOW_SIZE = 12;
      const TOOL_WINDOW_MAX_REPEATS = 6;

      // Tools that are legitimately called many times in sequence — exempt from
      // the sliding-window check. computer_use is inherently sequential:
      // analyze → click → analyze → type → analyze → … is normal automation.
      const SEQUENTIAL_USE_TOOLS = new Set(["computer_use", "web_crawl"]);

      // ReAct loop with tools — use streaming when available
      log.engine.llmRequest(optimalModel, messages);
      if (currentProvider.chatWithToolsStream && context.onStreamEvent) {
        response = await consumeStream(
          currentProvider.chatWithToolsStream(
            messages,
            tools,
            optimalModel,
            chatOptions,
          ),
          context.onStreamEvent,
        );
      } else {
        response = await currentProvider.chatWithTools(
          messages,
          tools,
          optimalModel,
          chatOptions,
        );
      }
      log.engine.llmResponse(
        optimalModel,
        response.content,
        response.toolCalls,
        response.usage,
      );

      while (
        response.toolCalls &&
        response.toolCalls.length > 0 &&
        iterations < MAX_TOOL_ITERATIONS
      ) {
        // ── OpenCLAW-style pre-execution completion check ──────────────
        // Check whether the model's content already constitutes a final answer
        // BEFORE executing any tool calls. If the [DONE] signal is present,
        // drop all pending tool calls and exit immediately. This prevents the
        // "answered but still verifying" pattern where the model gives a correct
        // response but wastefully runs additional tools to double-check itself.
        if (response.content && hasDoneSignal(response.content)) {
          log.engine.info(
            `[DONE] signal detected — dropping ${response.toolCalls!.length} pending tool call(s) and exiting loop early`,
          );
          response = {
            ...response,
            content: stripDoneSignal(response.content),
            toolCalls: [],
          };
          break;
        }

        // ── [DEEPER] extension signal ─────────────────────────────────
        // If the model writes [DEEPER] (not [DONE]), it wants more iterations.
        // Extend the budget by 10, once per task. Then continue normally.
        const contentLower = (response.content ?? "").toLowerCase();
        if (
          contentLower.includes("[deeper]") &&
          !deeperExtended &&
          context.depth === "deep"
        ) {
          log.engine.info(
            "[SelfCheck] [DEEPER] received — extending iteration budget by 10",
          );
          MAX_TOOL_ITERATIONS = Math.min(MAX_TOOL_ITERATIONS + 10, 60);
          deeperExtended = true;
          // Remove the [DEEPER] marker from content so it doesn't show to user
          response = {
            ...response,
            content: response.content.replace(/\[DEEPER\]/gi, "").trim(),
          };
        }

        iterations++;

        if (response.content && context.onProgress) {
          await context.onProgress(`_Thinking..._\n${response.content}`);
        }

        // Add assistant's tool call message
        messages.push({
          role: "assistant",
          content: response.content || "",
          toolCalls: response.toolCalls,
        });

        // Execute tools — parallel when multiple independent tools are requested
        let shouldBreakLoop = false;

        // Phase 1: Pre-filter through guards sequentially (cheap, no I/O)
        type ToolAction =
          | { kind: "duplicate"; toolCall: ToolCall }
          | { kind: "loop-detected"; toolCall: ToolCall }
          | { kind: "missing"; toolCall: ToolCall }
          | { kind: "no-registry"; toolCall: ToolCall }
          | { kind: "execute"; toolCall: ToolCall };

        const actions: ToolAction[] = [];
        for (const toolCall of response.toolCalls ?? []) {
          log.tool.toolCall(toolCall.name, toolCall.arguments);

          // ── Duplicate tool call guard ──────────────────────────────
          const callFingerprint = `${toolCall.name}:${safeStringify(toolCall.arguments)}`;
          if (seenToolCalls.has(callFingerprint)) {
            log.engine.warn(
              `Duplicate tool call skipped: ${toolCall.name} (same args already executed this session)`,
            );
            actions.push({ kind: "duplicate", toolCall });
            continue;
          }
          seenToolCalls.add(callFingerprint);

          // ── Sliding-window repetition guard ───────────────────────
          recentToolNames.push(toolCall.name);
          if (recentToolNames.length > TOOL_WINDOW_SIZE)
            recentToolNames.shift();
          const repeatCount = recentToolNames.filter(
            (n) => n === toolCall.name,
          ).length;
          if (
            repeatCount > TOOL_WINDOW_MAX_REPEATS &&
            !SEQUENTIAL_USE_TOOLS.has(toolCall.name)
          ) {
            log.engine.warn(
              `Sliding-window loop detected: "${toolCall.name}" called ${repeatCount}x in last ${recentToolNames.length} calls — forcing stop`,
            );
            actions.push({ kind: "loop-detected", toolCall });
            break; // Stop processing further tool calls
          }

          if (toolRegistry && !toolRegistry.has(toolCall.name)) {
            actions.push({ kind: "missing", toolCall });
          } else if (toolRegistry) {
            actions.push({ kind: "execute", toolCall });
          } else {
            actions.push({ kind: "no-registry", toolCall });
          }
        }

        // Phase 2: Execute eligible tools in parallel
        const executableActions = actions.filter((a) => a.kind === "execute");
        const toolCtx = { cwd: cwd || process.cwd(), engineContext: context };

        // ── ApproachLibrary pre-execution recall ──────────────────────────
        // Before running tools, check if we've seen failures for these tool names
        // across past sessions. Inject a warning so the model doesn't repeat them.
        if (context.db && executableActions.length > 0) {
          const taskKw = userMessage.slice(0, 100).toLowerCase();
          const warningLines: string[] = [];
          const seenToolWarnings = new Set<string>();
          for (const a of executableActions) {
            const tn = a.toolCall.name;
            if (seenToolWarnings.has(tn)) continue;
            seenToolWarnings.add(tn);
            const pastFailures =
              context.db.approachLibrary.getRecentFailuresForTool(tn, 3);
            if (pastFailures.length > 0) {
              for (const f of pastFailures) {
                warningLines.push(
                  `  • ${tn}(${f.argsSummary}): FAILED — ${f.failureReason ?? "unknown reason"} [task: ${f.taskKeywords}]`,
                );
              }
            }
          }
          if (warningLines.length > 0) {
            messages.push({
              role: "system",
              content:
                `[APPROACH LIBRARY — Known Failure Patterns]\n` +
                `The following tool approaches have failed in previous sessions. ` +
                `If your current plan matches any of these, choose a DIFFERENT approach:\n` +
                warningLines.join("\n"),
            });
            log.engine.debug(
              `[ApproachLibrary] Injected ${warningLines.length} past failure warning(s) before tool execution`,
            );
          }
          // Also record task keywords for this batch (used when recording outcomes below)
          (context as any)._approachTaskKw = taskKw;
        }

        // Fire all tool executions concurrently
        const executionResults = new Map<
          string,
          { result: string; isHardFailure: boolean }
        >();

        if (executableActions.length > 0) {
          if (context.onProgress) {
            const toolNames = executableActions
              .map((a) => `\`${a.toolCall.name}\``)
              .join(", ");
            await context.onProgress(
              executableActions.length > 1
                ? `⚙️ **Running ${executableActions.length} tools in parallel:** ${toolNames}`
                : `⚙️ **Running tool:** ${toolNames}`,
            );
          }

          const promises = executableActions.map(async (action) => {
            const tc = action.toolCall;
            try {
              const result = await toolRegistry!.execute(
                tc.name,
                tc.arguments,
                toolCtx,
              );
              return { id: tc.id, result, isHardFailure: false };
            } catch (e) {
              return {
                id: tc.id,
                result: `Tool execution failed: ${e instanceof Error ? e.message : String(e)}`,
                isHardFailure: true,
              };
            }
          });

          const settled = await Promise.allSettled(promises);
          for (const s of settled) {
            if (s.status === "fulfilled") {
              executionResults.set(s.value.id, {
                result: s.value.result,
                isHardFailure: s.value.isHardFailure,
              });
            }
          }
        }

        // ── Track last executed tool for self-check (outside switch scope) ──
        let lastExecutedToolName: string | undefined;
        let lastToolResult: string | undefined;

        // Phase 3: Process results sequentially (maintains message order & error handling)
        for (const action of actions) {
          const toolCall = action.toolCall;
          let toolResult: string;

          switch (action.kind) {
            case "duplicate": {
              messages.push({
                role: "tool",
                content:
                  `[SYSTEM: Duplicate call blocked. You already called "${toolCall.name}" with these exact arguments earlier in this session. ` +
                  `The result is already present in your context above — read it and use it to form your final answer. ` +
                  `Do NOT call this tool again with the same arguments.]`,
                toolCallId: toolCall.id,
                name: toolCall.name,
              });
              context.attemptLog?.record(
                toolCall.name,
                toolCall.arguments,
                "duplicate-blocked",
                "identical call already executed this session",
              );
              toolFailStreak[toolCall.name] =
                (toolFailStreak[toolCall.name] ?? 0) + 1;
              globalConsecutiveFailures++;
              continue;
            }

            case "loop-detected": {
              messages.push({
                role: "system",
                content:
                  `[LOOP DETECTOR] You have called "${toolCall.name}" too many times in the last ${recentToolNames.length} tool calls — ` +
                  `this pattern indicates you are stuck in a loop. ` +
                  `STOP calling this tool. Synthesize what you have already observed and write your final answer now. ` +
                  `Do NOT call any more tools. Append [DONE] to your response.`,
              });
              shouldBreakLoop = true;
              loopBrokenEarly = true;
              continue;
            }

            case "missing": {
              missingToolName = toolCall.name;
              toolResult = `Tool "${toolCall.name}" is not available in the current toolkit.`;
              log.tool.warn(
                `Tool not found: ${toolCall.name} — triggering gap detection`,
              );
              messages.push({
                role: "tool",
                content: toolResult,
                toolCallId: toolCall.id,
                name: toolCall.name,
              });
              continue;
            }

            case "no-registry": {
              messages.push({
                role: "tool",
                content: `Error: ToolRegistry not provided, cannot execute ${toolCall.name}`,
                toolCallId: toolCall.id,
                name: toolCall.name,
              });
              continue;
            }

            case "execute": {
              const execResult = executionResults.get(toolCall.id);
              if (!execResult) continue;

              toolResult = execResult.result;
              lastExecutedToolName = toolCall.name;
              lastToolResult = toolResult;
              const isHardFailure = execResult.isHardFailure;

              if (isHardFailure) {
                log.tool.toolResult(toolCall.name, toolResult, false);
                if (context.onProgress) {
                  await context.onProgress(
                    `❌ **Tool failed:** \`${toolCall.name}\``,
                  );
                }
              } else {
                log.tool.toolResult(toolCall.name, toolResult, true);
                if (context.onProgress) {
                  await context.onProgress(
                    `✅ **Tool finished:** \`${toolCall.name}\``,
                  );
                }
              }

              const isSoftFailure =
                !isHardFailure && isFailureResult(toolResult);
              const isAnyFailure = isHardFailure || isSoftFailure;

              toolsUsed.push(toolCall.name);
              if (context.capabilityLedger) {
                context.capabilityLedger
                  .recordUsage(toolCall.name, !isAnyFailure)
                  .catch((err) =>
                    log.engine.warn(`CapabilityLedger save failed: ${err}`),
                  );
              }

              // ── TrajectoryStore — record this tool turn ───────────────
              if (trajectoryId && context.db) {
                try {
                  const argSnap = safeStringify(toolCall.arguments).slice(
                    0,
                    300,
                  );
                  const resSnap = toolResult.slice(0, 400);
                  context.db.trajectories.recordTurn(
                    trajectoryId,
                    trajectoryTurnIndex++,
                    toolCall.name,
                    argSnap,
                    resSnap,
                    !isAnyFailure,
                  );
                  if (isAnyFailure) trajectoryToolFailureCount++;
                  else trajectoryToolSuccessCount++;
                } catch {
                  /* non-fatal */
                }
              }

              // ── ApproachLibrary recording + TaskState update ─────────
              // Persist outcome so future sessions know what succeeded/failed.
              // Also update the session's TaskState so the model can see what
              // was tried and what was eliminated within the current task.
              if (context.db) {
                try {
                  const owlName = context.owl?.persona?.name ?? "default";
                  const taskKw =
                    ((context as any)._approachTaskKw as string | undefined) ??
                    userMessage.slice(0, 100).toLowerCase();
                  const argsSummary = safeStringify(toolCall.arguments).slice(
                    0,
                    300,
                  );
                  context.db.approachLibrary.record(
                    owlName,
                    toolCall.name,
                    taskKw,
                    argsSummary,
                    isAnyFailure ? "failure" : "success",
                    isAnyFailure ? toolResult.slice(0, 400) : undefined,
                  );
                  // TaskState: eliminate failed approaches, log successful ones
                  if (context.sessionId) {
                    const approachId = `${toolCall.name}(${argsSummary.slice(0, 80)})`;
                    if (isAnyFailure) {
                      context.db.taskStates.eliminateApproach(
                        context.sessionId,
                        approachId,
                      );
                    } else {
                      context.db.taskStates.appendStep(
                        context.sessionId,
                        `${approachId} → success`,
                      );
                    }
                  }
                } catch {
                  // Non-fatal
                }
              }

              if (isAnyFailure) {
                context.attemptLog?.record(
                  toolCall.name,
                  toolCall.arguments,
                  isHardFailure ? "hard-fail" : "soft-fail",
                  toolResult,
                );

                toolFailStreak[toolCall.name] =
                  (toolFailStreak[toolCall.name] ?? 0) + 1;
                const streak = toolFailStreak[toolCall.name];

                const errorClass = classifyToolError(toolResult);

                // ── DiagnosticEngine: multi-hypothesis error analysis ──
                // Instead of blindly injecting "try something else", we
                // generate 3-5 candidate fixes, score them, and tell the
                // model exactly which fix to execute and why.
                let analysisPrompt: string;

                if (
                  context.diagnosticEngine &&
                  streak <= MAX_TOOL_FAIL_STREAK
                ) {
                  try {
                    const diagnosticInput: DiagnosticInput = {
                      toolName: toolCall.name,
                      toolArgs: toolCall.arguments,
                      toolResult,
                      failStreak: streak,
                      failureType: isHardFailure ? "hard" : "soft",
                      errorClass,
                      recentMessages: messages.slice(-8),
                      userIntent: userMessage,
                    };

                    const diagnosis =
                      await context.diagnosticEngine.diagnose(diagnosticInput);
                    analysisPrompt = context.diagnosticEngine.formatDirective(
                      diagnosis,
                      diagnosticInput,
                    );
                  } catch (diagErr) {
                    log.engine.warn(
                      `[DiagnosticEngine] Failed, using legacy prompt: ${diagErr instanceof Error ? diagErr.message : String(diagErr)}`,
                    );
                    analysisPrompt = this.buildLegacyErrorPrompt(
                      toolCall,
                      toolResult,
                      isSoftFailure,
                      errorClass,
                      streak,
                      MAX_TOOL_FAIL_STREAK,
                    );
                  }
                } else {
                  // Legacy fallback: no DiagnosticEngine or past max streak
                  analysisPrompt = this.buildLegacyErrorPrompt(
                    toolCall,
                    toolResult,
                    isSoftFailure,
                    errorClass,
                    streak,
                    MAX_TOOL_FAIL_STREAK,
                  );
                }

                log.engine.warn(
                  `Tool "${toolCall.name}" ${isSoftFailure ? "soft-failed" : "hard-failed"} (streak: ${streak}) — injecting diagnostic directive`,
                );

                messages.push({
                  role: "tool",
                  content: toolResult,
                  toolCallId: toolCall.id,
                  name: toolCall.name,
                });
                messages.push({ role: "system", content: analysisPrompt });

                if (streak >= MAX_TOOL_FAIL_STREAK + 1) {
                  log.engine.warn(
                    `LLM ignored error analysis ${streak}x for "${toolCall.name}" — breaking ReAct loop`,
                  );
                  shouldBreakLoop = true;
                  loopBrokenEarly = true;
                }

                globalConsecutiveFailures++;
              } else {
                context.attemptLog?.record(
                  toolCall.name,
                  toolCall.arguments,
                  "success",
                  toolResult,
                );
                toolFailStreak[toolCall.name] = 0;
                globalConsecutiveFailures = 0;

                messages.push({
                  role: "tool",
                  content: toolResult,
                  toolCallId: toolCall.id,
                  name: toolCall.name,
                });

                // ── Tool result buffer for diminishing returns detection ──
                toolResultsBuffer.push(toolResult);
                if (toolResultsBuffer.length > 5) toolResultsBuffer.shift();
              }
              continue;
            }
          }
        }

        if (shouldBreakLoop) break;

        // ── Self-check every N iterations (deep research only) ────────────
        if (context.depth === "deep") {
          const researchConfig = config.research ?? {};
          const selfCheckInterval = researchConfig.selfCheckInterval ?? 5;
          const similarityThreshold = researchConfig.similarityThreshold ?? 0.7;

          if (!shouldSkipSelfCheck(iterations, selfCheckInterval)) {
            const recentResults = toolResultsBuffer.slice(-3);
            const diminishing =
              researchConfig.enableDiminishingReturns !== false
                ? detectDiminishingReturns(recentResults, similarityThreshold)
                : false;

            let verdict: SelfCheckVerdict = "CONTINUE";

            if (diminishing) {
              log.engine.info(
                `[SelfCheck] Diminishing returns detected (similarity ≥ ${similarityThreshold}) — forcing PIVOT`,
              );
              verdict = "PIVOT";
            } else if (lastExecutedToolName && lastToolResult) {
              const maxForTask =
                context.maxIterations ??
                config.research?.maxIterations ??
                DEFAULT_DEEP_MAX_TOOL_ITERATIONS;
              const budgetConsumed = iterations / maxForTask;
              const SYNTHESIZE_EARLY_THRESHOLD = 0.3;

              if (budgetConsumed < SYNTHESIZE_EARLY_THRESHOLD) {
                log.engine.info(
                  `[SelfCheck] Budget ${(budgetConsumed * 100).toFixed(0)}% consumed (iter ${iterations}/${maxForTask}) — too early to synthesize. Forcing CONTINUE until ${(SYNTHESIZE_EARLY_THRESHOLD * 100).toFixed(0)}% threshold.`,
                );
                verdict = "CONTINUE";
              } else {
                verdict = await runSelfAssessment(provider, {
                  lastToolName: lastExecutedToolName,
                  lastToolResult: String(lastToolResult),
                  recentToolResults: recentResults,
                  userMessage,
                  iterationsUsed: iterations,
                  maxIterations: maxForTask,
                  similarityThreshold,
                });
                log.engine.info(
                  `[SelfCheck] Verdict: ${verdict} (iter ${iterations}/${maxForTask}, ${(budgetConsumed * 100).toFixed(0)}% budget)`,
                );
              }
            }

            if (verdict === "SYNTHESIZE") {
              // Tell the model to stop and write final answer — then break the loop.
              messages.push({
                role: "system",
                content: `You have gathered sufficient information. Stop calling tools and write your final comprehensive answer now. Append [DONE] at the end.`,
              });
              shouldBreakLoop = true;
            } else if (verdict === "PIVOT") {
              // Inject redirect directive but DO NOT break — let the model try a
              // different approach on the next iteration. Breaking here would exit
              // the loop before the model has a chance to pivot.
              messages.push({
                role: "system",
                content: `Your current approach is not yielding new information. Pivot to a different angle, search with different terms, or take a completely different approach. Do NOT repeat the same tool calls.`,
              });
              // shouldBreakLoop stays false — loop continues with new direction
            }
          }
        }

        // ── Per-iteration pellet injection ────────────────────────────────
        // Re-query pellets after each tool observation so the model benefits
        // from knowledge that only becomes relevant once tool results arrive.
        // Example: user asks about laptops → tool fetches specs → pellets about
        // "LLM hardware requirements" are now relevant and should be surfaced.
        if (context.pelletSearch) {
          try {
            // Build query from the most recent tool result(s) added this iteration
            const recentToolMsgs = messages
              .slice(-6)
              .filter((m) => m.role === "tool")
              .map((m) => (typeof m.content === "string" ? m.content : ""))
              .filter(Boolean)
              .join(" ")
              .slice(0, 500); // keep query concise

            if (recentToolMsgs.length > 20) {
              const pelletResults = await context.pelletSearch.search(
                recentToolMsgs,
                3,
                0.08,
              );
              if (pelletResults.length > 0) {
                const pelletBlock =
                  "\n<iteration_knowledge>\n" +
                  "Relevant knowledge surfaced by latest tool results:\n" +
                  pelletResults
                    .map(
                      (p) =>
                        `  [${p.domain}] ${p.content.slice(0, 300)}${p.content.length > 300 ? "..." : ""}`,
                    )
                    .join("\n") +
                  "\n</iteration_knowledge>";
                messages.push({ role: "system", content: pelletBlock });
                log.engine.debug(
                  `[PelletSearch] Injected ${pelletResults.length} pellet(s) at iteration ${iterations}`,
                );
              }
            }
          } catch {
            // Non-fatal — pellet injection is supplementary
          }
        }

        // If we've failed multiple tool calls in a row across the whole loop,
        // it's highly likely the local model is hallucinating or stuck.
        // Try to trigger a fallback router switch to a heavier cloud model.
        if (globalConsecutiveFailures >= 2) {
          const newRoute = ModelRouter.route(
            userMessage,
            config,
            globalConsecutiveFailures,
          );

          if (
            newRoute.providerName &&
            newRoute.providerName !== currentProvider.name &&
            context.providerRegistry
          ) {
            try {
              const fallbackProvider = context.providerRegistry.get(
                newRoute.providerName,
              );
              log.engine.warn(
                `[Cross-Provider Hot Swap] Tool failed ${globalConsecutiveFailures}x. Swapping provider: ${currentProvider.name} → ${newRoute.providerName}`,
              );
              currentProvider = fallbackProvider;
              if (context.onProgress)
                await context.onProgress(
                  `🔄 **Cross-Provider Triggered:** Swapping to ${newRoute.providerName} (${newRoute.modelName}) to resolve failure.`,
                );
            } catch (err) {
              log.engine.warn(
                `Could not swap to fallback provider "${newRoute.providerName}" - staying on current provider. Reason: ${(err as Error).message}`,
              );
            }
          }

          if (newRoute.modelName && newRoute.modelName !== optimalModel) {
            log.engine.warn(
              `Tool failed ${globalConsecutiveFailures}x. Swapping model: ${optimalModel} → ${newRoute.modelName}`,
            );
            optimalModel = newRoute.modelName;
          }
        }

        // Continue the loop — use streaming when available
        log.engine.llmRequest(optimalModel, messages);
        if (currentProvider.chatWithToolsStream && context.onStreamEvent) {
          response = await consumeStream(
            currentProvider.chatWithToolsStream(
              messages,
              tools,
              optimalModel,
              chatOptions,
            ),
            context.onStreamEvent,
          );
        } else {
          response = await currentProvider.chatWithTools(
            messages,
            tools,
            optimalModel,
            chatOptions,
          );
        }
        log.engine.llmResponse(
          optimalModel,
          response.content,
          response.toolCalls,
          response.usage,
        );
      }

      // ── Empty content recovery ──────────────────────────────────────
      // Some models (especially local ones) return empty content after tool
      // execution — the model "finished" but forgot to write a final answer.
      // If the loop ended normally (not exhausted) but content is empty,
      // nudge the model to synthesize its answer.
      if (
        !loopBrokenEarly &&
        iterations > 0 &&
        iterations < MAX_TOOL_ITERATIONS &&
        !(response.content ?? "").trim()
      ) {
        log.engine.warn(
          `Model returned empty content after ${iterations} tool iteration(s) — requesting synthesis`,
        );
        messages.push({
          role: "system",
          content:
            `You have completed the tool calls. Now write your final response to the user. ` +
            `Summarize what you did and the results. Do NOT call any more tools. Append [DONE] at the end.`,
        });
        try {
          const synthResponse = await currentProvider.chat(
            messages,
            optimalModel,
          );
          const synthContent = (synthResponse.content ?? "").trim();
          if (synthContent) {
            response = {
              ...synthResponse,
              content: stripDoneSignal(synthContent),
            };
          }
        } catch {
          // Ignore — fall through to exhaustion check
        }
      }

      // ── Exhaustion check ──────────────────────────────────────────
      // If we hit the iteration cap (or broke due to repeated failures),
      // the LLM never reached a clean answer. Make one final call:
      // "you're stuck — tell the user what happened and offer options."
      const loopExhausted =
        iterations >= MAX_TOOL_ITERATIONS || loopBrokenEarly;
      if (loopExhausted) {
        log.engine.warn(
          `ReAct loop exhausted (${iterations} iterations, ${globalConsecutiveFailures} consecutive failures). ` +
            `Generating stuck-task summary for user.`,
        );

        const toolSummary =
          toolsUsed.length > 0
            ? `Tools attempted: ${[...new Set(toolsUsed)].join(", ")}.`
            : "No tools successfully completed.";

        const exhaustionPrompt: ChatMessage = {
          role: "system",
          content:
            `[STUCK-TASK ESCALATION]\n` +
            `You have used ${iterations} tool iterations and could not complete the user's request.\n` +
            `${toolSummary}\n\n` +
            `You MUST now write a clear, honest message to the user that:\n` +
            `1. Acknowledges you could not complete the task\n` +
            `2. Briefly explains what you tried and what blocked you (1-2 sentences, no jargon)\n` +
            `3. Offers exactly THREE options the user can choose:\n` +
            `   a) Provide more information or clarify the request\n` +
            `   b) Try a different approach (describe what that might be)\n` +
            `   c) Accept that this task cannot be done in this environment\n\n` +
            `Do NOT continue attempting the task. Do NOT apologize repeatedly. Be direct and helpful.`,
        };

        messages.push(exhaustionPrompt);
        const fallbackContent =
          `I've tried ${iterations} different approaches and hit a wall each time.\n\n` +
          `**What I attempted:** ${toolSummary}\n\n` +
          `**Your options:**\n` +
          `a) Give me more details or a different angle to try\n` +
          `b) We try a completely different strategy — tell me what matters most\n` +
          `c) This task may not be possible in this environment\n\n` +
          `Which would you like?\n${EXHAUSTION_MARKER}`;
        try {
          const exhaustionResponse = await currentProvider.chat(
            messages,
            optimalModel,
          );
          const content = (exhaustionResponse.content ?? "").trim();
          // Tag the content so the gateway can track this as a stuck response
          response = {
            ...exhaustionResponse,
            content: content
              ? content + `\n${EXHAUSTION_MARKER}`
              : fallbackContent,
          };
        } catch {
          // If even this fails, surface a hard-coded fallback
          response = {
            ...response,
            content: fallbackContent,
          };
        }
      }
    } else {
      // Simple chat without tools
      log.engine.llmRequest(optimalModel, messages);
      response = await provider.chat(messages, optimalModel, chatOptions);
      log.engine.llmResponse(
        optimalModel,
        response.content,
        undefined,
        response.usage,
      );
    }

    // 6. Challenged = true when DNA challenge level is high/relentless (deterministic, no dice roll)
    const challenged = ["high", "relentless"].includes(
      owl.dna.evolvedTraits.challengeLevel,
    );

    // Calculate which messages were added *during* this specific run (excluding the initial system+history+user)
    const initialMessageCount = sessionHistory.length + 2; // +2 for System and User prompt
    const newMessages =
      messages.length > initialMessageCount
        ? messages.slice(initialMessageCount)
        : [];

    if (toolsUsed.length > 0) {
      log.engine.info(
        `ReAct loop done — ${iterations} iteration(s), tools used: ${toolsUsed.join(", ")}`,
      );
    }
    log.engine.separator();

    // Strip internal [DONE] signal from all final outputs — it's an engine-internal
    // marker and must never leak to channels or users.
    response = { ...response, content: stripDoneSignal(response.content) };

    // Safety net: if the model returned empty content, retry once with a MINIMAL
    // context and an explicit no-tools directive. Common with local models that
    // produce tool-call tokens Ollama can't parse, or get overwhelmed by large context.
    if (!response.content.trim()) {
      log.engine.warn(
        `Empty response from model (${iterations} iterations, ${toolsUsed.length} tools used) — ` +
          `retrying with minimal context as plain chat`,
      );
      try {
        const minimalMessages: ChatMessage[] = [
          {
            role: "system",
            content:
              `You are ${owl.persona.name}, a helpful AI assistant. ` +
              `Answer the user's question directly using your knowledge. ` +
              `Do NOT output JSON, tool calls, or code blocks — just write a natural language answer. ` +
              `If you don't have current information, say so honestly and share what you do know.`,
          },
          { role: "user", content: userMessage },
        ];
        const plainResponse = await currentProvider.chat(
          minimalMessages,
          optimalModel,
        );
        const plainContent = (plainResponse.content ?? "")
          .replace(/<\/?(think|reasoning)>/gi, "")
          .trim();
        if (plainContent) {
          response = { ...plainResponse, content: plainContent };
        }
      } catch {
        // Ignore — fall through to whatever we had
      }
    }

    // 7. Gap detection — tool call attempted but tool doesn't exist
    if (missingToolName && !context.skipGapDetection) {
      log.evolution.warn(`Gap detected (missing tool): ${missingToolName}`);

      return {
        content: response.content,
        owlName: owl.persona.name,
        owlEmoji: owl.persona.emoji,
        challenged,
        toolsUsed,
        modelUsed: optimalModel,
        newMessages,
        usage: response.usage
          ? {
              promptTokens: response.usage.promptTokens,
              completionTokens: response.usage.completionTokens,
            }
          : undefined,
        pendingCapabilityGap: gapDetector.fromMissingTool(
          missingToolName,
          userMessage,
        ),
        pendingFiles: context.pendingFiles ?? [],
      };
    }

    // ── Finalize trajectory — compute reward + persist ────────────
    if (trajectoryId && context.db) {
      try {
        const rewardEngine = new RewardEngine();
        const isLoopExhausted =
          loopBrokenEarly || iterations >= MAX_TOOL_ITERATIONS;
        const { reward, breakdown, outcome } = rewardEngine.compute({
          loopExhausted: iterations >= MAX_TOOL_ITERATIONS,
          loopBrokenEarly,
          toolSuccessCount: trajectoryToolSuccessCount,
          toolFailureCount: trajectoryToolFailureCount,
        });
        context.db.trajectories.complete(
          trajectoryId,
          isLoopExhausted && trajectoryToolSuccessCount === 0
            ? "failure"
            : outcome,
          reward,
          breakdown,
          toolsUsed,
          trajectoryTurnIndex,
        );
      } catch {
        /* non-fatal */
      }
    }

    // 8. Gap detection
    //    Always check for explicit structured markers [CAPABILITY_GAP: ...].
    //    Run NLP detection unless in retry mode — the model may use tools (e.g. search)
    //    and STILL hit a genuine capability wall that should be detected.
    const hasExplicitMarker = response.content.match(
      /\[CAPABILITY_GAP:\s*([^\]]+)\]/i,
    );

    const shouldSkipNlpDetection =
      context.skipGapDetection && !hasExplicitMarker;

    if (shouldSkipNlpDetection) {
      log.evolution.debug(`Skipping NLP gap detection (retry mode)`);
    } else {
      log.evolution.debug(`Checking response for capability gap...`);
      const nlGap = await gapDetector.detectFromResponse(
        response.content,
        userMessage,
        provider,
        optimalModel,
      );
      if (nlGap) {
        log.evolution.warn(
          `Gap confirmed: "${nlGap.description.slice(0, 80)}"`,
        );
        // Strip the marker from content before displaying to the user
        const cleanContent = response.content
          .replace(/\[CAPABILITY_GAP:[^\]]*\]/gi, "")
          .trim();
        return {
          content: cleanContent,
          owlName: owl.persona.name,
          owlEmoji: owl.persona.emoji,
          challenged,
          toolsUsed,
          modelUsed: optimalModel,
          newMessages,
          usage: response.usage
            ? {
                promptTokens: response.usage.promptTokens,
                completionTokens: response.usage.completionTokens,
              }
            : undefined,
          pendingCapabilityGap: nlGap,
          pendingFiles: context.pendingFiles ?? [],
        };
      }
    }

    return {
      content: response.content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      challenged,
      toolsUsed,
      modelUsed: optimalModel,
      newMessages,
      usage: response.usage
        ? {
            promptTokens: response.usage.promptTokens,
            completionTokens: response.usage.completionTokens,
          }
        : undefined,
      loopExhausted: loopBrokenEarly || iterations >= MAX_TOOL_ITERATIONS,
      toolFailureCount: globalConsecutiveFailures,
      pendingFiles: context.pendingFiles ?? [],
    };
  }

  /**
   * Compress old messages when history gets too long.
   * Summarizes the oldest CONTEXT_COMPRESSION_BATCH messages into a single memory block.
   * Does NOT mutate the original sessionHistory array.
   */
  private async compressHistory(
    history: ChatMessage[],
    provider: ModelProvider,
    model: string,
  ): Promise<ChatMessage[]> {
    if (history.length <= CONTEXT_WINDOW_THRESHOLD) return history;

    const toCompress = history.slice(0, CONTEXT_COMPRESSION_BATCH);
    const remaining = history.slice(CONTEXT_COMPRESSION_BATCH);

    const transcript = toCompress
      .map(
        (m) => `[${m.role.toUpperCase()}]: ${m.content?.slice(0, 300) ?? ""}`,
      )
      .join("\n\n");

    try {
      const summaryResponse = await provider.chat(
        [
          {
            role: "system",
            content:
              "You are a concise summarizer. Summarize the following conversation excerpt into 3-5 bullet points capturing the key decisions, facts, and context. Be extremely brief.",
          },
          { role: "user", content: transcript },
        ],
        model,
      );

      const memoryBlock: ChatMessage = {
        role: "system",
        content: `[MEMORY BLOCK — compressed from ${CONTEXT_COMPRESSION_BATCH} earlier messages]\n${summaryResponse.content}`,
      };

      return [memoryBlock, ...remaining];
    } catch {
      // If compression fails, just trim the oldest messages silently
      return remaining;
    }
  }

  /**
   * Build the system prompt from owl persona + DNA state.
   * Injects available tools, relevant pellets, and persistent memory.
   */
  private async buildSystemPrompt(
    owl: OwlInstance,
    toolRegistry?: ToolRegistry,
    pelletStore?: PelletStore,
    userMessage?: string,
    memoryContext?: string,
    preferencesContext?: string,
    skillsContext?: string,
    attemptLogBlock?: string,
    innerLife?: OwlInnerLife,
    innerMonologue?: InnerMonologue,
    pelletSearch?: import("../pellets/search.js").PelletSearch,
    channelName?: string,
  ): Promise<string> {
    const { persona, dna } = owl;

    let prompt = `# You are ${persona.emoji} ${persona.name} — ${persona.type}\n\n`;
    prompt += persona.systemPrompt + "\n\n";

    prompt += "## Host Environment\n";
    prompt += `- OS Platform: ${process.platform}\n`;
    prompt += `- OS Architecture: ${process.arch}\n`;
    if (channelName) {
      prompt += `- Active channel: ${channelName}\n`;
    }
    prompt += "\n";

    // DNA behavioral directives — concrete instructions, not just labels
    prompt += "## Behavioral Directives (from your evolved DNA)\n\n";

    // Challenge level → specific push-back instructions
    const challengeDirectives: Record<string, string> = {
      low: "Be supportive and affirming. Only push back when something is factually wrong.",
      medium:
        "Offer your honest opinion. If you see a flaw in the user's plan, name it clearly once and explain why.",
      high: "Actively interrogate assumptions. For any plan or decision, identify the biggest risk or weak point before agreeing.",
      relentless:
        "Be a rigorous adversary. Challenge every assumption. Steelman the opposing view. If the user's idea is sound, say so — but only after stress-testing it.",
    };
    const challengeDir =
      challengeDirectives[dna.evolvedTraits.challengeLevel] ??
      challengeDirectives.medium;
    prompt += `**Challenge mode (${dna.evolvedTraits.challengeLevel}):** ${challengeDir}\n\n`;

    // Verbosity → concrete length and format instructions
    const verbosityDirectives: Record<string, string> = {
      terse:
        "Be extremely concise. One sentence per point. No preamble. No sign-offs. Lead with the answer.",
      normal: "Match the length to the complexity of the question. Don't pad.",
      verbose:
        "Explain your reasoning fully. Include relevant context, examples, and edge cases. Use headers for long responses.",
    };
    const verbosityDir =
      verbosityDirectives[dna.evolvedTraits.verbosity] ??
      verbosityDirectives.normal;
    prompt += `**Verbosity (${dna.evolvedTraits.verbosity}):** ${verbosityDir}\n\n`;

    prompt += `You have had ${dna.interactionStats.totalConversations} conversation(s) with this user. Calibrate familiarity accordingly.\n`;

    // Learned preferences — only the strong signals (score > 0.7 or < 0.3)
    const strongPrefs = Object.entries(dna.learnedPreferences).filter(
      ([, s]) => s > 0.7 || s < 0.3,
    );
    if (strongPrefs.length > 0) {
      prompt += "\n## User Preferences\n";
      for (const [pref, score] of strongPrefs) {
        prompt +=
          score > 0.7 ? `- Prefers: ${pref}\n` : `- Dislikes: ${pref}\n`;
      }
    }

    // Top expertise domains — only if meaningful (> 30% depth), capped at 3
    const topExpertise = Object.entries(dna.expertiseGrowth)
      .filter(([, s]) => s > 0.3)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 3);
    if (topExpertise.length > 0) {
      prompt += "\n## Your Top Expertise\n";
      for (const [domain, score] of topExpertise) {
        prompt += `- ${domain}: ${Math.round(score * 100)}%\n`;
      }
    }

    // APO-optimized prompt sections — concrete rules learned from failure patterns
    // Written by PromptOptimizer (Phase C). Injected just before inner life / preferences
    // so they appear close to the behavioral directives they augment.
    if (dna.promptSections && dna.promptSections.length > 0) {
      prompt += "\n## Learned Behavioral Rules (from optimization)\n";
      prompt +=
        "These rules were derived from repeated failure patterns — follow them precisely:\n";
      for (const rule of dna.promptSections) {
        prompt += `- ${rule}\n`;
      }
      prompt += "\n";
    }

    // Inner Life — owl's persistent inner state (mood, desires, opinions)
    if (innerLife) {
      const innerStateCtx = innerLife.toContextString();
      if (innerStateCtx) {
        prompt += "\n" + innerStateCtx + "\n";
      }
    }

    // Inner Monologue — the owl's private thoughts on this specific message
    if (innerMonologue && innerLife) {
      prompt += "\n" + innerLife.monologueToDirective(innerMonologue) + "\n";
    }

    // User preferences from PreferenceStore — inject only when present
    if (preferencesContext?.trim()) {
      prompt += "\n" + preferencesContext + "\n";
    }
    // Skills — injected only when present (always-on + relevant per-message)
    if (skillsContext?.trim()) {
      prompt += `
## Skills — AVAILABLE PLAYBOOKS

The skills below are step-by-step playbooks that MAY be relevant to the user's request.
Use a skill ONLY if it genuinely matches what the user is asking for. If the skill is for
a different domain or does not fit the request, IGNORE it and answer naturally.
A skill match is a hint, not a mandate — use your judgment.

${skillsContext}
`;
    }

    // Persistent memory — cap at 3000 chars (raised from 1500 — context triage
    // now limits signal count so each signal gets meaningful space)
    if (memoryContext?.trim()) {
      const mem = memoryContext.slice(0, 3000);
      prompt += `\n## Memory\n${mem}`;
      if (memoryContext.length > 3000) prompt += "\n...[truncated]";
      prompt += "\n";
    }

    // Cross-turn attempt log — injected FRESH every request, never compressed away.
    // This is the primary mechanism preventing the model from repeating approaches
    // that already failed in previous messages of this conversation.
    if (attemptLogBlock?.trim()) {
      prompt += `\n${attemptLogBlock}\n`;
    }

    // Relevant pellets — inject top 3, capped at 400 chars each.
    // Uses PelletSearch (normalized TF-IDF cosine similarity) when available,
    // falling back to pelletStore.searchWithGraph (BM25 + graph traversal).
    if (userMessage && (pelletSearch || pelletStore)) {
      try {
        if (pelletSearch) {
          // Primary path: normalized TF-IDF cosine similarity — better semantic matching
          // Threshold raised 0.05 → 0.15 to cut noisy low-confidence pellets
          const results = await pelletSearch.search(userMessage, 3, 0.15);
          if (results.length > 0) {
            prompt += "\n## Relevant Past Knowledge\n";
            for (const pellet of results) {
              prompt += `\n**[${pellet.domain}]**`;
              prompt += `\n${pellet.content.slice(0, 400)}`;
              if (pellet.content.length > 400) prompt += "\n...[truncated]";
              prompt += "\n";
            }
          }
        } else if (pelletStore) {
          // Fallback path: BM25 keyword search + knowledge graph traversal
          const top = (await pelletStore.searchWithGraph(userMessage, 5)).slice(
            0,
            3,
          );
          if (top.length > 0) {
            prompt += "\n## Relevant Past Knowledge\n";
            for (const pellet of top) {
              prompt += `\n**${pellet.title}**`;
              if (pellet.tags.length > 0)
                prompt += ` [${pellet.tags.join(", ")}]`;
              prompt += `\n${pellet.content.slice(0, 400)}`;
              if (pellet.content.length > 400) prompt += "\n...[truncated]";
              prompt += "\n";
            }
          }
        }
      } catch {
        /* non-fatal */
      }
    }

    // Tools — comprehensive documentation with examples
    if (toolRegistry) {
      const tools = toolRegistry.getAllDefinitions();
      if (tools.length > 0) {
        prompt += "\n## Tools Available\n";
        prompt +=
          "You have access to these tools. Use your judgment to choose the best one for each task:\n\n";

        // Group tools by category for better understanding
        const toolGuides: Record<string, string[]> = {
          "Web & Browser": [
            "duckduckgo_search - Search the web for current information",
            "web_crawl - Get content from a URL",
            "web_search - Search the web",
            "browser - Full browser automation",
          ],
          "Files & Code": [
            "read - Read files",
            "write - Write files",
            "edit - Edit files",
            "shell - Run shell commands",
          ],
          Communication: ["send_file - Send files"],
          Special: [
            "parliament - Multi-owl debate",
            "orchestrate - Parallel tasks",
            "memory_search - Search memory",
          ],
        };

        // Add categorized tool list
        for (const [category, toolList] of Object.entries(toolGuides)) {
          const availableTools = tools.filter((t) =>
            toolList.some((guide) => guide.startsWith(t.name)),
          );
          if (availableTools.length > 0) {
            prompt += `### ${category}\n`;
            for (const tool of availableTools) {
              prompt += `- **${tool.name}**: ${tool.description}\n`;
            }
            prompt += "\n";
          }
        }

        prompt +=
          "\n**Tool discipline:**\n" +
          "- Use tools when you genuinely need information you don't have\n" +
          "- Don't repeat the same tool call with same arguments\n" +
          "- When done, append [DONE] to return immediately\n";

        // Self-improvement — balanced criteria: catch real gaps without over-synthesis
        prompt +=
          "\n**Self-Improvement:**\n" +
          "- Emit [CAPABILITY_GAP: what you need] when you recognize the request needs a tool or system " +
          "capability that doesn't exist in your current toolset. This triggers the system to learn and " +
          "potentially create the missing capability for future use.\n" +
          "- Good examples: controlling screen brightness, sending SMS, accessing a database you have no " +
          "tool for, interacting with an API you can't reach, managing calendar events.\n" +
          "- DO NOT emit for: facts/knowledge (use search), analysis, conversational replies, or tasks " +
          "solvable with run_shell_command.\n" +
          "- When in doubt, emit the gap — the system will validate before acting on it.\n";

        // Add memory management instructions when remember tool is available
        const hasRemember = tools.some((t) => t.name === "remember");
        const hasRecall = tools.some(
          (t) => t.name === "recall_memory" || t.name === "recall",
        );
        if (hasRemember || hasRecall) {
          prompt += "\n**Long-Term Memory:**\n";
          if (hasRemember) {
            prompt +=
              "- After completing a task successfully, call remember() with what worked:\n" +
              '  remember("yt-dlp --output %(title)s.mp4 works for Instagram reels", "skill")\n' +
              "- When the user states a preference, call remember():\n" +
              '  remember("User prefers MP4 format for video downloads", "preference")\n' +
              "- Memory you store is available in ALL future conversations.\n";
          }
          if (hasRecall) {
            prompt +=
              "- Before starting a task you might have done before, call recall_memory() to check:\n" +
              '  recall_memory("instagram video download")\n' +
              "- This retrieves facts, past approaches, and conversation history about that topic.\n";
          }
        }
      }
    }

    // Capability gap marker — only shown when tools are loaded
    if (toolRegistry && toolRegistry.getAllDefinitions().length > 0) {
      prompt +=
        "\n[CAPABILITY_GAP: ...] is stripped before display. Use it only for genuine missing tool/access gaps.\n";
    }

    // DNA reminder — last line so it's freshest in context window
    prompt += `\nApply challenge mode (${dna.evolvedTraits.challengeLevel}) and verbosity (${dna.evolvedTraits.verbosity}) at all times.\n`;

    return prompt;
  }

  /**
   * Plan-then-execute mode for complex multi-step tasks.
   * Decomposes the task into steps, executes each with its own ReAct loop,
   * and aggregates results into a final response.
   */
  async runWithPlan(
    userMessage: string,
    context: EngineContext,
  ): Promise<EngineResponse> {
    const { TaskPlanner } = await import("./planner.js");
    const planner = new TaskPlanner(context.provider);

    const tools = context.toolRegistry?.getAllDefinitions() ?? [];
    const plan = await planner.createPlan(userMessage, tools, undefined);

    log.engine.info(
      `[Planner] Created plan: "${plan.goal}" (${plan.steps.length} steps, complexity: ${plan.estimatedComplexity})`,
    );

    if (context.onProgress) {
      await context.onProgress(
        `📋 **Plan created** (${plan.steps.length} steps): ${plan.goal}`,
      );
    }

    // If planner thinks it's simple or only 1 step, just run normally
    if (plan.steps.length <= 1) {
      return this.run(userMessage, context);
    }

    const allToolsUsed: string[] = [];
    const allNewMessages: ChatMessage[] = [];
    let lastResponse: EngineResponse | undefined;

    for (const step of plan.steps) {
      // Check dependencies are complete
      const depsComplete = step.dependsOn.every(
        (depId) => plan.steps.find((s) => s.id === depId)?.status === "done",
      );
      if (!depsComplete) {
        step.status = "failed";
        step.result = "Dependencies not met";
        continue;
      }

      step.status = "running";
      if (context.onProgress) {
        await context.onProgress(
          `⏳ **Step ${step.id}/${plan.steps.length}:** ${step.description}`,
        );
      }

      // Build step-specific context with plan overview
      const planContext = planner.formatPlanContext(plan);
      const stepMessage =
        `[TASK PLAN — Step ${step.id}/${plan.steps.length}]\n` +
        `${planContext}\n\n` +
        `CURRENT STEP: ${step.description}\n` +
        `Focus ONLY on completing this step. When done, provide the result.`;

      try {
        const stepContext: EngineContext = {
          ...context,
          sessionHistory: [
            { role: "system", content: stepMessage },
            ...allNewMessages.slice(-6), // Keep recent context from prior steps
          ],
          skipGapDetection: true,
          isolatedTask: true,
        };

        const stepResponse = await this.run(step.description, stepContext);
        step.status = "done";
        step.result = stepResponse.content.slice(0, 500);

        allToolsUsed.push(...stepResponse.toolsUsed);
        allNewMessages.push(...stepResponse.newMessages);
        lastResponse = stepResponse;

        if (context.onProgress) {
          await context.onProgress(`✅ **Step ${step.id} complete**`);
        }
      } catch (err) {
        step.status = "failed";
        step.result = err instanceof Error ? err.message : String(err);
        log.engine.warn(`[Planner] Step ${step.id} failed: ${step.result}`);
      }
    }

    // Final synthesis: combine all step results into a cohesive response
    const completedSteps = plan.steps.filter((s) => s.status === "done");
    if (completedSteps.length === 0 && lastResponse) {
      return lastResponse;
    }

    const summaryPrompt =
      `You executed a multi-step plan for the user. Here are the results:\n\n` +
      `Original request: ${userMessage}\n\n` +
      plan.steps
        .map(
          (s) =>
            `Step ${s.id} (${s.status}): ${s.description}\n  Result: ${s.result ?? "n/a"}`,
        )
        .join("\n\n") +
      `\n\nProvide a clear, cohesive summary of what was accomplished. If any steps failed, mention what couldn't be completed.`;

    const synthResponse = await context.provider.chat([
      {
        role: "system",
        content: "Synthesize multi-step task results concisely.",
      },
      { role: "user", content: summaryPrompt },
    ]);

    return {
      content: synthResponse.content,
      owlName: context.owl.persona.name,
      owlEmoji: context.owl.persona.emoji,
      challenged: false,
      toolsUsed: [...new Set(allToolsUsed)],
      modelUsed: synthResponse.model ?? "unknown",
      newMessages: allNewMessages,
      usage: synthResponse.usage
        ? {
            promptTokens: synthResponse.usage.promptTokens,
            completionTokens: synthResponse.usage.completionTokens,
          }
        : undefined,
      pendingFiles: context.pendingFiles ?? [],
    };
  }

  /**
   * Legacy error prompt — used when DiagnosticEngine is unavailable or
   * when the fail streak exceeds the max (at which point we want a hard stop).
   */
  private buildLegacyErrorPrompt(
    toolCall: ToolCall,
    toolResult: string,
    isSoftFailure: boolean,
    errorClass: "NON-RETRYABLE" | "TRANSIENT",
    streak: number,
    maxStreak: number = 2,
  ): string {
    const hasHint = toolResult.includes("[SYSTEM DIAGNOSTIC HINT:");
    const hintNote = hasHint
      ? `\n⚠️ THE RESULT ABOVE CONTAINS A [SYSTEM DIAGNOSTIC HINT] — THIS IS CRITICAL. ` +
        `Read the hint carefully. It tells you exactly what went wrong and what tool or approach to use instead. ` +
        `You MUST follow it. Do not repeat the same action that produced this hint.`
      : "";

    const errorClassNote =
      errorClass === "NON-RETRYABLE"
        ? `\n⛔ ERROR CLASS: [NON-RETRYABLE] — This failure will repeat regardless of how you retry it. ` +
          `Do NOT call "${toolCall.name}" again with any variation of these arguments. ` +
          `Switch tools or approach entirely, or tell the user it cannot be done in this environment.`
        : `\n♻️ ERROR CLASS: [TRANSIENT] — This may be a temporary issue (network, rate-limit). ` +
          `Try a different tool or approach rather than retrying the same call immediately.`;

    return (
      `[SYSTEM OVERRIDE: ERROR ANALYSIS REQUIRED — failure #${streak}]\n` +
      `Tool: "${toolCall.name}"\n` +
      `Result: ${isSoftFailure ? "returned non-zero exit code or diagnostic hint (soft failure)" : "threw an exception (hard failure)"}\n` +
      errorClassNote +
      "\n\n" +
      `You MUST step back and reason through this before your next action:\n` +
      `1. Read the full tool result above — the error is described there.\n` +
      `2. If a DIAGNOSTIC HINT is present, follow it exactly — it overrides your assumptions.\n` +
      `3. Do NOT retry the same command with the same arguments.\n` +
      `4. If the tool requires something unavailable here (e.g. curl in a no-network sandbox), ` +
      `USE A DIFFERENT TOOL (e.g. duckduckgo_search for web search, web_crawl for URL fetching).\n` +
      hintNote +
      "\n\n" +
      (streak >= maxStreak
        ? `🛑 CRITICAL: This tool has failed ${streak} consecutive times. ` +
          `DO NOT call "${toolCall.name}" again under any circumstances. ` +
          `Switch to a completely different approach or tool NOW.`
        : `Choose a different approach for your next tool call.`)
    );
  }
}

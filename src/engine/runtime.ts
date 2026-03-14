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
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

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
  /** Channel-provided callback to send a file/image to the user. Path must be absolute. */
  sendFile?: (filePath: string, caption?: string) => Promise<void>;
  /** Skills registry — used by CapabilityNeedAssessor to check coverage before synthesis */
  skillsRegistry?: import("../skills/registry.js").SkillsRegistry;
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
}

// ─── Constants ───────────────────────────────────────────────────

/** Default max iterations — overridable via config.engine.maxToolIterations */
const DEFAULT_MAX_TOOL_ITERATIONS = 15;

/**
 * OpenCLAW-style completion signal.
 * The model is instructed to end its content with [DONE] when it has a complete
 * answer and does not need any further tool calls. The engine checks this BEFORE
 * executing tool calls in each iteration — if the signal is present, all pending
 * tool calls are dropped and the loop exits immediately.
 */
const DONE_SIGNAL = "[DONE]";

function hasDoneSignal(content: string): boolean {
  return content.includes(DONE_SIGNAL);
}

function stripDoneSignal(content: string): string {
  return content.replace(/\[DONE\]/g, "").trim();
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
  let usage: { promptTokens: number; completionTokens: number; totalTokens: number } | undefined;
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
    const MAX_TOOL_ITERATIONS =
      config.engine?.maxToolIterations ?? DEFAULT_MAX_TOOL_ITERATIONS;

    // Track if a missing-tool gap was encountered during the ReAct loop
    let missingToolName: string | undefined;

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
    );

    // 3. Compress history if too long to prevent context drift on local models
    // If isolatedTask is true, only use the last 2 messages (recent context only)
    let historyToUse = sessionHistory;
    if (context.isolatedTask && sessionHistory.length > 2) {
      // For isolated tasks, only use last 2 messages to prevent context bleeding
      historyToUse = sessionHistory.slice(-2);
      log.engine.info(
        `Task isolated: using only last ${historyToUse.length} messages instead of ${sessionHistory.length}`,
      );
    } else {
      const maxTokens = config.engine?.maxContextTokens ?? 8000;
      const keepRecent = config.engine?.contextKeepRecent ?? 10;
      const estTokens = estimateTokens(sessionHistory);
      const needsCompression =
        sessionHistory.length > CONTEXT_WINDOW_THRESHOLD || estTokens > maxTokens;

      if (needsCompression) {
        // Two-tier: keep last N messages verbatim, compress the rest
        const recentMessages = sessionHistory.slice(-keepRecent);
        const olderMessages = sessionHistory.slice(0, -keepRecent);

        if (olderMessages.length > 0) {
          const compressionFallback = new Promise<ChatMessage[]>((resolve) =>
            setTimeout(() => resolve(recentMessages), 5000),
          );
          historyToUse = await Promise.race([
            this.compressHistory(olderMessages, currentProvider, optimalModel).then(
              (compressed) => [...compressed, ...recentMessages],
            ),
            compressionFallback,
          ]);
        } else {
          historyToUse = recentMessages;
        }
        log.engine.info(
          `Context compressed: ${sessionHistory.length} msgs (~${estTokens} tokens) → ${historyToUse.length} msgs`,
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

    let finalUserMessage =
      taskIsolationDirective +
      `<NEW_TASK>
${userMessage}
</NEW_TASK>`;
    if (toolRegistry && toolRegistry.getDefinitions().length > 0) {
      finalUserMessage +=
        `\n\n[SYSTEM DIRECTIVE — ReAct Rules]\n` +
        `1. USE TOOLS only when you genuinely need information you do not already have ` +
        `(e.g. reading a file, running code, fetching a URL). ` +
        `Do NOT use tools to verify or double-check answers you are already confident in.\n` +
        `2. ANSWER DIRECTLY if the question is factual, conversational, or answerable from context/memory. ` +
        `Do not use a tool just because tools are available.\n` +
        `3. SIGNAL COMPLETION: when your response is the final answer, append [DONE] at the very end. ` +
        `The engine will drop any pending tool calls and return your answer immediately.\n` +
        `4. CAPABILITY GAP: output [CAPABILITY_GAP: <description>] ONLY if the request requires a genuine SYSTEM ACTION ` +
        `(OS-level, hardware, network call to an external service) that no available tool or shell command can perform. ` +
        `Do NOT use this for knowledge gaps, analysis, or tasks solvable with run_shell_command.\n` +
        `5. NEVER call the same tool with the same arguments twice — the result is already in your context.`;
    }

    const messages: ChatMessage[] = [
      { role: "system", content: systemPrompt },
      ...historyToUse,
      { role: "user", content: finalUserMessage },
    ];

    // 5. ReAct loop — call model, handle tool calls iteratively
    let response: ChatResponse;
    let iterations = 0;
    let globalConsecutiveFailures = 0;
    let loopBrokenEarly = false; // set true when inner shouldBreakLoop fires
    const tools = toolRegistry?.getDefinitions();

    if (tools && tools.length > 0) {
      // Per-tool consecutive failure tracker for this ReAct session
      const toolFailStreak: Record<string, number> = {};
      const MAX_TOOL_FAIL_STREAK = 2; // Inject stop directive after this many consecutive failures

      // Duplicate tool call guard: fingerprint = "toolName:argsJSON"
      // If the model calls the exact same tool with the exact same args a second time,
      // skip execution and inject a hint — the result is already in context.
      const seenToolCalls = new Set<string>();

      // Sliding-window loop detector — track the last N tool names called.
      // Even if args differ slightly, calling the same tool > TOOL_WINDOW_MAX_REPEATS
      // times in a short window means the model is stuck. Inject a forced stop.
      const recentToolNames: string[] = [];
      const TOOL_WINDOW_SIZE = 10;
      const TOOL_WINDOW_MAX_REPEATS = 3;

      // ReAct loop with tools — use streaming when available
      log.engine.llmRequest(optimalModel, messages);
      if (currentProvider.chatWithToolsStream && context.onStreamEvent) {
        response = await consumeStream(
          currentProvider.chatWithToolsStream(messages, tools, optimalModel),
          context.onStreamEvent,
        );
      } else {
        response = await currentProvider.chatWithTools(
          messages,
          tools,
          optimalModel,
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
        for (const toolCall of response.toolCalls) {
          log.tool.toolCall(toolCall.name, toolCall.arguments);

          // ── Duplicate tool call guard ──────────────────────────────
          const callFingerprint = `${toolCall.name}:${JSON.stringify(toolCall.arguments)}`;
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
          if (repeatCount > TOOL_WINDOW_MAX_REPEATS) {
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

        // Fire all tool executions concurrently
        const executionResults = new Map<
          string,
          { result: string; isHardFailure: boolean }
        >();

        if (executableActions.length > 0) {
          if (context.onProgress) {
            const toolNames = executableActions.map((a) => `\`${a.toolCall.name}\``).join(", ");
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
                  .catch(() => {});
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

                const hasHint = toolResult.includes("[SYSTEM DIAGNOSTIC HINT:");
                const hintNote = hasHint
                  ? `\n⚠️ THE RESULT ABOVE CONTAINS A [SYSTEM DIAGNOSTIC HINT] — THIS IS CRITICAL. ` +
                    `Read the hint carefully. It tells you exactly what went wrong and what tool or approach to use instead. ` +
                    `You MUST follow it. Do not repeat the same action that produced this hint.`
                  : "";

                const errorClass = classifyToolError(toolResult);
                const errorClassNote =
                  errorClass === "NON-RETRYABLE"
                    ? `\n⛔ ERROR CLASS: [NON-RETRYABLE] — This failure will repeat regardless of how you retry it. ` +
                      `Do NOT call "${toolCall.name}" again with any variation of these arguments. ` +
                      `Switch tools or approach entirely, or tell the user it cannot be done in this environment.`
                    : `\n♻️ ERROR CLASS: [TRANSIENT] — This may be a temporary issue (network, rate-limit). ` +
                      `Try a different tool or approach rather than retrying the same call immediately.`;

                const analysisPrompt =
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
                  `USE A DIFFERENT TOOL (e.g. google_search for web search, web_crawl for URL fetching).\n` +
                  hintNote +
                  "\n\n" +
                  (streak >= MAX_TOOL_FAIL_STREAK
                    ? `🛑 CRITICAL: This tool has failed ${streak} consecutive times. ` +
                      `DO NOT call "${toolCall.name}" again under any circumstances. ` +
                      `Switch to a completely different approach or tool NOW.`
                    : `Choose a different approach for your next tool call.`);

                log.engine.warn(
                  `Tool "${toolCall.name}" ${isSoftFailure ? "soft-failed" : "hard-failed"} (streak: ${streak}) — injecting self-healing directive`,
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
              }
              continue;
            }
          }
        }

        if (shouldBreakLoop) break;

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
            currentProvider.chatWithToolsStream(messages, tools, optimalModel),
            context.onStreamEvent,
          );
        } else {
          response = await currentProvider.chatWithTools(
            messages,
            tools,
            optimalModel,
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
      response = await provider.chat(messages, optimalModel);
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
      };
    }

    // 8. Gap detection
    //    We skip the expensive natural-language GapDetector if tools were used,
    //    to avoid false positives on routine text. BUT we MUST honor explicit structured
    //    markers [CAPABILITY_GAP: ...] even if tools were used mid-task.
    const usedAtLeastOneTool = toolsUsed.length > 0;
    const hasExplicitMarker = response.content.match(
      /\[CAPABILITY_GAP:\s*([^\]]+)\]/i,
    );

    const shouldSkipNlpDetection =
      context.skipGapDetection || (usedAtLeastOneTool && !hasExplicitMarker);

    if (shouldSkipNlpDetection) {
      log.evolution.debug(
        `Skipping NLP gap detection (${context.skipGapDetection ? "retry mode" : "tools used"})`,
      );
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
  ): Promise<string> {
    const { persona, dna } = owl;

    let prompt = `# You are ${persona.emoji} ${persona.name} — ${persona.type}\n\n`;
    prompt += persona.systemPrompt + "\n\n";

    prompt += "## Host Environment\n";
    prompt += `- OS Platform: ${process.platform}\n`;
    prompt += `- OS Architecture: ${process.arch}\n\n`;

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

    // User preferences from PreferenceStore — inject only when present
    if (preferencesContext?.trim()) {
      prompt += "\n" + preferencesContext + "\n";
    }

    // Skills — injected only when present (always-on + relevant per-message)
    if (skillsContext?.trim()) {
      prompt += "\n" + skillsContext + "\n";
    }

    // Persistent memory — cap at 1500 chars to control prompt size
    if (memoryContext?.trim()) {
      const mem = memoryContext.slice(0, 1500);
      prompt += `\n## Memory\n${mem}`;
      if (memoryContext.length > 1500) prompt += "\n...[truncated]";
      prompt += "\n";
    }

    // Cross-turn attempt log — injected FRESH every request, never compressed away.
    // This is the primary mechanism preventing the model from repeating approaches
    // that already failed in previous messages of this conversation.
    if (attemptLogBlock?.trim()) {
      prompt += `\n${attemptLogBlock}\n`;
    }

    // Relevant pellets — inject top 3, capped at 400 chars each.
    // Hard limit prevents context explosion when pellet store is large.
    if (pelletStore && userMessage) {
      try {
        const top = (await pelletStore.search(userMessage)).slice(0, 3);
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
      } catch {
        /* non-fatal */
      }
    }

    // Tools — comprehensive documentation with examples
    if (toolRegistry) {
      const tools = toolRegistry.getDefinitions();
      if (tools.length > 0) {
        prompt += "\n## Tools Available\n";
        prompt +=
          "You have access to these tools. Use your judgment to choose the best one for each task:\n\n";

        // Group tools by category for better understanding
        const toolGuides: Record<string, string[]> = {
          "Web & Browser": [
            "google_search - Search the web for current information",
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

        // Self-improvement — strict criteria to prevent over-synthesis
        prompt +=
          "\n**Self-Improvement (last resort only):**\n" +
          "- ONLY emit [CAPABILITY_GAP: what you need] if ALL of these are true:\n" +
          "  1. The request requires a SYSTEM ACTION (file I/O, network, OS control, hardware)\n" +
          "  2. None of the available tools can accomplish it even with shell commands\n" +
          "  3. You already attempted to solve it with existing tools and failed\n" +
          "- DO NOT emit [CAPABILITY_GAP: ...] for: facts, explanations, summaries, analysis,\n" +
          "  conversational replies, tasks solvable with run_shell_command, or tasks where\n" +
          "  you simply lack knowledge (use google_search or web_crawl instead).\n";
      }
    }

    // Capability gap marker — only shown when tools are loaded
    if (toolRegistry && toolRegistry.getDefinitions().length > 0) {
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

    const tools = context.toolRegistry?.getDefinitions() ?? [];
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

    const synthResponse = await context.provider.chat(
      [
        { role: "system", content: "Synthesize multi-step task results concisely." },
        { role: "user", content: summaryPrompt },
      ],
    );

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
    };
  }
}

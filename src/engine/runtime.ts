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
} from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { PelletStore } from "../pellets/store.js";
import type { ProviderRegistry } from "../providers/registry.js";
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
  /** Provider registry to fetch fallback providers dynamically for cross-provider routing */
  providerRegistry?: ProviderRegistry;
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

const MAX_TOOL_ITERATIONS = 10;

/**
 * OpenCLAW-style completion signal.
 * The model is instructed to end its content with [DONE] when it has a complete
 * answer and does not need any further tool calls. The engine checks this BEFORE
 * executing tool calls in each iteration — if the signal is present, all pending
 * tool calls are dropped and the loop exits immediately.
 */
const DONE_SIGNAL = '[DONE]';

function hasDoneSignal(content: string): boolean {
  return content.includes(DONE_SIGNAL);
}

function stripDoneSignal(content: string): string {
  return content.replace(/\[DONE\]/g, '').trim();
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
  if (result.includes('[SYSTEM DIAGNOSTIC HINT:')) return true;
  return false;
}
const CONTEXT_WINDOW_THRESHOLD = 20;
const CONTEXT_COMPRESSION_BATCH = 10;

/**
 * Marker embedded in the response content when the ReAct loop exhausted all
 * iterations or broke due to repeated failures. The gateway uses this to
 * track stuck tasks across consecutive messages.
 */
export const EXHAUSTION_MARKER = '__STACKOWL_EXHAUSTED__';

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

    // Track if a missing-tool gap was encountered during the ReAct loop
    let missingToolName: string | undefined;

    // 1. Determine optimal model (heuristic, no LLM call)
    let routeDecision = ModelRouter.route(userMessage, config);
    let optimalModel = routeDecision.modelName;

    // Dynamic provider resolution based on route (if cross-provider routing is needed early)
    let currentProvider = provider;
    if (routeDecision.providerName && routeDecision.providerName !== provider.name && context.providerRegistry) {
      log.engine.warn(`Cross-provider routing on first turn: Swapping ${provider.name} for ${routeDecision.providerName}`);
      currentProvider = context.providerRegistry.get(routeDecision.providerName);
    }

    log.engine.model(optimalModel);

    // 2. Build system prompt (async — may inject pellets + memory + skills)
    const systemPrompt = await this.buildSystemPrompt(
      owl,
      toolRegistry,
      context.pelletStore,
      userMessage,
      context.memoryContext,
      context.preferencesContext,
      context.skillsContext,
    );

    // 3. Compress history if too long to prevent context drift on local models
    const wasLong = sessionHistory.length > CONTEXT_WINDOW_THRESHOLD;
    const compressedHistory = await this.compressHistory(
      sessionHistory,
      currentProvider,
      optimalModel,
    );
    if (wasLong) {
      log.engine.info(
        `Context compressed: ${sessionHistory.length} → ${compressedHistory.length} messages`,
      );
    }

    // 4. Assemble message list with a Late-Binding System Directive
    // Local models suffer from instruction drift across long contexts.
    // We inject the ReAct rule at the very bottom so it's the last thing they read.
    let finalUserMessage = `<NEW_TASK>\n${userMessage}\n</NEW_TASK>`;
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
        `4. CAPABILITY GAP: if you need a tool that doesn't exist, output [CAPABILITY_GAP: <description>].\n` +
        `5. NEVER call the same tool with the same arguments twice — the result is already in your context.`;
    }

    const messages: ChatMessage[] = [
      { role: "system", content: systemPrompt },
      ...compressedHistory,
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

      // ReAct loop with tools
      log.engine.llmRequest(optimalModel, messages);
      response = await currentProvider.chatWithTools(messages, tools, optimalModel);
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

        // Execute each tool and add results
        let shouldBreakLoop = false;
        for (const toolCall of response.toolCalls) {
          log.tool.toolCall(toolCall.name, toolCall.arguments);
          if (context.onProgress) {
            const argsStr =
              Object.keys(toolCall.arguments || {}).length > 0
                ? JSON.stringify(toolCall.arguments).slice(0, 50) + "..."
                : "";
            await context.onProgress(
              `⚙️ **Running tool:** \`${toolCall.name}\` ${argsStr}`,
            );
          }

          let toolResult: string;
          let isHardFailure = false;

          // ── Duplicate tool call guard ──────────────────────────────
          // If the model calls the exact same tool+args it already called
          // this session, skip execution entirely. The result is already in
          // the message context — re-running is pure waste and signals the
          // model is looping. Inject a direct "use the result above" hint.
          const callFingerprint = `${toolCall.name}:${JSON.stringify(toolCall.arguments)}`;
          if (seenToolCalls.has(callFingerprint)) {
            log.engine.warn(
              `Duplicate tool call skipped: ${toolCall.name} (same args already executed this session)`,
            );
            messages.push({
              role: "tool",
              content:
                `[SYSTEM: Duplicate call blocked. You already called "${toolCall.name}" with these exact arguments earlier in this session. ` +
                `The result is already present in your context above — read it and use it to form your final answer. ` +
                `Do NOT call this tool again with the same arguments.]`,
              toolCallId: toolCall.id,
              name: toolCall.name,
            });
            // Count as a soft failure so the model gets the analysis directive
            toolFailStreak[toolCall.name] = (toolFailStreak[toolCall.name] ?? 0) + 1;
            globalConsecutiveFailures++;
            continue;
          }
          seenToolCalls.add(callFingerprint);

          if (toolRegistry && !toolRegistry.has(toolCall.name)) {
            // Tool doesn't exist — signal gap and let the LLM know gracefully
            missingToolName = toolCall.name;
            toolResult = `Tool "${toolCall.name}" is not available in the current toolkit.`;
            log.tool.warn(
              `Tool not found: ${toolCall.name} — triggering gap detection`,
            );
          } else if (toolRegistry) {
            const toolCtx = {
              cwd: cwd || process.cwd(),
              engineContext: context,
            };
            try {
              toolResult = await toolRegistry.execute(
                toolCall.name,
                toolCall.arguments,
                toolCtx,
              );
              log.tool.toolResult(toolCall.name, toolResult, true);
              if (context.onProgress) {
                await context.onProgress(
                  `✅ **Tool finished:** \`${toolCall.name}\``,
                );
              }
            } catch (e) {
              isHardFailure = true;
              toolResult = `Tool execution failed: ${e instanceof Error ? e.message : String(e)}`;
              log.tool.toolResult(toolCall.name, toolResult, false);
              if (context.onProgress) {
                await context.onProgress(
                  `❌ **Tool failed:** \`${toolCall.name}\``,
                );
              }
            }

            // Detect soft failures: tool returned without throwing but the result
            // contains a non-zero EXIT_CODE or a SYSTEM DIAGNOSTIC HINT.
            // These must be treated the same as hard failures — the LLM must be
            // forced to read the result and switch strategy, not just retry.
            const isSoftFailure = !isHardFailure && isFailureResult(toolResult!);
            const isAnyFailure = isHardFailure || isSoftFailure;

            toolsUsed.push(toolCall.name);
            if (context.capabilityLedger) {
              context.capabilityLedger
                .recordUsage(toolCall.name, !isAnyFailure)
                .catch(() => { });
            }

            // On every failure (hard OR soft): force the LLM to reason about what
            // went wrong before deciding its next action. Without this, local models
            // read the error and immediately retry the same failing call.
            if (isAnyFailure) {
              toolFailStreak[toolCall.name] =
                (toolFailStreak[toolCall.name] ?? 0) + 1;
              const streak = toolFailStreak[toolCall.name];

              // If the result contains a DIAGNOSTIC HINT, force the LLM to act on it
              const hasHint = toolResult!.includes('[SYSTEM DIAGNOSTIC HINT:');
              const hintNote = hasHint
                ? `\n⚠️ THE RESULT ABOVE CONTAINS A [SYSTEM DIAGNOSTIC HINT] — THIS IS CRITICAL. ` +
                  `Read the hint carefully. It tells you exactly what went wrong and what tool or approach to use instead. ` +
                  `You MUST follow it. Do not repeat the same action that produced this hint.`
                : '';

              const analysisPrompt =
                `[SYSTEM OVERRIDE: ERROR ANALYSIS REQUIRED — failure #${streak}]\n` +
                `Tool: "${toolCall.name}"\n` +
                `Result: ${isSoftFailure ? 'returned non-zero exit code or diagnostic hint (soft failure)' : 'threw an exception (hard failure)'}\n\n` +
                `You MUST step back and reason through this before your next action:\n` +
                `1. Read the full tool result above — the error is described there.\n` +
                `2. If a DIAGNOSTIC HINT is present, follow it exactly — it overrides your assumptions.\n` +
                `3. Do NOT retry the same command with the same arguments.\n` +
                `4. If the tool requires something unavailable here (e.g. curl in a no-network sandbox), ` +
                `USE A DIFFERENT TOOL (e.g. web_crawl for URL fetching).\n` +
                hintNote + "\n\n" +
                (streak >= MAX_TOOL_FAIL_STREAK
                  ? `🛑 CRITICAL: This tool has failed ${streak} consecutive times. ` +
                    `DO NOT call "${toolCall.name}" again under any circumstances. ` +
                    `Switch to a completely different approach or tool NOW.`
                  : `Choose a different approach for your next tool call.`);

              log.engine.warn(
                `Tool "${toolCall.name}" ${isSoftFailure ? 'soft-failed' : 'hard-failed'} (streak: ${streak}) — injecting self-healing directive`,
              );
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
              // Genuine success — reset streaks
              toolFailStreak[toolCall.name] = 0;
              globalConsecutiveFailures = 0;
            }
          } else {
            toolResult = `Error: ToolRegistry not provided, cannot execute ${toolCall.name}`;
          }

          messages.push({
            role: "tool",
            content: toolResult!,
            toolCallId: toolCall.id,
            name: toolCall.name,
          });
        }

        if (shouldBreakLoop) break;

        // If we've failed multiple tool calls in a row across the whole loop,
        // it's highly likely the local model is hallucinating or stuck.
        // Try to trigger a fallback router switch to a heavier cloud model.
        if (globalConsecutiveFailures >= 2) {
          const newRoute = ModelRouter.route(userMessage, config, globalConsecutiveFailures);

          if (newRoute.providerName && newRoute.providerName !== currentProvider.name && context.providerRegistry) {
            try {
              const fallbackProvider = context.providerRegistry.get(newRoute.providerName);
              log.engine.warn(
                `[Cross-Provider Hot Swap] Tool failed ${globalConsecutiveFailures}x. Swapping provider: ${currentProvider.name} → ${newRoute.providerName}`,
              );
              currentProvider = fallbackProvider;
              if (context.onProgress) await context.onProgress(`🔄 **Cross-Provider Triggered:** Swapping to ${newRoute.providerName} (${newRoute.modelName}) to resolve failure.`);
            } catch (err) {
              log.engine.warn(`Could not swap to fallback provider "${newRoute.providerName}" - staying on current provider. Reason: ${(err as Error).message}`);
            }
          }

          if (newRoute.modelName && newRoute.modelName !== optimalModel) {
            log.engine.warn(
              `Tool failed ${globalConsecutiveFailures}x. Swapping model: ${optimalModel} → ${newRoute.modelName}`,
            );
            optimalModel = newRoute.modelName;
          }
        }

        // Continue the loop
        log.engine.llmRequest(optimalModel, messages);
        response = await currentProvider.chatWithTools(messages, tools, optimalModel);
        log.engine.llmResponse(
          optimalModel,
          response.content,
          response.toolCalls,
          response.usage,
        );
      }

      // ── Exhaustion check ──────────────────────────────────────────
      // If we hit the iteration cap (or broke due to repeated failures),
      // the LLM never reached a clean answer. Make one final call:
      // "you're stuck — tell the user what happened and offer options."
      const loopExhausted = iterations >= MAX_TOOL_ITERATIONS || loopBrokenEarly;
      if (loopExhausted) {
        log.engine.warn(
          `ReAct loop exhausted (${iterations} iterations, ${globalConsecutiveFailures} consecutive failures). ` +
          `Generating stuck-task summary for user.`
        );

        const toolSummary = toolsUsed.length > 0
          ? `Tools attempted: ${[...new Set(toolsUsed)].join(', ')}.`
          : 'No tools successfully completed.';

        const exhaustionPrompt: ChatMessage = {
          role: 'system',
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
        try {
          const exhaustionResponse = await currentProvider.chat(messages, optimalModel);
          // Tag the content so the gateway can track this as a stuck response
          response = {
            ...exhaustionResponse,
            content: exhaustionResponse.content + `\n${EXHAUSTION_MARKER}`,
          };
        } catch {
          // If even this fails, surface a hard-coded fallback
          response = {
            ...response,
            content:
              `I've tried ${iterations} different approaches and hit a wall each time.\n\n` +
              `**What I attempted:** ${toolSummary}\n\n` +
              `**Your options:**\n` +
              `a) Give me more details or a different angle to try\n` +
              `b) We try a completely different strategy — tell me what matters most\n` +
              `c) This task may not be possible in this environment\n\n` +
              `Which would you like?\n${EXHAUSTION_MARKER}`,
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
    const challenged = ['high', 'relentless'].includes(owl.dna.evolvedTraits.challengeLevel);

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
      low:       "Be supportive and affirming. Only push back when something is factually wrong.",
      medium:    "Offer your honest opinion. If you see a flaw in the user's plan, name it clearly once and explain why.",
      high:      "Actively interrogate assumptions. For any plan or decision, identify the biggest risk or weak point before agreeing.",
      relentless:"Be a rigorous adversary. Challenge every assumption. Steelman the opposing view. If the user's idea is sound, say so — but only after stress-testing it.",
    };
    const challengeDir = challengeDirectives[dna.evolvedTraits.challengeLevel] ?? challengeDirectives.medium;
    prompt += `**Challenge mode (${dna.evolvedTraits.challengeLevel}):** ${challengeDir}\n\n`;

    // Verbosity → concrete length and format instructions
    const verbosityDirectives: Record<string, string> = {
      terse:   "Be extremely concise. One sentence per point. No preamble. No sign-offs. Lead with the answer.",
      normal:  "Match the length to the complexity of the question. Don't pad.",
      verbose: "Explain your reasoning fully. Include relevant context, examples, and edge cases. Use headers for long responses.",
    };
    const verbosityDir = verbosityDirectives[dna.evolvedTraits.verbosity] ?? verbosityDirectives.normal;
    prompt += `**Verbosity (${dna.evolvedTraits.verbosity}):** ${verbosityDir}\n\n`;

    prompt += `You have had ${dna.interactionStats.totalConversations} conversation(s) with this user. Calibrate familiarity accordingly.\n`;

    // Learned preferences — only the strong signals (score > 0.7 or < 0.3)
    const strongPrefs = Object.entries(dna.learnedPreferences).filter(
      ([, s]) => s > 0.7 || s < 0.3,
    );
    if (strongPrefs.length > 0) {
      prompt += "\n## User Preferences\n";
      for (const [pref, score] of strongPrefs) {
        prompt += score > 0.7 ? `- Prefers: ${pref}\n` : `- Dislikes: ${pref}\n`;
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

    // Relevant pellets — inject top 2, capped at 400 chars each
    if (pelletStore && userMessage) {
      try {
        const top = (await pelletStore.search(userMessage)).slice(0, 2);
        if (top.length > 0) {
          prompt += "\n## Relevant Past Knowledge\n";
          for (const pellet of top) {
            prompt += `\n**${pellet.title}**`;
            if (pellet.tags.length > 0) prompt += ` [${pellet.tags.join(", ")}]`;
            prompt += `\n${pellet.content.slice(0, 400)}`;
            if (pellet.content.length > 400) prompt += "\n...[truncated]";
            prompt += "\n";
          }
        }
      } catch { /* non-fatal */ }
    }

    // Tools — compact one-liner per tool (schema injected by provider's tool-calling API)
    if (toolRegistry) {
      const tools = toolRegistry.getDefinitions();
      if (tools.length > 0) {
        prompt += "\n## Tools Available\n";
        prompt += "Use tools only when you need information you do not already have. Do NOT use tools to verify answers you are already confident in.\n";
        for (const tool of tools) {
          prompt += `- **${tool.name}**: ${tool.description}\n`;
        }
        prompt +=
          "\n**Tool discipline rules:**\n" +
          "- Call a tool only if the answer genuinely requires it (file content, live data, code execution).\n" +
          "- Never call the same tool with the same arguments twice — the previous result is already in your context.\n" +
          "- When your response IS the final answer, append `[DONE]` at the end. The engine will return your answer immediately and discard any pending tool calls.\n" +
          "- If you need a capability that no tool provides, output `[CAPABILITY_GAP: description]`.\n";
      }
    }

    // Capability gap marker — only shown when tools are loaded
    if (toolRegistry && toolRegistry.getDefinitions().length > 0) {
      prompt += "\n[CAPABILITY_GAP: ...] is stripped before display. Use it only for genuine missing tool/access gaps.\n";
    }

    // DNA reminder — last line so it's freshest in context window
    prompt += `\nApply challenge mode (${dna.evolvedTraits.challengeLevel}) and verbosity (${dna.evolvedTraits.verbosity}) at all times.\n`;

    return prompt;
  }

}

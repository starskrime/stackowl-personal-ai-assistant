/**
 * StackOwl — Owl Engine Runtime
 *
 * The core AI loop: ReAct (Receive → Think → Act → Observe → Respond)
 * with integrated Challenge Mode, sliding context window, and pellet injection.
 */

import type { ModelProvider, ChatMessage, ChatResponse } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import type { ToolRegistry } from '../tools/registry.js';
import type { CapabilityLedger } from '../evolution/ledger.js';
import type { StackOwlConfig } from '../config/loader.js';
import type { OwlRegistry } from '../owls/registry.js';
import type { PelletStore } from '../pellets/store.js';
import { ModelRouter } from './router.js';
import { GapDetector } from '../evolution/detector.js';
import { log } from '../logger.js';

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
const CONTEXT_WINDOW_THRESHOLD = 20;
const CONTEXT_COMPRESSION_BATCH = 10;

// ─── Owl Engine ──────────────────────────────────────────────────

export class OwlEngine {
    /**
     * Run the full ReAct + Challenge loop for a user message.
     */
    async run(
        userMessage: string,
        context: EngineContext
    ): Promise<EngineResponse> {
        const { provider, owl, sessionHistory, config, toolRegistry, cwd } = context;
        const toolsUsed: string[] = [];
        const gapDetector = new GapDetector();

        // Track if a missing-tool gap was encountered during the ReAct loop
        let missingToolName: string | undefined;

        // 1. Determine optimal model
        const optimalModel = await ModelRouter.route(userMessage, provider, config);
        log.engine.model(optimalModel);

        // 2. Build system prompt (async — may inject pellets + memory)
        const systemPrompt = await this.buildSystemPrompt(owl, toolRegistry, context.pelletStore, userMessage, context.memoryContext);

        // 3. Compress history if too long to prevent context drift on local models
        const wasLong = sessionHistory.length > CONTEXT_WINDOW_THRESHOLD;
        const compressedHistory = await this.compressHistory(sessionHistory, provider, optimalModel);
        if (wasLong) {
            log.engine.info(`Context compressed: ${sessionHistory.length} → ${compressedHistory.length} messages`);
        }

        // 4. Assemble message list with a Late-Binding System Directive
        // Local models suffer from instruction drift across long contexts.
        // We inject the ReAct rule at the very bottom so it's the last thing they read.
        let finalUserMessage = userMessage;
        if (toolRegistry && toolRegistry.getDefinitions().length > 0) {
            finalUserMessage += `\n\n[SYSTEM DIRECTIVE: You are an agent, not a chatbot. If answering this request correctly requires investigating the machine, network, files, or writing code, YOU MUST CALL A TOOL FIRST. Do NOT guess or answer from memory. If you lack a tool necessary to complete this task, you MUST output [CAPABILITY_GAP: <desc>] to trigger the tool synthesizer.]`;
        }

        const messages: ChatMessage[] = [
            { role: 'system', content: systemPrompt },
            ...compressedHistory,
            { role: 'user', content: finalUserMessage },
        ];

        // 5. ReAct loop — call model, handle tool calls iteratively
        let response: ChatResponse;
        let iterations = 0;
        const tools = toolRegistry?.getDefinitions();

        if (tools && tools.length > 0) {
            // Per-tool consecutive failure tracker for this ReAct session
            const toolFailStreak: Record<string, number> = {};
            const MAX_TOOL_FAIL_STREAK = 2; // Inject stop directive after this many consecutive failures

            // ReAct loop with tools
            log.engine.llmRequest(optimalModel, messages);
            response = await provider.chatWithTools(messages, tools, optimalModel);
            log.engine.llmResponse(optimalModel, response.content, response.toolCalls, response.usage);

            while (response.toolCalls && response.toolCalls.length > 0 && iterations < MAX_TOOL_ITERATIONS) {
                iterations++;

                if (response.content && context.onProgress) {
                    await context.onProgress(`_Thinking..._\n${response.content}`);
                }

                // Add assistant's tool call message
                messages.push({
                    role: 'assistant',
                    content: response.content || '',
                    toolCalls: response.toolCalls,
                });

                // Execute each tool and add results
                let shouldBreakLoop = false;
                for (const toolCall of response.toolCalls) {
                    log.tool.toolCall(toolCall.name, toolCall.arguments);
                    if (context.onProgress) {
                        const argsStr = Object.keys(toolCall.arguments || {}).length > 0
                            ? JSON.stringify(toolCall.arguments).slice(0, 50) + '...'
                            : '';
                        await context.onProgress(`⚙️ **Running tool:** \`${toolCall.name}\` ${argsStr}`);
                    }

                    let toolResult: string;

                    if (toolRegistry && !toolRegistry.has(toolCall.name)) {
                        // Tool doesn't exist — signal gap and let the LLM know gracefully
                        missingToolName = toolCall.name;
                        toolResult = `Tool "${toolCall.name}" is not available in the current toolkit.`;
                        log.tool.warn(`Tool not found: ${toolCall.name} — triggering gap detection`);
                    } else if (toolRegistry) {
                        const toolCtx = {
                            cwd: cwd || process.cwd(),
                            engineContext: context
                        };
                        let success = true;
                        try {
                            toolResult = await toolRegistry.execute(toolCall.name, toolCall.arguments, toolCtx);
                            log.tool.toolResult(toolCall.name, toolResult, true);
                            // Reset streak on success
                            toolFailStreak[toolCall.name] = 0;
                            if (context.onProgress) {
                                await context.onProgress(`✅ **Tool finished:** \`${toolCall.name}\``);
                            }
                        } catch (e) {
                            success = false;
                            toolResult = `Tool execution failed: ${e instanceof Error ? e.message : String(e)}`;
                            log.tool.toolResult(toolCall.name, toolResult, false);
                            if (context.onProgress) {
                                await context.onProgress(`❌ **Tool failed:** \`${toolCall.name}\``);
                            }
                        }
                        toolsUsed.push(toolCall.name);
                        if (context.capabilityLedger) {
                            context.capabilityLedger.recordUsage(toolCall.name, success).catch(() => { });
                        }

                        // On every failure: force the LLM to reason about what went wrong
                        // before it decides its next action. Without this, local models
                        // read the error and immediately retry the same call.
                        if (!success) {
                            toolFailStreak[toolCall.name] = (toolFailStreak[toolCall.name] ?? 0) + 1;
                            const streak = toolFailStreak[toolCall.name];

                            const analysisPrompt =
                                `[ERROR ANALYSIS REQUIRED — do this before your next action]\n` +
                                `Tool "${toolCall.name}" just failed (failure #${streak}).\n` +
                                `Error: ${toolResult}\n\n` +
                                `You MUST reason through this step by step:\n` +
                                `1. WHY did it fail? (wrong args? broken tool? missing dependency? bad URL? wrong path?)\n` +
                                `2. Is this fixable? If yes — what exactly will you do differently?\n` +
                                `3. If not fixable — what alternative approach achieves the same goal?\n\n` +
                                (streak >= MAX_TOOL_FAIL_STREAK
                                    ? `⚠️ This tool has failed ${streak} times. Do NOT call "${toolCall.name}" again. Switch to a different approach now.`
                                    : `Think carefully before your next tool call.`);

                            log.engine.warn(`Tool "${toolCall.name}" failed (streak: ${streak}) — injecting error analysis prompt`);
                            messages.push({ role: 'system', content: analysisPrompt });

                            if (streak >= MAX_TOOL_FAIL_STREAK + 1) {
                                // LLM is still ignoring the analysis — break the loop
                                log.engine.warn(`LLM ignored error analysis ${streak}x — breaking ReAct loop`);
                                shouldBreakLoop = true;
                            }
                        }
                    } else {
                        toolResult = `Error: ToolRegistry not provided, cannot execute ${toolCall.name}`;
                    }

                    messages.push({
                        role: 'tool',
                        content: toolResult,
                        toolCallId: toolCall.id,
                        name: toolCall.name,
                    });
                }

                if (shouldBreakLoop) break;

                // Continue the loop
                log.engine.llmRequest(optimalModel, messages);
                response = await provider.chatWithTools(messages, tools, optimalModel);
                log.engine.llmResponse(optimalModel, response.content, response.toolCalls, response.usage);
            }
        } else {
            // Simple chat without tools
            log.engine.llmRequest(optimalModel, messages);
            response = await provider.chat(messages, optimalModel);
            log.engine.llmResponse(optimalModel, response.content, undefined, response.usage);
        }

        // 6. Check if challenge mode should engage
        const challenged = this.shouldChallenge(userMessage, owl);

        // Calculate which messages were added *during* this specific run (excluding the initial system+history+user)
        const initialMessageCount = sessionHistory.length + 2; // +2 for System and User prompt
        const newMessages = messages.length > initialMessageCount
            ? messages.slice(initialMessageCount)
            : [];

        if (toolsUsed.length > 0) {
            log.engine.info(`ReAct loop done — ${iterations} iteration(s), tools used: ${toolsUsed.join(', ')}`);
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
                    ? { promptTokens: response.usage.promptTokens, completionTokens: response.usage.completionTokens }
                    : undefined,
                pendingCapabilityGap: gapDetector.fromMissingTool(missingToolName, userMessage),
            };
        }

        // 8. Gap detection
        //    We skip the expensive natural-language GapDetector if tools were used,
        //    to avoid false positives on routine text. BUT we MUST honor explicit structured
        //    markers [CAPABILITY_GAP: ...] even if tools were used mid-task.
        const usedAtLeastOneTool = toolsUsed.length > 0;
        const hasExplicitMarker = response.content.match(/\[CAPABILITY_GAP:\s*([^\]]+)\]/i);

        const shouldSkipNlpDetection = context.skipGapDetection || (usedAtLeastOneTool && !hasExplicitMarker);

        if (shouldSkipNlpDetection) {
            log.evolution.debug(`Skipping NLP gap detection (${context.skipGapDetection ? 'retry mode' : 'tools used'})`);
        } else {
            log.evolution.debug(`Checking response for capability gap...`);
            const nlGap = await gapDetector.detectFromResponse(response.content, userMessage, provider, optimalModel);
            if (nlGap) {
                log.evolution.warn(`Gap confirmed: "${nlGap.description.slice(0, 80)}"`);
                // Strip the marker from content before displaying to the user
                const cleanContent = response.content.replace(/\[CAPABILITY_GAP:[^\]]*\]/gi, '').trim();
                return {
                    content: cleanContent,
                    owlName: owl.persona.name,
                    owlEmoji: owl.persona.emoji,
                    challenged,
                    toolsUsed,
                    modelUsed: optimalModel,
                    newMessages,
                    usage: response.usage
                        ? { promptTokens: response.usage.promptTokens, completionTokens: response.usage.completionTokens }
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
        model: string
    ): Promise<ChatMessage[]> {
        if (history.length <= CONTEXT_WINDOW_THRESHOLD) return history;

        const toCompress = history.slice(0, CONTEXT_COMPRESSION_BATCH);
        const remaining = history.slice(CONTEXT_COMPRESSION_BATCH);

        const transcript = toCompress
            .map(m => `[${m.role.toUpperCase()}]: ${m.content?.slice(0, 300) ?? ''}`)
            .join('\n\n');

        try {
            const summaryResponse = await provider.chat([
                {
                    role: 'system',
                    content: 'You are a concise summarizer. Summarize the following conversation excerpt into 3-5 bullet points capturing the key decisions, facts, and context. Be extremely brief.',
                },
                { role: 'user', content: transcript },
            ], model);

            const memoryBlock: ChatMessage = {
                role: 'system',
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
        memoryContext?: string
    ): Promise<string> {
        const { persona, dna } = owl;

        let prompt = `# You are ${persona.emoji} ${persona.name} — ${persona.type}\n\n`;
        prompt += persona.systemPrompt + '\n\n';

        prompt += '## Host Environment\n';
        prompt += `- OS Platform: ${process.platform}\n`;
        prompt += `- OS Architecture: ${process.arch}\n\n`;

        // Inject DNA-influenced behavior modifiers
        prompt += '## Current Behavioral Calibration (from Owl DNA)\n';
        prompt += `- Challenge Level: ${dna.evolvedTraits.challengeLevel}\n`;
        prompt += `- Verbosity: ${dna.evolvedTraits.verbosity}\n`;
        prompt += `- Total conversations with this user: ${dna.interactionStats.totalConversations}\n`;

        if (Object.keys(dna.learnedPreferences).length > 0) {
            prompt += '\n## Learned User Preferences\n';
            for (const [pref, score] of Object.entries(dna.learnedPreferences)) {
                if (score > 0.7) {
                    prompt += `- User strongly prefers: ${pref}\n`;
                } else if (score < 0.3) {
                    prompt += `- User dislikes: ${pref}\n`;
                }
            }
        }

        if (Object.keys(dna.expertiseGrowth).length > 0) {
            const topExpertise = Object.entries(dna.expertiseGrowth)
                .sort(([, a], [, b]) => b - a)
                .slice(0, 5);

            if (topExpertise.length > 0) {
                prompt += '\n## Your Growing Expertise\n';
                for (const [domain, score] of topExpertise) {
                    prompt += `- ${domain}: ${Math.round(score * 100)}% proficiency\n`;
                }
            }
        }

        // Inject persistent memory (facts from past sessions)
        if (memoryContext && memoryContext.trim().length > 0) {
            prompt += '\n## Persistent Memory (facts from previous sessions)\n';
            prompt += memoryContext.slice(0, 2000);
            if (memoryContext.length > 2000) prompt += '\n...[truncated]';
            prompt += '\n';
        }

        // Inject relevant pellets based on current message
        if (pelletStore && userMessage) {
            try {
                const relevant = await pelletStore.search(userMessage);
                const top = relevant.slice(0, 3);
                if (top.length > 0) {
                    prompt += '\n## Relevant Knowledge from Previous Discussions\n';
                    for (const pellet of top) {
                        prompt += `\n### ${pellet.title}`;
                        if (pellet.tags.length > 0) prompt += ` (tags: ${pellet.tags.join(', ')})`;
                        prompt += '\n';
                        prompt += pellet.content.slice(0, 500);
                        if (pellet.content.length > 500) prompt += '\n...[truncated]';
                        prompt += '\n';
                    }
                }
            } catch {
                // Non-fatal — pellets are optional enrichment
            }
        }

        // Inject loaded tool definitions so the LLM knows what it can call and exactly how to call it
        // Local models MUST have the JSON schema explicitly in the context window
        if (toolRegistry) {
            const tools = toolRegistry.getDefinitions();
            if (tools.length > 0) {
                prompt += '\n## Your Agency and Toolkit\n';
                prompt += `You are an AUTONOMOUS AGENT running natively on the user's local machine.\n`;
                prompt += `You have direct access to the system via the following tools. DO NOT act like a helpless cloud chatbot.\n`;
                prompt += `If the user asks you to do something (like check a port, read a file, or run a test), DO NOT tell them how to do it manually. Execute the action yourself using your tools.\n\n`;
                prompt += 'Available Tools (with JSON Schema Parameters):\n';
                tools.forEach(tool => {
                    prompt += `- ${tool.name}: ${tool.description}\n`;
                    prompt += `  Parameters Schema: ${JSON.stringify(tool.parameters.properties)}\n`;
                });
                prompt += '\nALWAYS check this list before saying you cannot do something. If a tool exists for the task, USE IT.\n';
            }
        }

        // Self-improvement marker
        prompt += '\n## IMPORTANT: Capability Gaps\n';
        prompt += 'You have a set of tools available. If a user asks you to do something that requires a tool or capability you do NOT have,\n';
        prompt += 'you MUST include this exact marker somewhere in your response:\n';
        prompt += '[CAPABILITY_GAP: one sentence describing what you tried to do and why you cannot]\n';
        prompt += 'Example: [CAPABILITY_GAP: tried to take a screenshot but no screen capture tool is available]\n';
        prompt += 'Example: [CAPABILITY_GAP: tried to send an email but no email/SMTP tool is available]\n';
        prompt += 'This marker is invisible to the user — it is stripped before display. Do NOT add it if you simply choose not to do something for ethical reasons.\n';

        // Core directive: challenge mode
        prompt += '\n## IMPORTANT: Challenge Mode\n';
        prompt += 'You are NOT a yes-man. You are a colleague who genuinely cares about getting the right answer.\n';
        prompt += `Your challenge level is set to "${dna.evolvedTraits.challengeLevel}".\n`;
        prompt += '- If the user\'s request seems flawed, say so and explain why.\n';
        prompt += '- If you see a better approach, propose it.\n';
        prompt += '- If you agree, explain why you agree — don\'t just nod along.\n';
        prompt += '- Always be respectful but honest. Disagree with ideas, not people.\n';

        return prompt;
    }

    /**
     * Determine if the owl should actively challenge the user's input.
     * This is a heuristic — in production, the LLM itself decides via the system prompt.
     */
    private shouldChallenge(_userMessage: string, owl: OwlInstance): boolean {
        // Challenge probability increases with challenge level
        const challengeLevels: Record<string, number> = {
            low: 0.2,
            medium: 0.4,
            high: 0.7,
            relentless: 0.9,
        };

        const threshold = challengeLevels[owl.dna.evolvedTraits.challengeLevel] ?? 0.4;
        return Math.random() < threshold;
    }
}

/**
 * StackOwl — Owl Engine Runtime
 *
 * The core AI loop: ReAct (Receive → Think → Act → Observe → Respond)
 * with integrated Challenge Mode.
 */

import type { ModelProvider, ChatMessage, ChatResponse } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import type { ToolRegistry } from '../tools/registry.js';
import type { StackOwlConfig } from '../config/loader.js';
import { ModelRouter } from './router.js';
import { GapDetector } from '../evolution/detector.js';

// ─── Types ───────────────────────────────────────────────────────

export interface EngineContext {
    provider: ModelProvider;
    owl: OwlInstance;
    sessionHistory: ChatMessage[];
    config: StackOwlConfig;
    toolRegistry?: ToolRegistry;
    cwd?: string;
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
    usage?: {
        promptTokens: number;
        completionTokens: number;
    };
    /** Set when the engine detected a capability gap that needs user approval to resolve */
    pendingCapabilityGap?: PendingCapabilityGap;
}

// ─── Constants ───────────────────────────────────────────────────

const MAX_TOOL_ITERATIONS = 10;

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

        // 2. Build system prompt from owl persona + DNA
        const systemPrompt = this.buildSystemPrompt(owl);

        // 3. Assemble message list
        const messages: ChatMessage[] = [
            { role: 'system', content: systemPrompt },
            ...sessionHistory,
            { role: 'user', content: userMessage },
        ];

        // 4. ReAct loop — call model, handle tool calls iteratively
        let response: ChatResponse;
        let iterations = 0;
        const tools = toolRegistry?.getDefinitions();

        if (tools && tools.length > 0) {
            // ReAct loop with tools
            response = await provider.chatWithTools(messages, tools, optimalModel);

            while (response.toolCalls && response.toolCalls.length > 0 && iterations < MAX_TOOL_ITERATIONS) {
                iterations++;

                // Add assistant's tool call message
                messages.push({
                    role: 'assistant',
                    content: response.content || '',
                    toolCalls: response.toolCalls,
                });

                // Execute each tool and add results
                for (const toolCall of response.toolCalls) {
                    let toolResult: string;

                    if (toolRegistry && !toolRegistry.has(toolCall.name)) {
                        // Tool doesn't exist — signal gap and let the LLM know gracefully
                        missingToolName = toolCall.name;
                        toolResult = `Tool "${toolCall.name}" is not available in the current toolkit.`;
                    } else if (toolRegistry) {
                        const toolCtx = { cwd: cwd || process.cwd() };
                        toolResult = await toolRegistry.execute(toolCall.name, toolCall.arguments, toolCtx);
                        toolsUsed.push(toolCall.name);
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

                // Continue the loop
                response = await provider.chatWithTools(messages, tools, optimalModel);
            }
        } else {
            // Simple chat without tools
            response = await provider.chat(messages, optimalModel);
        }

        // 5. Check if challenge mode should engage
        const challenged = this.shouldChallenge(userMessage, owl);

        // 6. Gap detection — tool call attempted but tool doesn't exist
        if (missingToolName) {
            console.log(`[Evolution] Tool-not-found gap detected: "${missingToolName}"`);

            return {
                content: response.content,
                owlName: owl.persona.name,
                owlEmoji: owl.persona.emoji,
                challenged,
                toolsUsed,
                modelUsed: optimalModel,
                usage: response.usage
                    ? { promptTokens: response.usage.promptTokens, completionTokens: response.usage.completionTokens }
                    : undefined,
                pendingCapabilityGap: gapDetector.fromMissingTool(missingToolName, userMessage),
            };
        }

        // 7. Gap detection — LLM expressed inability in natural language
        console.log(`[Evolution] checking response for gap:\n  "${response.content.slice(0, 200).replace(/\n/g, ' ')}"`);
        const nlGap = gapDetector.detectFromResponse(response.content, userMessage);
        if (nlGap) {
            console.log(`[Evolution] NL gap detected: "${nlGap.description.slice(0, 80)}..."`);
            // Strip the marker from content before displaying to the user
            const cleanContent = response.content.replace(/\[CAPABILITY_GAP:[^\]]*\]/gi, '').trim();
            return {
                content: cleanContent,
                owlName: owl.persona.name,
                owlEmoji: owl.persona.emoji,
                challenged,
                toolsUsed,
                modelUsed: optimalModel,
                usage: response.usage
                    ? { promptTokens: response.usage.promptTokens, completionTokens: response.usage.completionTokens }
                    : undefined,
                pendingCapabilityGap: nlGap,
            };
        }

        return {
            content: response.content,
            owlName: owl.persona.name,
            owlEmoji: owl.persona.emoji,
            challenged,
            toolsUsed,
            modelUsed: optimalModel,
            usage: response.usage
                ? {
                    promptTokens: response.usage.promptTokens,
                    completionTokens: response.usage.completionTokens,
                }
                : undefined,
        };
    }

    /**
     * Build the system prompt from owl persona + DNA state.
     */
    private buildSystemPrompt(owl: OwlInstance): string {
        const { persona, dna } = owl;

        let prompt = `# You are ${persona.emoji} ${persona.name} — ${persona.type}\n\n`;
        prompt += persona.systemPrompt + '\n\n';

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

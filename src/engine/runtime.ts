/**
 * StackOwl — Owl Engine Runtime
 *
 * The core AI loop: ReAct (Receive → Think → Act → Observe → Respond)
 * with integrated Challenge Mode.
 */

import type { ModelProvider, ChatMessage, ToolDefinition, ChatResponse } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';

// ─── Types ───────────────────────────────────────────────────────

export interface EngineContext {
    provider: ModelProvider;
    owl: OwlInstance;
    sessionHistory: ChatMessage[];
    tools?: ToolDefinition[];
    model?: string;
}

export interface EngineResponse {
    content: string;
    owlName: string;
    owlEmoji: string;
    challenged: boolean;
    toolsUsed: string[];
    usage?: {
        promptTokens: number;
        completionTokens: number;
    };
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
        const { provider, owl, sessionHistory, tools, model } = context;
        const toolsUsed: string[] = [];

        // 1. Build system prompt from owl persona + DNA
        const systemPrompt = this.buildSystemPrompt(owl);

        // 2. Assemble message list
        const messages: ChatMessage[] = [
            { role: 'system', content: systemPrompt },
            ...sessionHistory,
            { role: 'user', content: userMessage },
        ];

        // 3. ReAct loop — call model, handle tool calls iteratively
        let response: ChatResponse;
        let iterations = 0;

        if (tools && tools.length > 0) {
            // ReAct loop with tools
            response = await provider.chatWithTools(messages, tools, model);

            while (response.toolCalls && response.toolCalls.length > 0 && iterations < MAX_TOOL_ITERATIONS) {
                iterations++;

                // Add assistant's tool call message
                messages.push({
                    role: 'assistant',
                    content: response.content || '',
                });

                // Execute each tool and add results
                for (const toolCall of response.toolCalls) {
                    toolsUsed.push(toolCall.name);
                    // Tool execution would happen here — for now, placeholder
                    const toolResult = `[Tool "${toolCall.name}" executed with args: ${JSON.stringify(toolCall.arguments)}]`;
                    messages.push({
                        role: 'tool',
                        content: toolResult,
                        toolCallId: toolCall.id,
                        name: toolCall.name,
                    });
                }

                // Continue the loop
                response = await provider.chatWithTools(messages, tools, model);
            }
        } else {
            // Simple chat without tools
            response = await provider.chat(messages, model);
        }

        // 4. Check if challenge mode should engage
        const challenged = this.shouldChallenge(userMessage, owl);

        return {
            content: response.content,
            owlName: owl.persona.name,
            owlEmoji: owl.persona.emoji,
            challenged,
            toolsUsed,
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

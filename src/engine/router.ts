/**
 * StackOwl — Dynamic Model Router
 *
 * Intercepts a prompt and evaluates the task complexity, then routes it
 * to the most appropriate AI model from the configured `smartRouting` roster.
 */

import type { ModelProvider } from '../providers/base.js';
import type { StackOwlConfig } from '../config/loader.js';

export interface RouteDecision {
    modelName: string;
    providerName?: string;
}

export class ModelRouter {
    /**
     * Determine the best model for the given prompt context.
     * Uses the default routing model (typically fast, like llama3.2) to evaluate
     * the task and pick one of the available models.
     */
    static async route(
        prompt: string,
        provider: ModelProvider,
        config: StackOwlConfig,
        failureCount: number = 0
    ): Promise<RouteDecision> {
        // If the local model is repeatedly failing tool execution, force cross-provider fallback
        if (failureCount >= 2 && config.smartRouting?.fallbackModel) {
            console.warn(`[ModelRouter] Local model repeated failure detected (${failureCount}x). Routing to fallback: ${config.smartRouting.fallbackProvider} / ${config.smartRouting.fallbackModel}`);
            return {
                modelName: config.smartRouting.fallbackModel,
                providerName: config.smartRouting.fallbackProvider
            };
        }

        // If smart routing is disabled or misconfigured, fallback to default immediately
        if (!config.smartRouting?.enabled || config.smartRouting.availableModels.length === 0) {
            return { modelName: config.defaultModel };
        }

        const models = config.smartRouting.availableModels;

        // If there's only one model in the roster, just use it
        if (models.length === 1) {
            return { modelName: models[0].name };
        }

        const modelListDesc = models
            .map(m => `- **${m.name}**: ${m.description}`)
            .join('\n');

        const sysPrompt = `You are the StackOwl Engine Router.
Your job is to read the user's upcoming task and decide WHICH AI MODEL is best suited to handle it, based purely on their descriptions.

Available Models:
${modelListDesc}

Output strictly and EXACTLY the exact name of the selected model. No explanation, no markdown ticks, just the model name.`;

        try {
            // Ping the provider using the default (fast) model as the evaluator
            const res = await provider.chat(
                [
                    { role: 'system', content: sysPrompt },
                    { role: 'user', content: prompt }
                ],
                config.defaultModel, // The routing logic always uses the default fast model
                { temperature: 0.1, maxTokens: 50 } // Low temp for deterministic routing
            );

            const selection = res.content.trim();

            // Verify the selected model actually exists in our roster
            const selectedModel = models.find(m => m.name === selection);
            if (selectedModel) {
                return { modelName: selectedModel.name };
            } else {
                // If it hallucinates, fallback gracefully
                console.warn(`[ModelRouter] Hallucinated model selection: "${selection}". Falling back to default.`);
                return { modelName: config.defaultModel };
            }
        } catch (error) {
            console.error('[ModelRouter] Routing failed. Error:', error);
            return { modelName: config.defaultModel }; // Safe fallback
        }
    }
}

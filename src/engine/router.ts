/**
 * StackOwl — Dynamic Model Router
 *
 * Intercepts a prompt and evaluates the task complexity, then routes it
 * to the most appropriate AI model from the configured `smartRouting` roster.
 */

import type { ModelProvider } from '../providers/base.js';
import type { StackOwlConfig } from '../config/loader.js';

export class ModelRouter {
    /**
     * Determine the best model for the given prompt context.
     * Uses the default routing model (typically fast, like llama3.2) to evaluate
     * the task and pick one of the available models.
     */
    static async route(
        prompt: string,
        provider: ModelProvider,
        config: StackOwlConfig
    ): Promise<string> {
        // If smart routing is disabled or misconfigured, fallback to default immediately
        if (!config.smartRouting?.enabled || config.smartRouting.availableModels.length === 0) {
            return config.defaultModel;
        }

        const models = config.smartRouting.availableModels;

        // If there's only one model in the roster, just use it
        if (models.length === 1) {
            return models[0].name;
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
                return selectedModel.name;
            } else {
                // If it hallucinates, fallback gracefully
                console.warn(`[ModelRouter] Hallucinated model selection: "${selection}". Falling back to default.`);
                return config.defaultModel;
            }
        } catch (error) {
            console.error('[ModelRouter] Routing failed. Error:', error);
            return config.defaultModel; // Safe fallback
        }
    }
}

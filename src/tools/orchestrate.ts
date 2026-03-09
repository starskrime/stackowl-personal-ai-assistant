import type { ToolImplementation, ToolContext } from './registry.js';
import { OwlEngine } from '../engine/runtime.js';
import { log } from '../logger.js';

export const OrchestrateTasksTool: ToolImplementation = {
    definition: {
        name: 'orchestrate_tasks',
        description: 'Spawn asynchronous background sub-owls to execute multiple unrelated complex tasks in parallel. Use this when the user asks you to do several slow or complex things at once, so you do not block the main thread.',
        parameters: {
            type: 'object',
            properties: {
                tasks: {
                    type: 'array',
                    description: 'Array of specific, detailed instructions for each background agent to execute.',
                } as any, // Cast to any because base ToolDefinition is too strict for array types
            },
            required: ['tasks'],
        },
    },

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const tasks = args['tasks'] as string[];
        if (!Array.isArray(tasks) || tasks.length === 0) {
            throw new Error('Must provide an array of at least one task string.');
        }

        const eCtx = context.engineContext;
        if (!eCtx) {
            throw new Error('EngineContext is required to spawn sub-owls.');
        }

        log.engine.info(`Spawning ${tasks.length} background sub-owls for parallel execution.`);

        // Fire and forget the background tasks so we don't block the primary agent
        Promise.allSettled(tasks.map(async (taskText, index) => {
            const laneId = `Lane-${index + 1}`;
            try {
                if (eCtx.onProgress) {
                    await eCtx.onProgress(`🚀 **[Swarm]** ${laneId} launched: "${taskText}"`);
                }

                const engine = new OwlEngine();
                // Pass a deeply cloned or isolated session history to prevent bleeding
                const subContext = {
                    ...eCtx,
                    sessionHistory: [],
                    // Do not pass the onProgress callback to the sub-agent directly if we want to avoid UI spam,
                    // but for now we'll let it log so the user sees them working.
                };

                const backgroundPrompt = `[SYSTEM DIRECTIVE: You are an asynchronous background Sub-Owl spawned by the Primary Owl to execute a specific lane task. Do NOT ask clarifying questions. Execute this task to completion using your tools. Your final output will be shown directly to the user.]\n\nYOUR TASK: ${taskText}`;

                const result = await engine.run(backgroundPrompt, subContext);

                if (eCtx.onProgress) {
                    await eCtx.onProgress(`✅ **[Swarm]** ${laneId} finished!\n\n${result.content}`);
                }
            } catch (error) {
                log.engine.error(`${laneId} failed:`, error);
                if (eCtx.onProgress) {
                    await eCtx.onProgress(`❌ **[Swarm]** ${laneId} failed: ${error instanceof Error ? error.message : String(error)}`);
                }
            }
        }));

        return `Successfully spawned ${tasks.length} background sub-owls. Tell the user you have delegated their requests to your Swarm and they are running asynchronously.`;
    },
};

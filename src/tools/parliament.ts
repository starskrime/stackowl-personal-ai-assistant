import type { ToolImplementation, ToolContext, ToolDefinition } from './registry.js';
import { ParliamentOrchestrator } from '../parliament/orchestrator.js';

export class SummonParliamentTool implements ToolImplementation {
    definition = {
        name: 'summon_parliament',
        description: 'Summon multiple specialist AI agents for a structured debate on a complex topic. Use ONLY for high-stakes decisions requiring multiple perspectives (architecture reviews, strategy decisions, complex tradeoffs). NOT for simple questions, web searches, or tasks you can handle alone. Runs 3 debate rounds — slow and expensive.',
        parameters: {
            type: 'object',
            properties: {
                topic: {
                    type: 'string',
                    description: 'The specific question, problem, or topic the Parliament should debate. Be as detailed as possible to give the agents context.'
                }
            },
            required: ['topic']
        }
    } as unknown as ToolDefinition;

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const topic = args.topic as string;
        if (!topic) {
            throw new Error('Missing parameter: topic');
        }

        if (!context.engineContext) {
            throw new Error('Tool execution failed: engineContext is not available. Parliament requires the engine context to run.');
        }

        const { provider, config, pelletStore, owlRegistry } = context.engineContext;

        if (!provider || !config || !pelletStore || !owlRegistry) {
            throw new Error('Tool execution failed: Missing required engine components (provider, config, pelletStore, or owlRegistry).');
        }

        // Gather participants automatically from the registry
        // We pick top ones to ensure a good debate, falling back to all available if few exist
        const preferredScns = ['Noctua', 'Archimedes', 'Scrooge', 'Socrates'];
        const participants = preferredScns
            .map(name => owlRegistry.get(name))
            .filter(Boolean) as any[];

        if (participants.length < 2) {
            const allOwls = owlRegistry.listOwls();
            if (allOwls.length < 2) {
                throw new Error('Parliament requires at least 2 owls to exist in the registry (check the workspace/owls directory).');
            }
            participants.length = 0;
            participants.push(...allOwls.slice(0, 4));
        }

        try {
            const orchestrator = new ParliamentOrchestrator(provider, config, pelletStore);

            // Convene the parliament silently in the background
            const session = await orchestrator.convene({
                topic,
                participants,
                contextMessages: context.engineContext.sessionHistory || [],
            });

            // Return the formatted markdown transcript so the calling Owl can read it and synthesize an answer to the user
            return orchestrator.formatSessionMarkdown(session);
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            throw new Error(`Parliament session failed: ${msg}`);
        }
    }
}

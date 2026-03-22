import type { ToolImplementation, ToolContext } from './registry.js';

/**
 * Growth Journal Tool — generates and retrieves periodic growth journals.
 */
export class GrowthJournalTool implements ToolImplementation {
  definition = {
    name: 'growth_journal',
    description:
      'Generate or view growth journals that track your learning progress over time. ' +
      'Journals are auto-generated from pellets, sessions, and DNA evolution data.',
    parameters: {
      type: 'object' as const,
      properties: {
        action: {
          type: 'string',
          description: 'Action: "generate" (create new), "list" (all journals), "view" (specific), "search" (by keyword)',
        },
        period: {
          type: 'string',
          description: 'For generate: "weekly" or "monthly"',
        },
        id: {
          type: 'string',
          description: 'For view: journal ID',
        },
        query: {
          type: 'string',
          description: 'For search: keyword to search journals',
        },
      },
      required: ['action'],
    },
  };

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const action = args.action as string;
    const journal = context.engineContext?.journalGenerator;

    if (!journal) {
      return 'Growth journal is not available.';
    }

    try {
      switch (action) {
        case 'generate': {
          const period = (args.period as 'weekly' | 'monthly') || 'weekly';
          const entry = await journal.generate(period);
          return `**${period.charAt(0).toUpperCase() + period.slice(1)} Growth Journal**\n\n${entry.narrative}\n\n` +
            `---\n` +
            `Pellets: ${entry.sections.metrics.pelletsCreated} | Sessions: ${entry.sections.metrics.sessionsCount} | ` +
            `Topics: ${entry.sections.metrics.topicsExplored.join(', ') || 'none'}`;
        }

        case 'list': {
          const entries = await journal.list();
          if (entries.length === 0) return 'No journal entries yet. Use action "generate" to create one.';
          return '**Growth Journals:**\n' +
            entries.map(e => `- **${e.id}** (${e.period}) — ${new Date(e.generatedAt).toLocaleDateString()}`).join('\n');
        }

        case 'view': {
          const id = args.id as string;
          if (!id) return 'Error: id is required for view action.';
          const entry = await journal.get(id);
          if (!entry) return `Journal "${id}" not found.`;
          return `**${entry.period} Journal** (${new Date(entry.startDate).toLocaleDateString()} — ${new Date(entry.endDate).toLocaleDateString()})\n\n${entry.narrative}`;
        }

        case 'search': {
          const query = args.query as string;
          if (!query) return 'Error: query is required for search action.';
          const results = await journal.search(query);
          if (results.length === 0) return `No journals matching "${query}".`;
          return `**Search results for "${query}":**\n` +
            results.map(e => `- **${e.id}**: ${e.narrative.slice(0, 100)}...`).join('\n');
        }

        default:
          return 'Unknown action. Use "generate", "list", "view", or "search".';
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Growth journal operation failed: ${msg}`;
    }
  }
}

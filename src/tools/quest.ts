import type { ToolImplementation, ToolContext } from './registry.js';

/**
 * Quest Tool — create and manage gamified learning quests.
 */
export class QuestTool implements ToolImplementation {
  definition = {
    name: 'quest',
    description:
      'Create and manage learning quests — structured journeys with milestones. ' +
      'Use when the user wants to learn a topic systematically or track learning progress.',
    parameters: {
      type: 'object' as const,
      properties: {
        action: {
          type: 'string',
          description: 'Action: "create", "list", "progress", "pause", "abandon"',
        },
        topic: {
          type: 'string',
          description: 'For create: the topic to build a quest around',
        },
        difficulty: {
          type: 'string',
          description: 'For create: "beginner", "intermediate" (default), "advanced", or "expert"',
        },
        questId: {
          type: 'string',
          description: 'For progress/pause/abandon: the quest ID',
        },
      },
      required: ['action'],
    },
  };

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const action = args.action as string;
    const questManager = context.engineContext?.questManager;

    if (!questManager) {
      return 'Quest system is not available.';
    }

    try {
      switch (action) {
        case 'create': {
          const topic = args.topic as string;
          if (!topic) return 'Error: topic is required for create action.';
          const difficulty = (args.difficulty as any) || 'intermediate';
          const quest = await questManager.create(topic, difficulty);
          let result = `**Quest Created: ${quest.title}**\n\n${quest.description}\n\n**Milestones:**\n`;
          for (const m of quest.milestones) {
            result += `${m.order + 1}. ${m.title}\n   ${m.description}\n`;
          }
          return result;
        }

        case 'list': {
          const quests = await questManager.list();
          if (quests.length === 0) return 'No quests yet. Use action "create" to start one.';
          return '**Your Quests:**\n' +
            quests.map(q => {
              const done = q.milestones.filter(m => m.completed).length;
              return `- **${q.title}** [${q.status}] — ${done}/${q.milestones.length} milestones (ID: ${q.id})`;
            }).join('\n');
        }

        case 'progress': {
          const qid = args.questId as string;
          if (!qid) return 'Error: questId is required.';
          const progress = await questManager.progress(qid);
          if (!progress) return `Quest "${qid}" not found.`;
          let result = `**${progress.questTitle}** — ${progress.percentComplete}% complete\n`;
          result += `Milestones: ${progress.completedMilestones}/${progress.totalMilestones}\n`;
          if (progress.nextMilestone) {
            result += `\n**Next:** ${progress.nextMilestone.title}\n${progress.nextMilestone.description}\n`;
          }
          if (progress.suggestion) {
            result += `\n**Suggestion:** ${progress.suggestion}`;
          }
          return result;
        }

        case 'pause': {
          const id = args.questId as string;
          if (!id) return 'Error: questId is required.';
          const q = await questManager.updateStatus(id, 'paused');
          return q ? `Quest "${q.title}" paused.` : `Quest "${id}" not found.`;
        }

        case 'abandon': {
          const id = args.questId as string;
          if (!id) return 'Error: questId is required.';
          const q = await questManager.updateStatus(id, 'abandoned');
          return q ? `Quest "${q.title}" abandoned.` : `Quest "${id}" not found.`;
        }

        default:
          return 'Unknown action. Use "create", "list", "progress", "pause", or "abandon".';
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Quest operation failed: ${msg}`;
    }
  }
}

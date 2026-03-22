import type { ToolImplementation, ToolContext } from './registry.js';

/**
 * Time Capsule Tool — create and manage messages to your future self.
 */
export class TimeCapsuleTool implements ToolImplementation {
  definition = {
    name: 'time_capsule',
    description:
      'Create time capsules — messages to your future self that are delivered on a specific date ' +
      'or when a condition is met. Use for reminders, goal check-ins, or reflective messages.',
    parameters: {
      type: 'object' as const,
      properties: {
        action: {
          type: 'string',
          description: 'Action: "create", "list", "open" (deliver early)',
        },
        message: {
          type: 'string',
          description: 'For create: the message to your future self',
        },
        triggerType: {
          type: 'string',
          description: 'For create: "date" (deliver on specific date) or "condition" (deliver when condition is met)',
        },
        triggerValue: {
          type: 'string',
          description: 'For create: ISO date string (for date trigger) or natural language condition',
        },
        capsuleId: {
          type: 'string',
          description: 'For open: capsule ID to open early',
        },
      },
      required: ['action'],
    },
  };

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const action = args.action as string;
    const capsuleManager = context.engineContext?.capsuleManager;

    if (!capsuleManager) {
      return 'Time capsule system is not available.';
    }

    try {
      switch (action) {
        case 'create': {
          const message = args.message as string;
          if (!message) return 'Error: message is required.';
          const triggerType = (args.triggerType as 'date' | 'condition') || 'date';
          const triggerValue = args.triggerValue as string;
          if (!triggerValue) return 'Error: triggerValue is required (date or condition).';

          const trigger = {
            type: triggerType,
            ...(triggerType === 'date' ? { date: triggerValue } : { condition: triggerValue }),
          };

          const capsule = await capsuleManager.create(message, trigger);
          const deliveryInfo = triggerType === 'date'
            ? `on ${new Date(triggerValue).toLocaleDateString()}`
            : `when: "${triggerValue}"`;
          return `**Time Capsule Sealed**\n\nID: ${capsule.id}\nDelivery: ${deliveryInfo}\n\nYour message has been sealed and will be delivered ${deliveryInfo}.`;
        }

        case 'list': {
          const capsules = await capsuleManager.list();
          if (capsules.length === 0) return 'No time capsules. Use action "create" to seal one.';
          return '**Time Capsules:**\n' +
            capsules.map(c => {
              const status = c.status === 'sealed' ? '\uD83D\uDD12' : '\uD83D\uDCEC';
              const trigger = c.trigger.type === 'date'
                ? `Date: ${new Date(c.trigger.date!).toLocaleDateString()}`
                : `Condition: ${c.trigger.condition}`;
              return `${status} **${c.id}** [${c.status}] — ${trigger} (created ${new Date(c.createdAt).toLocaleDateString()})`;
            }).join('\n');
        }

        case 'open': {
          const id = args.capsuleId as string;
          if (!id) return 'Error: capsuleId is required.';
          const capsule = await capsuleManager.open(id);
          if (!capsule) return `Capsule "${id}" not found.`;
          return capsuleManager.formatDelivery(capsule);
        }

        default:
          return 'Unknown action. Use "create", "list", or "open".';
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Time capsule operation failed: ${msg}`;
    }
  }
}

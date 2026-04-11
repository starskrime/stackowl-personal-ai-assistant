import type {
  ToolImplementation,
  ToolContext,
  ToolDefinition,
} from "./registry.js";
import { ParliamentOrchestrator } from "../parliament/orchestrator.js";
import type { ParliamentCallbacks } from "../parliament/protocol.js";

export class SummonParliamentTool implements ToolImplementation {
  definition = {
    name: "summon_parliament",
    description:
      "Summon multiple specialist AI agents for a structured debate on a complex topic. Use ONLY for high-stakes decisions requiring multiple perspectives (architecture reviews, strategy decisions, complex tradeoffs). NOT for simple questions, web searches, or tasks you can handle alone. Runs 3 debate rounds — slow and expensive.",
    parameters: {
      type: "object",
      properties: {
        topic: {
          type: "string",
          description:
            "The specific question, problem, or topic the Parliament should debate. Be as detailed as possible to give the agents context.",
        },
      },
      required: ["topic"],
    },
  } as unknown as ToolDefinition;

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const topic = args.topic as string;
    if (!topic) {
      throw new Error("Missing parameter: topic");
    }

    if (!context.engineContext) {
      throw new Error(
        "Tool execution failed: engineContext is not available. Parliament requires the engine context to run.",
      );
    }

    const { provider, config, pelletStore, owlRegistry } =
      context.engineContext;

    if (!provider || !config || !pelletStore || !owlRegistry) {
      throw new Error(
        "Tool execution failed: Missing required engine components (provider, config, pelletStore, or owlRegistry).",
      );
    }

    // Gather participants automatically from the registry
    const preferredScns = ["Noctua", "Archimedes", "Scrooge", "Socrates"];
    const participants = preferredScns
      .map((name) => owlRegistry.get(name))
      .filter(Boolean) as any[];

    if (participants.length < 2) {
      const allOwls = owlRegistry.listOwls();
      if (allOwls.length < 2) {
        throw new Error(
          "Parliament requires at least 2 owls to exist in the registry (check the workspace/owls directory).",
        );
      }
      participants.length = 0;
      participants.push(...allOwls.slice(0, 4));
    }

    // Build streaming callbacks from engine context onProgress
    const onProgress = context.engineContext.onProgress;
    const callbacks: ParliamentCallbacks | undefined = onProgress
      ? {
          onRoundStart: async (round, phase) => {
            const labels: Record<string, string> = {
              round1_position: "📢 Round 1: Initial Positions",
              round2_challenge: "⚔️ Round 2: Cross-Examination",
              round3_synthesis: "🔮 Round 3: Synthesis",
            };
            await onProgress(
              `\n🏛️ **Parliament** — ${labels[phase] || `Round ${round}`}`,
            );
          },
          onPositionReady: async (position) => {
            await onProgress(
              `${position.owlEmoji} **${position.owlName}** [${position.position}]: ${position.argument}`,
            );
          },
          onChallengeReady: async (challenge) => {
            await onProgress(
              `⚔️ **${challenge.owlName}** challenges ${challenge.targetOwl}: ${challenge.challengeContent}`,
            );
          },
          onSynthesisReady: async (synthesis, verdict) => {
            await onProgress(`📋 **Verdict: [${verdict}]**\n${synthesis}`);
          },
        }
      : undefined;

    try {
      const orchestrator = new ParliamentOrchestrator(
        provider,
        config,
        pelletStore,
        context.engineContext.toolRegistry,
        (context.engineContext as any).db,
      );

      const session = await orchestrator.convene({
        topic,
        participants,
        contextMessages: context.engineContext.sessionHistory || [],
        callbacks,
      });

      return orchestrator.formatSessionMarkdown(session);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      throw new Error(`Parliament session failed: ${msg}`);
    }
  }
}

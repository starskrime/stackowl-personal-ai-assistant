import type {
  ToolImplementation,
  ToolContext,
  ToolDefinition,
} from "./registry.js";
import { ParliamentOrchestrator } from "../parliament/orchestrator.js";
import type { ParliamentCallbacks, ParliamentSession } from "../parliament/protocol.js";
import { createDefaultDNA } from "../owls/persona.js";
import type { OwlInstance } from "../owls/persona.js";
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";

export function buildBmadParticipant(spec: SpecializedOwlSpec): OwlInstance {
  return {
    persona: {
      name: spec.name,
      type: spec.type,
      emoji: spec.emoji,
      challengeLevel: spec.personality.challengeLevel,
      specialties: spec.expertise,
      traits: [],
      systemPrompt: [spec.role, spec.additionalPrompt].filter(Boolean).join(". "),
      sourcePath: "",
    },
    dna: createDefaultDNA(spec.name, spec.personality.challengeLevel),
    specialistPrompt: spec.additionalPrompt || undefined,
  } as OwlInstance;
}

export function buildAuditSummary(session: ParliamentSession): string {
  const topic = session.config.topic.slice(0, 100);

  const s1 = `Parliament debated: "${topic}."`;

  const forPos = session.positions.find((p) => p.position === "FOR" || p.position === "CONDITIONAL");
  const againstPos = session.positions.find((p) => p.position === "AGAINST");
  let s2: string;
  if (forPos && againstPos) {
    s2 = `${forPos.owlEmoji} ${forPos.owlName} argued FOR; ${againstPos.owlEmoji} ${againstPos.owlName} argued AGAINST.`;
  } else if (session.positions.length >= 2) {
    const [a, b] = session.positions;
    s2 = `${a.owlEmoji} ${a.owlName} said [${a.position}]; ${b.owlEmoji} ${b.owlName} said [${b.position}].`;
  } else {
    s2 = `${session.positions.length} position(s) presented.`;
  }

  const verdict = session.verdict ?? "PENDING";
  const citationNote = session.agentCitations
    ? ` (Cited: ${session.agentCitations.slice(0, 120)})`
    : "";
  const s3 = `Verdict: **${verdict}**${citationNote}.`;

  return [s1, s2, s3].join(" ");
}

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
    capabilities: ["multi_agent_debate", "knowledge_synthesis"],
    executionPolicy: { timeoutMs: 600_000, maxRetries: 0 },
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

    const { provider, config } = context.engineContext;

    if (!provider || !config) {
      throw new Error(
        "Tool execution failed: Missing required engine components (provider or config).",
      );
    }

    // Parliament uses BMAD agents exclusively as participants
    const specializedRegistry = context.engineContext.specializedRegistry;
    const bmadAgents = specializedRegistry
      ? specializedRegistry.listAll().filter((s: SpecializedOwlSpec) => s.source === "bmad")
      : [];
    if (bmadAgents.length < 2) {
      throw new Error(
        "Parliament requires at least 2 BMAD agents. Ensure bmad-method is installed and agents loaded.",
      );
    }
    const participants = bmadAgents.slice(0, 4).map(buildBmadParticipant);

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
        context.engineContext.toolRegistry,
        (context.engineContext as any).db,
      );

      const session = await orchestrator.convene({
        topic,
        participants,
        contextMessages: context.engineContext.sessionHistory || [],
        callbacks,
      });

      const formatted = orchestrator.formatSessionMarkdown(session);

      if (onProgress) {
        const summary = buildAuditSummary(session);
        await onProgress(`\n📋 **Parliament Audit Summary**\n${summary}`);
      }

      return formatted;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      throw new Error(`Parliament session failed: ${msg}`);
    }
  }
}

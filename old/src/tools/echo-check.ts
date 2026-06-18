import type { ToolImplementation, ToolContext } from "./registry.js";

/**
 * Echo Chamber Tool — analyzes conversation history for cognitive biases
 * and generates calibrated challenges.
 */
export class EchoCheckTool implements ToolImplementation {
  definition = {
    name: "echo_check",
    description:
      "Analyze your recent conversations for cognitive biases and echo chamber patterns. " +
      "Use when the user wants honest feedback about their thinking patterns, " +
      "or when you detect potential bias. Can also generate a challenge message.",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          description:
            'Action: "analyze" (full analysis), "challenge" (generate a challenge), "status" (last analysis)',
        },
        intensity: {
          type: "string",
          description:
            'Challenge intensity: "gentle", "balanced" (default), or "relentless"',
        },
      },
      required: ["action"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = args.action as string;
    const detector = context.engineContext?.echoChamberDetector;

    if (!detector) {
      return "Echo chamber detection is not available.";
    }

    try {
      switch (action) {
        case "analyze": {
          const analysis = await detector.analyze();
          if (analysis.detections.length === 0) {
            return `**Echo Chamber Analysis** (${analysis.sessionCount} sessions)\n\n${analysis.overallAssessment}`;
          }
          let result = `**Echo Chamber Analysis** (${analysis.sessionCount} sessions)\n\n`;
          result += `${analysis.overallAssessment}\n\n`;
          result += "**Detected Patterns:**\n";
          for (const d of analysis.detections) {
            result += `- **${d.bias.replace(/_/g, " ")}** (${(d.confidence * 100).toFixed(0)}% confidence): ${d.evidence}\n`;
          }
          return result;
        }

        case "challenge": {
          const intensity = (args.intensity as any) || undefined;
          const challenge = await detector.generateChallenge(intensity);
          return (
            challenge ||
            "No bias patterns detected to challenge. Your thinking appears balanced."
          );
        }

        case "status": {
          const last = detector.getLastAnalysis();
          if (!last)
            return 'No analysis has been run yet. Use action "analyze" first.';
          return `Last analyzed: ${last.analyzedAt}\nSessions reviewed: ${last.sessionCount}\nPatterns found: ${last.detections.length}`;
        }

        default:
          return 'Unknown action. Use "analyze", "challenge", or "status".';
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Echo chamber analysis failed: ${msg}`;
    }
  }
}

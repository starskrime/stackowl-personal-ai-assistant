import type { IFeatureCommandHandler, FeatureCommandContext } from "../feature-command-router.js";
import type { GatewayResponse } from "../types.js";
import { log } from "../../logger.js";

export class KnowledgeCommandHandler implements IFeatureCommandHandler {
  readonly commands = ["/knowledge", "/fact", "/pellet", "/pellets"] as const;

  async handle(cmd: string, _args: string[], ctx: FeatureCommandContext): Promise<GatewayResponse | null> {
    log.gateway.debug("KnowledgeCommandHandler.handle: entry", { cmd, argCount: _args.length });
    const owl = ctx.gatewayCtx.owl;
    const text = ctx.message.text.trim();
    const mkResp = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
    });

    // /knowledge — show knowledge graph stats
    if (text.toLowerCase() === "/knowledge" && ctx.gatewayCtx.knowledgeGraph) {
      log.gateway.debug("KnowledgeCommandHandler.handle: knowledge stats", { cmd });
      const stats = ctx.gatewayCtx.knowledgeGraph.getStats();
      const topNodes = stats.topNodes
        .slice(0, 5)
        .map((n) => `  • **${n.title}** (accessed ${n.accessCount}x)`)
        .join("\n");
      const result = mkResp(
        `**🧠 Knowledge Graph**\n\n` +
          `Nodes: ${stats.totalNodes}\n` +
          `Edges: ${stats.totalEdges}\n` +
          `Domains: ${stats.domains.join(", ") || "none"}\n` +
          `Avg confidence: ${(stats.avgConfidence * 100).toFixed(0)}%\n\n` +
          `**Most accessed:**\n${topNodes || "  (none yet)"}`,
      );
      log.gateway.debug("KnowledgeCommandHandler.handle: exit", { cmd });
      return result;
    }

    log.gateway.debug("KnowledgeCommandHandler.handle: exit — no match", { cmd });
    return null;
  }
}

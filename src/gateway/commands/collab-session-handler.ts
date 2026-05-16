import type { IFeatureCommandHandler, FeatureCommandContext } from "../feature-command-router.js";
import type { GatewayResponse } from "../types.js";
import { log } from "../../logger.js";

export class CollabSessionCommandHandler implements IFeatureCommandHandler {
  readonly commands = ["/collab", "/session"] as const;

  async handle(cmd: string, _args: string[], ctx: FeatureCommandContext): Promise<GatewayResponse | null> {
    log.gateway.debug("CollabSessionCommandHandler.handle: entry", { cmd, argCount: _args.length });
    const owl = ctx.gatewayCtx.owl;
    const text = ctx.message.text.trim();
    const mkResp = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
    });

    // /collab create <name> — create a collaborative session
    const collabCreate = text.match(/^\/collab\s+create\s+(.+)$/i);
    if (collabCreate && ctx.gatewayCtx.collabManager) {
      log.gateway.debug("CollabSessionCommandHandler.handle: collab create", { cmd });
      try {
        const session = ctx.gatewayCtx.collabManager.createSession(
          collabCreate[1],
          owl.persona.name,
          {
            userId: ctx.message.userId,
            displayName: ctx.message.userId,
            channelId: ctx.message.channelId,
          },
        );
        const result = mkResp(
          `👥 **Collaborative session created!**\n\n` +
            `Name: **${session.name}**\n` +
            `Session ID: \`${session.id.slice(0, 8)}\`\n` +
            `Others can join with: \`/collab join ${session.id.slice(0, 8)}\``,
        );
        log.gateway.debug("CollabSessionCommandHandler.handle: exit", { cmd });
        return result;
      } catch (err) {
        log.gateway.error("CollabSessionCommandHandler.handle: collab create failed", err as Error, { cmd });
        return mkResp(
          `Failed to create session: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // /collab list — list active collab sessions
    if (text.toLowerCase() === "/collab list" && ctx.gatewayCtx.collabManager) {
      log.gateway.debug("CollabSessionCommandHandler.handle: collab list", { cmd });
      const sessions = ctx.gatewayCtx.collabManager.listSessions();
      if (sessions.length === 0) {
        log.gateway.debug("CollabSessionCommandHandler.handle: exit — no sessions", { cmd });
        return mkResp("No active collaborative sessions.");
      }
      const list = sessions
        .map(
          (s) =>
            `  • **${s.name}** (\`${s.id.slice(0, 8)}\`) — ${s.participants.length} participants, ${s.messages.length} messages`,
        )
        .join("\n");
      const result = mkResp(`**Active Collaborative Sessions:**\n${list}`);
      log.gateway.debug("CollabSessionCommandHandler.handle: exit", { cmd });
      return result;
    }

    log.gateway.debug("CollabSessionCommandHandler.handle: exit — no match", { cmd });
    return null;
  }
}

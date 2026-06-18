import type { IFeatureCommandHandler, FeatureCommandContext } from "../feature-command-router.js";
import type { GatewayResponse } from "../types.js";
import { log } from "../../logger.js";

export class TrustTimelineCommandHandler implements IFeatureCommandHandler {
  readonly commands = ["/trust", "/timeline", "/fork"] as const;

  async handle(cmd: string, _args: string[], ctx: FeatureCommandContext): Promise<GatewayResponse | null> {
    log.gateway.debug("TrustTimelineCommandHandler.handle: entry", { cmd, argCount: _args.length });
    const owl = ctx.gatewayCtx.owl;
    const text = ctx.message.text.trim();
    const mkResp = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
    });

    // /trust — show trust chain status
    if (text.toLowerCase() === "/trust" && ctx.gatewayCtx.trustChain) {
      log.gateway.debug("TrustTimelineCommandHandler.handle: trust chain status", { cmd });
      const result = mkResp(ctx.gatewayCtx.trustChain.formatStatus());
      log.gateway.debug("TrustTimelineCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /timeline — show conversation timeline
    if (text.toLowerCase() === "/timeline" && ctx.gatewayCtx.timelineManager) {
      log.gateway.debug("TrustTimelineCommandHandler.handle: timeline", { cmd });
      const timeline = ctx.gatewayCtx.timelineManager.getTimeline(ctx.message.sessionId);
      if (!timeline) {
        log.gateway.debug("TrustTimelineCommandHandler.handle: exit — no timeline", { cmd });
        return mkResp("No timeline data for this session yet.");
      }
      const snapshotList = timeline.snapshots
        .map(
          (s) =>
            `  • [${s.id.slice(0, 8)}] ${s.metadata.snapshotAt} — ${s.messageIndex} messages${s.metadata.description ? ` (${s.metadata.description})` : ""}`,
        )
        .join("\n");
      const forkList =
        timeline.forks.length > 0
          ? "\n\n**Forks:**\n" +
            timeline.forks
              .map(
                (f) =>
                  `  • ${f.createdAt} — forked at message ${f.forkIndex}${f.forkReason ? ` (${f.forkReason})` : ""}`,
              )
              .join("\n")
          : "";
      const result = mkResp(
        `**Timeline** (${timeline.totalMessages} messages)\n\n**Snapshots:**\n${snapshotList}${forkList}`,
      );
      log.gateway.debug("TrustTimelineCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /fork [reason] — fork conversation from current point
    if (text.toLowerCase().startsWith("/fork") && ctx.gatewayCtx.timelineManager) {
      log.gateway.debug("TrustTimelineCommandHandler.handle: fork", { cmd });
      const reason = text.slice(5).trim() || undefined;
      const session = await ctx.sessionManager.getOrCreate(ctx.message);
      const snapshot = ctx.gatewayCtx.timelineManager.createSnapshot(
        ctx.message.sessionId,
        session.messages,
        owl.persona.name,
        "Pre-fork snapshot",
      );
      const newSessionId = `${ctx.message.sessionId}:fork:${Date.now()}`;
      const fork = ctx.gatewayCtx.timelineManager.fork(
        snapshot.id,
        newSessionId,
        reason,
      );
      await ctx.gatewayCtx.timelineManager.save();
      const result = mkResp(
        `🔀 **Conversation forked!**\n\n` +
          `Fork ID: \`${fork.id.slice(0, 8)}\`\n` +
          `Forked at message: ${fork.forkIndex}\n` +
          (reason ? `Reason: ${reason}\n` : "") +
          `New session: \`${newSessionId}\`\n\n` +
          `You can continue here or switch to the fork.`,
      );
      log.gateway.debug("TrustTimelineCommandHandler.handle: exit", { cmd });
      return result;
    }

    log.gateway.debug("TrustTimelineCommandHandler.handle: exit — no match", { cmd });
    return null;
  }
}

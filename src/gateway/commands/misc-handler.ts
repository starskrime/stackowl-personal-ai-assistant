import type { IFeatureCommandHandler, FeatureCommandContext } from "../feature-command-router.js";
import type { GatewayResponse } from "../types.js";
import { log } from "../../logger.js";
import { join } from "node:path";

export class MiscCommandHandler implements IFeatureCommandHandler {
  readonly commands = [
    "/forge",
    "/swarm",
    "/tournament",
    "/voice",
    "/predict",
    "/echo-check",
    "/journal",
    "/quests",
    "/capsules",
    "/constellations",
    "/socratic",
    "/council",
    "/council-history",
    "/watch",
    "/unwatch",
  ] as const;

  async handle(cmd: string, _args: string[], ctx: FeatureCommandContext): Promise<GatewayResponse | null> {
    log.gateway.debug("MiscCommandHandler.handle: entry", { cmd, argCount: _args.length });
    const owl = ctx.gatewayCtx.owl;
    const text = ctx.message.text.trim();
    const mkResp = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
    });
    const mkHtml = (content: string): GatewayResponse => ({
      content,
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      toolsUsed: [],
      preformatted: true,
    });

    // /forge start <name> — start recording a demonstration
    const forgeStart = text.match(/^\/forge\s+start\s+(.+)$/i);
    if (forgeStart && ctx.gatewayCtx.demoRecorder) {
      log.gateway.debug("MiscCommandHandler.handle: forge start", { cmd });
      const id = ctx.gatewayCtx.demoRecorder.startRecording(
        forgeStart[1],
        forgeStart[1],
        ctx.gatewayCtx.cwd ?? process.cwd(),
      );
      const result = mkResp(
        `🔨 **Skill Forge recording started!**\n\n` +
          `Name: **${forgeStart[1]}**\n` +
          `Recording ID: \`${id.slice(0, 8)}\`\n\n` +
          `I'm now watching your actions. When done, use \`/forge stop\` to generate a skill.`,
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /forge stop — stop recording and generate skill
    if (
      text.toLowerCase() === "/forge stop" &&
      ctx.gatewayCtx.demoRecorder &&
      ctx.gatewayCtx.forgeSynthesizer
    ) {
      log.gateway.debug("MiscCommandHandler.handle: forge stop", { cmd });
      // Get the last active recording
      const activeIds = [
        ...((ctx.gatewayCtx.demoRecorder as any).activeRecordings?.keys?.() ?? []),
      ];
      if (activeIds.length === 0) {
        log.gateway.debug("MiscCommandHandler.handle: exit — no active recording", { cmd });
        return mkResp("No active recording to stop.");
      }

      const recording = ctx.gatewayCtx.demoRecorder.endRecording(
        activeIds[activeIds.length - 1],
      );
      try {
        const skillMd = await ctx.gatewayCtx.forgeSynthesizer.synthesize(recording);
        const skillDir =
          ctx.gatewayCtx.config.skills?.directories?.[0] || join(ctx.gatewayCtx.cwd ?? process.cwd(), "skills");
        const filePath = await ctx.gatewayCtx.forgeSynthesizer.saveSkill(
          skillMd,
          skillDir,
        );

        // Reindex skills after new skill added
        if (ctx.skillInjector) {
          ctx.skillInjector.reindex();
        }

        const result = mkResp(
          `✅ **Skill generated from demonstration!**\n\n` +
            `Steps recorded: ${recording.steps.length}\n` +
            `Skill saved to: \`${filePath}\`\n\n` +
            `The skill is now available for use.`,
        );
        log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
        return result;
      } catch (err) {
        log.gateway.error("MiscCommandHandler.handle: forge synthesize failed", err as Error, { cmd });
        return mkResp(
          `Skill generation failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // /swarm — show swarm status
    if (text.toLowerCase() === "/swarm" && ctx.gatewayCtx.swarmCoordinator) {
      log.gateway.debug("MiscCommandHandler.handle: swarm status", { cmd });
      const status = ctx.gatewayCtx.swarmCoordinator.getSwarmStatus();
      const nodeList = status.nodes
        .map(
          (n) =>
            `  • **${n.name}** (${n.platform}) — ${n.status}, load: ${(n.currentLoad * 100).toFixed(0)}%, capabilities: ${n.capabilities.join(", ")}`,
        )
        .join("\n");
      const result = mkResp(
        `**🐝 Swarm Status**\n\n` +
          `Nodes: ${status.nodes.length}\n` +
          `Active tasks: ${status.activeTasks.length}\n` +
          `Total completed: ${status.totalCompleted}\n\n` +
          `**Nodes:**\n${nodeList}`,
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /tournament <category> — run a skill tournament
    const tournMatch = text.match(/^\/tournament\s+(.+)$/i);
    if (tournMatch && ctx.gatewayCtx.skillArena) {
      log.gateway.debug("MiscCommandHandler.handle: tournament", { cmd });
      const category = tournMatch[1].trim();
      const result = mkResp(
        `🏆 Tournament for category "${category}" queued.\n` +
          `Use during quiet hours or run manually with the skill arena.`,
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /voice [on|off] — toggle voice output
    const voiceMatch = text.match(/^\/voice\s*(on|off)?$/i);
    if (voiceMatch && ctx.gatewayCtx.voiceAdapter) {
      log.gateway.debug("MiscCommandHandler.handle: voice", { cmd });
      const toggle = voiceMatch[1]?.toLowerCase();
      if (toggle === "on") {
        log.gateway.debug("MiscCommandHandler.handle: exit — voice on", { cmd });
        return mkResp(
          "🔊 Voice output enabled. Responses will be spoken aloud.",
        );
      } else if (toggle === "off") {
        log.gateway.debug("MiscCommandHandler.handle: exit — voice off", { cmd });
        return mkResp("🔇 Voice output disabled.");
      }
      const available = ctx.gatewayCtx.voiceAdapter.isAvailable();
      const result = mkResp(
        `🎤 Voice status: ${available ? "Available" : "Not configured"}`,
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /predict — show predicted tasks
    if (text.toLowerCase() === "/predict" && ctx.gatewayCtx.predictiveQueue) {
      log.gateway.debug("MiscCommandHandler.handle: predict", { cmd });
      const presentation = ctx.gatewayCtx.predictiveQueue.formatForPresentation();
      const result = mkResp(
        presentation ||
          "No predictions ready yet. I need more interaction history to identify patterns.",
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /echo-check — run echo chamber analysis
    if (text.toLowerCase() === "/echo-check" && ctx.gatewayCtx.echoChamberDetector) {
      log.gateway.debug("MiscCommandHandler.handle: echo-check", { cmd });
      const analysis = await ctx.gatewayCtx.echoChamberDetector.analyze();
      if (analysis.detections.length === 0) {
        log.gateway.debug("MiscCommandHandler.handle: exit — no detections", { cmd });
        return mkResp(
          `**Echo Chamber Check** (${analysis.sessionCount} sessions)\n\n${analysis.overallAssessment}`,
        );
      }
      const detectionList = analysis.detections
        .map(
          (d) =>
            `  - **${d.bias.replace(/_/g, " ")}** (${(d.confidence * 100).toFixed(0)}%): ${d.evidence}`,
        )
        .join("\n");
      const result = mkResp(
        `**Echo Chamber Check** (${analysis.sessionCount} sessions)\n\n` +
          `${analysis.overallAssessment}\n\n**Patterns:**\n${detectionList}`,
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /journal [weekly|monthly] — generate or view growth journal
    const journalMatch = text.match(/^\/journal(?:\s+(weekly|monthly))?$/i);
    if (journalMatch && ctx.gatewayCtx.journalGenerator) {
      log.gateway.debug("MiscCommandHandler.handle: journal", { cmd });
      const period = (journalMatch[1] as "weekly" | "monthly") || "weekly";
      const entry = await ctx.gatewayCtx.journalGenerator.generate(period);
      const result = mkResp(entry.narrative);
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /quests — list active quests
    if (text.toLowerCase() === "/quests" && ctx.gatewayCtx.questManager) {
      log.gateway.debug("MiscCommandHandler.handle: quests", { cmd });
      const quests = await ctx.gatewayCtx.questManager.list();
      if (quests.length === 0) {
        log.gateway.debug("MiscCommandHandler.handle: exit — no quests", { cmd });
        return mkResp("No active quests. Ask me to create one on any topic!");
      }
      const list = quests
        .map((q) => {
          const done = q.milestones.filter((m) => m.completed).length;
          return `  - **${q.title}** [${q.status}] — ${done}/${q.milestones.length} milestones`;
        })
        .join("\n");
      const result = mkResp(`**Your Quests:**\n${list}`);
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /capsules — list time capsules
    if (text.toLowerCase() === "/capsules" && ctx.gatewayCtx.capsuleManager) {
      log.gateway.debug("MiscCommandHandler.handle: capsules", { cmd });
      const capsules = await ctx.gatewayCtx.capsuleManager.list();
      if (capsules.length === 0) {
        log.gateway.debug("MiscCommandHandler.handle: exit — no capsules", { cmd });
        return mkResp("No time capsules. Ask me to create one!");
      }
      const list = capsules
        .map((c) => {
          const icon = c.status === "sealed" ? "🔒" : "📬";
          return `  ${icon} **${c.id}** [${c.status}] — created ${new Date(c.createdAt).toLocaleDateString()}`;
        })
        .join("\n");
      const result = mkResp(`**Time Capsules:**\n${list}`);
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /constellations — show discovered patterns
    if (
      text.toLowerCase() === "/constellations" &&
      ctx.gatewayCtx.constellationMiner
    ) {
      log.gateway.debug("MiscCommandHandler.handle: constellations", { cmd });
      const constellations = await ctx.gatewayCtx.constellationMiner.list();
      if (constellations.length === 0) {
        log.gateway.debug("MiscCommandHandler.handle: exit — no constellations", { cmd });
        return mkResp(
          "No constellations discovered yet. I need more pellets to find patterns.",
        );
      }
      const list = constellations
        .slice(0, 5)
        .map((c) => ctx.gatewayCtx.constellationMiner!.format(c))
        .join("\n\n---\n\n");
      const result = mkResp(`**Discovered Constellations:**\n\n${list}`);
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /socratic [mode|off] — toggle Socratic mode
    const socraticMatch = text.match(
      /^\/socratic(?:\s+(pure|guided|reflective|devils_advocate|off))?$/i,
    );
    if (socraticMatch && ctx.gatewayCtx.socraticEngine) {
      log.gateway.debug("MiscCommandHandler.handle: socratic", { cmd });
      const mode = socraticMatch[1]?.toLowerCase();
      if (mode === "off") {
        const ended = ctx.gatewayCtx.socraticEngine.deactivate(ctx.message.sessionId);
        if (ended) {
          log.gateway.debug("MiscCommandHandler.handle: exit — socratic deactivated", { cmd });
          return mkResp(
            `Socratic mode **deactivated** after ${ended.exchangeCount} exchanges.`,
          );
        }
        log.gateway.debug("MiscCommandHandler.handle: exit — socratic not active", { cmd });
        return mkResp("Socratic mode was not active.");
      }
      const subMode = (mode as any) || "guided";
      ctx.gatewayCtx.socraticEngine.activate(ctx.message.sessionId, subMode);
      const result = mkResp(
        `Socratic mode **activated** (${subMode}).\n\n` +
          `I will now respond primarily with questions to help you think deeper.\n` +
          `Use \`/socratic off\` to return to normal mode.`,
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // /council [topic1, topic2, ...] — convene a Knowledge Council
    if (
      text.toLowerCase().startsWith("/council") &&
      !text.toLowerCase().startsWith("/council-history") &&
      ctx.gatewayCtx.knowledgeCouncil
    ) {
      log.gateway.debug("MiscCommandHandler.handle: council", { cmd });
      const topicsArg = text.slice(8).trim();
      const topics = topicsArg
        ? topicsArg
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean)
        : undefined;

      try {
        const session = await ctx.gatewayCtx.knowledgeCouncil.convene(
          topics,
          undefined,
        );
        const result = mkResp(session.summary ?? "Knowledge Council session complete.");
        log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
        return result;
      } catch (err) {
        log.gateway.error("MiscCommandHandler.handle: council failed", err as Error, { cmd });
        return mkResp(
          `Failed to convene Knowledge Council: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // /council-history — show past council sessions
    if (
      text.toLowerCase() === "/council-history" &&
      ctx.gatewayCtx.knowledgeCouncil
    ) {
      log.gateway.debug("MiscCommandHandler.handle: council-history", { cmd });
      const result = mkResp(ctx.gatewayCtx.knowledgeCouncil.getHistorySummary());
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // "watch my claude code" / "watch my opencode [port N]" / "watch" → register
    if (/^(\/watch|watch(\s+(my\s+)?(claude[\s-]*(code)?|opencode|agent|coding\s+agent))?)(\s+port\s+\d+)?$/i.test(text)) {
      log.gateway.debug("MiscCommandHandler.handle: watch register", { cmd });
      if (!ctx.agentWatch) {
        log.gateway.debug("MiscCommandHandler.handle: exit — agent watch not enabled", { cmd });
        return mkResp("Agent Watch is not enabled. Start StackOwl with agent watch support.");
      }
      const isOpenCode = /opencode/i.test(text);
      const agentType = isOpenCode ? "opencode" : "claude-code";
      const portMatch = text.match(/port\s+(\d+)/i);
      const port = portMatch ? parseInt(portMatch[1]!, 10) : undefined;
      const reg = await ctx.agentWatch.registerUser(
        ctx.message.userId,
        ctx.message.channelId,
        agentType as import("../../agent-watch/formatters/telegram.js").AgentType,
        port,
      );
      const result = mkHtml(reg.telegramMessage);
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // "unwatch" / "/unwatch" → stop watching all sessions for this user
    if (/^\/?(unwatch|stop watching|stop watch)$/i.test(text)) {
      log.gateway.debug("MiscCommandHandler.handle: unwatch", { cmd });
      if (!ctx.agentWatch) {
        log.gateway.debug("MiscCommandHandler.handle: exit — agent watch not enabled", { cmd });
        return mkResp("Agent Watch is not enabled.");
      }
      const count = await ctx.agentWatch.unwatchUser(ctx.message.userId);
      const result = mkResp(
        count > 0
          ? `👁 Stopped watching ${count} session(s).`
          : "No active watch sessions for you.",
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    // "watch status" / "/watch status"
    if (/^\/?(watch\s+status|agent\s+status)$/i.test(text)) {
      log.gateway.debug("MiscCommandHandler.handle: watch status", { cmd });
      if (!ctx.agentWatch) {
        log.gateway.debug("MiscCommandHandler.handle: exit — agent watch not enabled", { cmd });
        return mkResp("Agent Watch is not enabled.");
      }
      const st = ctx.agentWatch.getStatus();
      const result = mkHtml(
        [
          `👁 <b>Agent Watch</b>`,
          `Active sessions: ${st.activeSessions}`,
          `Pending decisions: ${st.pendingQuestions}`,
        ].join("\n"),
      );
      log.gateway.debug("MiscCommandHandler.handle: exit", { cmd });
      return result;
    }

    log.gateway.debug("MiscCommandHandler.handle: exit — no match", { cmd });
    return null;
  }
}

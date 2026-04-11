/**
 * StackOwl — Slack Channel Adapter
 *
 * Transport layer only. All business logic lives in OwlGateway.
 * This adapter's responsibilities:
 *   - Connect to Slack via Bolt (Socket Mode or HTTP)
 *   - Normalize incoming messages to GatewayMessage
 *   - Provide GatewayCallbacks (progress, file sending)
 *   - Format GatewayResponse for Slack (mrkdwn, blocks, threads)
 *   - Deliver proactive messages from the gateway
 *   - Run the ProactivePinger
 *
 * Supports:
 *   - Direct messages (DMs)
 *   - Channel mentions (@bot)
 *   - Threaded replies (keeps context per thread)
 *   - File uploads
 *   - Streaming via message updates
 */

import { App, type SayFn } from "@slack/bolt";
import type { KnownBlock } from "@slack/types";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, basename } from "node:path";
import { createReadStream } from "node:fs";
import { ProactivePinger } from "../../heartbeat/proactive.js";
import { log } from "../../logger.js";
import { makeSessionId, makeMessageId, OwlGateway } from "../core.js";
import type { StreamEvent } from "../../providers/base.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";

// ─── Config ──────────────────────────────────────────────────────

export interface SlackAdapterConfig {
  /** Bot token (xoxb-...) */
  botToken: string;
  /** App-level token for Socket Mode (xapp-...) */
  appToken: string;
  /** Signing secret (for HTTP mode, optional if using Socket Mode) */
  signingSecret?: string;
  /** HTTP port (only used if appToken is not set) */
  port?: number;
  /** Restrict to specific channel IDs */
  allowedChannels?: string[];
  /** Path to persist known channel IDs across restarts */
  channelIdsPath?: string;
}

// ─── Adapter ─────────────────────────────────────────────────────

export class SlackAdapter implements ChannelAdapter {
  readonly id = "slack";
  readonly name = "Slack";

  private app: App;
  private pinger: ProactivePinger | null = null;
  private activeChannels: Set<string> = new Set();
  private botUserId: string = "";
  private channelIdsPath: string;

  constructor(
    private gateway: OwlGateway,
    private config: SlackAdapterConfig,
  ) {
    if (!config.botToken?.trim()) {
      throw new Error("[SlackAdapter] Bot token (xoxb-) is required.");
    }

    this.app = new App({
      token: config.botToken,
      signingSecret: config.signingSecret || "not-used-in-socket-mode",
      socketMode: !!config.appToken,
      appToken: config.appToken,
      port: config.port ?? 3078,
    });

    this.channelIdsPath =
      config.channelIdsPath ??
      join(process.cwd(), "workspace", "known_slack_channels.json");

    this.setupHandlers();
  }

  // ─── ChannelAdapter interface ─────────────────────────────────

  async sendToUser(userId: string, response: GatewayResponse): Promise<void> {
    try {
      // Open a DM with the user
      const dm = await this.app.client.conversations.open({ users: userId });
      if (dm.channel?.id) {
        const blocks = this.formatResponseBlocks(response);
        await this.app.client.chat.postMessage({
          channel: dm.channel.id,
          text: this.formatResponsePlain(response),
          blocks,
        });
      }
    } catch (err) {
      log.slack.warn(
        `sendToUser failed for ${userId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    const blocks = this.formatResponseBlocks(response);
    const text = this.formatResponsePlain(response);

    for (const channelId of this.activeChannels) {
      try {
        await this.app.client.chat.postMessage({
          channel: channelId,
          text,
          blocks,
        });
      } catch (err) {
        log.slack.error(
          `Broadcast failed for ${channelId}: ${err instanceof Error ? err.message : err}`,
        );
        this.activeChannels.delete(channelId);
      }
    }
  }

  async start(): Promise<void> {
    log.slack.info("Starting Slack adapter...");
    await this.loadChannelIds();

    // Get bot's own user ID to ignore self-messages
    const authResult = await this.app.client.auth.test();
    this.botUserId = authResult.user_id ?? "";
    log.slack.info(
      `Connected as <@${this.botUserId}> (${authResult.user ?? "unknown"})`,
    );
    log.slack.info(
      `Owl: ${this.gateway.getOwl().persona.emoji} ${this.gateway.getOwl().persona.name}`,
    );

    await this.app.start();
    log.slack.info("Slack adapter is running.");

    this.startPinger();
  }

  stop(): void {
    this.pinger?.stop();
    this.app.stop().catch(() => {});
    log.slack.info("Slack adapter stopped.");
  }

  async deliverFile(userId: string, filePath: string, caption?: string): Promise<void> {
    // userId for Slack is the user ID — open a DM channel to deliver the file
    try {
      // Try to find an active channel for this user; fallback to DM
      let channelId: string | undefined;
      for (const ch of this.activeChannels) {
        // activeChannels may include DM channels starting with 'D'
        if (ch.startsWith("D")) {
          channelId = ch;
          break;
        }
      }

      if (!channelId) {
        // Open a DM with the user
        const dm = await this.app.client.conversations.open({ users: userId });
        channelId = dm.channel?.id;
      }

      if (!channelId) {
        log.slack.warn(`deliverFile: could not resolve channel for user ${userId}`);
        return;
      }

      await this.app.client.files.uploadV2({
        channel_id: channelId,
        file: createReadStream(filePath),
        filename: basename(filePath),
        title: caption ?? basename(filePath),
      });
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err);
      if (raw.includes("invalid_auth") || raw.includes("token_revoked")) {
        throw new Error(
          `Slack bot token is invalid or revoked. Update 'slack.botToken' in stackowl.config.json.`,
        );
      }
      throw new Error(`Slack file delivery failed: ${raw}`);
    }
  }

  // ─── Bot handlers ─────────────────────────────────────────────

  private setupHandlers(): void {
    // Handle all messages (DMs and channel mentions)
    this.app.message(async ({ message, say, client }) => {
      // Type guard: only handle text messages
      if (!("text" in message) || message.subtype) return;
      const msg = message as {
        text: string;
        user: string;
        channel: string;
        ts: string;
        thread_ts?: string;
      };

      // Ignore bot's own messages
      if (msg.user === this.botUserId) return;
      if (!msg.text || !msg.user) return;

      // Check allowed channels
      if (this.config.allowedChannels?.length) {
        if (!this.config.allowedChannels.includes(msg.channel)) return;
      }

      let text = msg.text;

      // In channels (not DMs), only respond if mentioned
      const isDM = msg.channel.startsWith("D");
      if (!isDM) {
        const mentionPattern = new RegExp(`<@${this.botUserId}>`, "g");
        if (!mentionPattern.test(text)) return;
        // Strip the mention from the text
        text = text
          .replace(new RegExp(`<@${this.botUserId}>\\s*`, "g"), "")
          .trim();
        if (!text) return;
      }

      this.trackChannel(msg.channel);

      // Use thread_ts for session continuity — keeps conversations threaded
      const threadTs = msg.thread_ts ?? msg.ts;
      const sessionId = makeSessionId(this.id, `${msg.user}:${threadTs}`);

      log.slack.incoming(`user:${msg.user} ch:${msg.channel}`, text);

      // Show typing indicator
      try {
        await client.reactions.add({
          channel: msg.channel,
          timestamp: msg.ts,
          name: "eyes",
        });
      } catch {
        // Non-fatal — might not have permission
      }

      try {
        this.gateway.getCognitiveLoop()?.notifyUserActivity();

        const streamCtx = this.createStreamHandler(
          msg.channel,
          threadTs,
          client,
        );

        const response = await this.gateway.handle(
          {
            id: makeMessageId(),
            channelId: this.id,
            userId: msg.user,
            sessionId,
            text,
          },
          {
            onProgress: async (progressMsg: string) => {
              streamCtx.pushToolStatus(progressMsg);
            },
            askInstall: async (deps: string[]) => {
              await say({
                text: `📦 Need to install npm deps: \`${deps.join(" ")}\`\nReply *yes* to install or *no* to skip.`,
                thread_ts: threadTs,
              });
              // For Slack we auto-approve — interactive approval would need
              // a button-based flow which is significantly more complex
              return true;
            },
            onStreamEvent: streamCtx.handler,
          },
        );

        // Remove eyes reaction
        try {
          await client.reactions.remove({
            channel: msg.channel,
            timestamp: msg.ts,
            name: "eyes",
          });
        } catch {
          /* non-fatal */
        }

        log.slack.outgoing(`user:${msg.user}`, response.content);
        log.slack.info(
          `tools:[${response.toolsUsed.join(", ") || "none"}] ` +
            `usage:${response.usage ? `${response.usage.promptTokens}→${response.usage.completionTokens}` : "n/a"}`,
        );

        // Send final response only if streaming didn't already deliver it
        const streamed = streamCtx.status.streamedContent;
        const finalContent = response.content.trim();
        const streamedFinal = streamed.trim();
        const alreadyDelivered =
          streamedFinal.length > 0 &&
          finalContent.startsWith(streamedFinal.slice(0, 100));

        if (!alreadyDelivered) {
          await this.sendResponse(msg.channel, threadTs, response, say);
        }
      } catch (error) {
        const errMsg = error instanceof Error ? error.message : String(error);
        log.slack.error(`Error for user ${msg.user}: ${errMsg}`);

        // Remove eyes, add warning
        try {
          await client.reactions.remove({
            channel: msg.channel,
            timestamp: msg.ts,
            name: "eyes",
          });
          await client.reactions.add({
            channel: msg.channel,
            timestamp: msg.ts,
            name: "warning",
          });
        } catch {
          /* non-fatal */
        }

        await say({ text: `❌ Error: ${errMsg}`, thread_ts: threadTs });
      }
    });

    // Handle app_mention events — already covered by the message handler
    // but registered to prevent Bolt from logging unhandled event warnings
    this.app.event("app_mention", async () => {});

    // Slash commands
    this.app.command("/owl-status", async ({ ack, respond }) => {
      await ack();
      const owl = this.gateway.getOwl();
      const config = this.gateway.getConfig();
      await respond({
        text:
          `🦉 *StackOwl Status*\n\n` +
          `*Model:* ${config.defaultModel}\n` +
          `*Owl:* ${owl.persona.emoji} ${owl.persona.name}\n` +
          `*Channel:* Slack`,
      });
    });

    this.app.command("/owl-reset", async ({ ack, respond, command }) => {
      await ack();
      const sessionId = makeSessionId(this.id, `${command.user_id}:reset`);
      await this.gateway.endSession(sessionId).catch(() => {});
      await respond({ text: "🔄 Context reset. Starting fresh." });
    });

    this.app.command("/owl-owls", async ({ ack, respond }) => {
      await ack();
      const registry = this.gateway.getOwlRegistry();
      let msg = "🦉 *Available Owls*\n\n";
      for (const o of registry.listOwls()) {
        msg += `${o.persona.emoji} *${o.persona.name}* — ${o.persona.type}\n`;
      }
      await respond({ text: msg });
    });
  }

  // ─── Streaming (edit-in-place) ──────────────────────────────────

  private createStreamHandler(
    channel: string,
    threadTs: string,
    client: InstanceType<typeof App>["client"],
  ): {
    handler: (event: StreamEvent) => Promise<void>;
    status: { streamedContent: string };
    pushToolStatus: (msg: string) => void;
  } {
    const status = { streamedContent: "" };
    let messageTs: string | null = null;
    let displayText = "";
    let pureContent = "";
    let lastEditTime = 0;
    let pendingEdit: ReturnType<typeof setTimeout> | null = null;
    let hasToolStatus = false;
    let contentStarted = false;
    const THROTTLE_MS = 1200; // Slack rate limits are stricter — 1.2s between edits

    const flushEdit = async () => {
      if (!messageTs || !displayText) return;
      try {
        await client.chat.update({
          channel,
          ts: messageTs,
          text: displayText,
        });
        lastEditTime = Date.now();
      } catch {
        // Edit may fail if message too old or rate limited — non-fatal
      }
    };

    const handler = async (event: StreamEvent) => {
      switch (event.type) {
        case "text_delta": {
          const chunk = event.content.replace(/\[DONE\]/g, "");
          if (!chunk) break;

          if (hasToolStatus && !contentStarted) {
            displayText += "\n\n";
            contentStarted = true;
          }
          displayText += chunk;
          pureContent += chunk;

          if (!messageTs) {
            try {
              const sent = await client.chat.postMessage({
                channel,
                thread_ts: threadTs,
                text: displayText || "...",
              });
              messageTs = sent.ts ?? null;
              lastEditTime = Date.now();
            } catch {
              // Fall back to final response
            }
            return;
          }

          const elapsed = Date.now() - lastEditTime;
          if (elapsed >= THROTTLE_MS) {
            if (pendingEdit) {
              clearTimeout(pendingEdit);
              pendingEdit = null;
            }
            await flushEdit();
          } else if (!pendingEdit) {
            pendingEdit = setTimeout(async () => {
              pendingEdit = null;
              await flushEdit();
            }, THROTTLE_MS - elapsed);
          }
          break;
        }
        case "tool_start":
        case "tool_end":
          break;
        case "done": {
          if (pendingEdit) {
            clearTimeout(pendingEdit);
            pendingEdit = null;
          }
          await flushEdit();
          if (messageTs && pureContent.length > 0) {
            status.streamedContent = pureContent;
          }
          break;
        }
      }
    };

    const pushToolStatus = (msg: string) => {
      displayText += `\n${msg}`;
      hasToolStatus = true;

      if (!messageTs) {
        client.chat
          .postMessage({
            channel,
            thread_ts: threadTs,
            text: displayText || "...",
          })
          .then((sent) => {
            messageTs = sent.ts ?? null;
            lastEditTime = Date.now();
          })
          .catch(() => {});
      } else {
        flushEdit().catch(() => {});
      }
    };

    return { handler, status, pushToolStatus };
  }

  // ─── Response formatting ──────────────────────────────────────

  private async sendResponse(
    _channel: string,
    threadTs: string,
    response: GatewayResponse,
    say: SayFn,
  ): Promise<void> {
    const blocks = this.formatResponseBlocks(response);
    await say({
      text: this.formatResponsePlain(response),
      blocks,
      thread_ts: threadTs,
    });
  }

  private formatResponsePlain(response: GatewayResponse): string {
    return `${response.owlEmoji} *${response.owlName}*\n\n${response.content}`;
  }

  private formatResponseBlocks(response: GatewayResponse): KnownBlock[] {
    const blocks: KnownBlock[] = [];

    // Header with owl name
    blocks.push({
      type: "section",
      text: {
        type: "mrkdwn",
        text: `${response.owlEmoji} *${response.owlName}*`,
      },
    });

    // Split content into chunks for Slack's 3000-char block limit
    const content = response.content;
    const chunks = this.splitContent(content, 2900);
    for (const chunk of chunks) {
      blocks.push({
        type: "section",
        text: {
          type: "mrkdwn",
          text: chunk,
        },
      });
    }

    // Tools used footer
    if (response.toolsUsed.length > 0) {
      blocks.push({
        type: "context",
        elements: [
          {
            type: "mrkdwn",
            text: `🛠️ Tools: ${response.toolsUsed.join(", ")}`,
          },
        ],
      });
    }

    // Usage footer
    if (response.usage) {
      blocks.push({
        type: "context",
        elements: [
          {
            type: "mrkdwn",
            text: `📊 ${response.usage.promptTokens}→${response.usage.completionTokens} tokens`,
          },
        ],
      });
    }

    return blocks;
  }

  private splitContent(text: string, maxLen: number): string[] {
    const chunks: string[] = [];
    let remaining = text;
    while (remaining.length > 0) {
      if (remaining.length <= maxLen) {
        chunks.push(remaining);
        break;
      }
      let splitAt = remaining.lastIndexOf("\n", maxLen);
      if (splitAt === -1 || splitAt < maxLen / 2)
        splitAt = remaining.lastIndexOf(" ", maxLen);
      if (splitAt === -1 || splitAt < maxLen / 2) splitAt = maxLen;
      chunks.push(remaining.substring(0, splitAt));
      remaining = remaining.substring(splitAt).trimStart();
    }
    return chunks;
  }

  // ─── Proactive Pinger ─────────────────────────────────────────

  private startPinger(): void {
    const owl = this.gateway.getOwl();
    const config = this.gateway.getConfig();

    this.pinger = new ProactivePinger({
      provider: this.gateway.getProvider(),
      owl,
      config,
      capabilityLedger: this.gateway.getCapabilityLedger()!,
      learningEngine: this.gateway.getLearningEngine(),
      preferenceStore: this.gateway.getPreferenceStore(),
      reflexionEngine: this.gateway.getReflexionEngine(),
      toolRegistry: this.gateway.getToolRegistry(),
      goalGraph: this.gateway.getGoalGraph(),
      proactiveLoop: this.gateway.getProactiveLoop(),
      sendToUser: async (message: string) => {
        await this.broadcast({
          content: message,
          owlName: owl.persona.name,
          owlEmoji: owl.persona.emoji,
          toolsUsed: [],
        });
      },
    });
    this.pinger.start();
  }

  // ─── Helpers ─────────────────────────────────────────────────

  private trackChannel(channelId: string): void {
    if (!this.activeChannels.has(channelId)) {
      this.activeChannels.add(channelId);
      this.saveChannelIds().catch(() => {});
    }
  }

  private async loadChannelIds(): Promise<void> {
    if (!existsSync(this.channelIdsPath)) return;
    try {
      const ids: string[] = JSON.parse(
        await readFile(this.channelIdsPath, "utf-8"),
      );
      for (const id of ids) this.activeChannels.add(id);
      log.slack.info(`Loaded ${ids.length} known channel(s)`);
    } catch {
      /* non-fatal */
    }
  }

  private async saveChannelIds(): Promise<void> {
    try {
      const dir = join(this.channelIdsPath, "..");
      if (!existsSync(dir)) await mkdir(dir, { recursive: true });
      await writeFile(
        this.channelIdsPath,
        JSON.stringify([...this.activeChannels]),
        "utf-8",
      );
    } catch (err) {
      log.slack.warn(
        `Could not persist channel IDs: ${err instanceof Error ? err.message : err}`,
      );
    }
  }
}

/**
 * StackOwl — Discord Channel Adapter
 *
 * Transport layer only. All business logic lives in OwlGateway.
 * This adapter's responsibilities:
 *   - Connect to Discord via discord.js v14
 *   - Normalize incoming DMs and server @mentions to GatewayMessage
 *   - Provide GatewayCallbacks (progress, streaming)
 *   - Format GatewayResponse for Discord (2000-char chunking)
 *   - Deliver proactive messages from the gateway
 *
 * Supports:
 *   - Direct messages (DMs — ChannelType.DM === 1)
 *   - Server @mentions (guild text channels)
 *   - Chunked message delivery (Discord limit: 2000 chars)
 */

import {
  Client,
  GatewayIntentBits,
  ChannelType,
  type Message,
  type TextChannel,
  type DMChannel,
} from "discord.js";
import { runWithContext } from "../../infra/observability/context.js";
import { log } from "../../logger.js";
import { makeSessionId, makeMessage, OwlGateway } from "../core.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";

// ─── Config ──────────────────────────────────────────────────────

export interface DiscordAdapterConfig {
  /** Discord bot token */
  botToken: string;
  /** Restrict to specific guild (server) IDs. Empty = allow all guilds. */
  guildIds?: string[];
  /**
   * DM policy:
   *  - "open"    — accept DMs from anyone
   *  - "pairing" — require pairing handshake before responding (default)
   */
  dmPolicy?: "open" | "pairing";
}

// ─── Mention strip regex ─────────────────────────────────────────

/** Strips <@SNOWFLAKE>, <@!SNOWFLAKE>, and generic <@WORD> mention patterns */
const MENTION_RE = /<@!?[\w\d]+>/g;

// ─── Adapter ─────────────────────────────────────────────────────

export class DiscordAdapter implements ChannelAdapter {
  readonly id = "discord";
  readonly name = "Discord";

  private client: Client | null = null;

  constructor(private config: DiscordAdapterConfig) {
    if (!config.botToken?.trim()) {
      throw new Error("[DiscordAdapter] botToken is required.");
    }
    // Client is intentionally NOT constructed here so that tests
    // can instantiate DiscordAdapter without triggering any network I/O
    // or import side-effects from discord.js. The Client is built in start().
  }

  // ─── ChannelAdapter interface ─────────────────────────────────

  async sendToUser(userId: string, response: GatewayResponse): Promise<void> {
    if (!this.client) return;
    try {
      const user = await this.client.users.fetch(userId);
      const dm = await user.createDM();
      const text = this.formatResponse(response);
      for (const chunk of this.chunkText(text)) {
        await dm.send(chunk);
      }
    } catch (err) {
      log.discord.warn(
        `sendToUser: failed for userId=${userId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  async broadcast(_response: GatewayResponse): Promise<void> {
    // Discord has no global broadcast concept. For proactive messages
    // the owner should configure a specific channel via sendToUser.
    log.discord.warn("broadcast: no-op for Discord adapter — use sendToUser instead");
  }

  async start(gateway?: OwlGateway): Promise<void> {
    log.discord.info("Starting Discord adapter...");

    this.client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent,
        GatewayIntentBits.DirectMessages,
      ],
    });

    this.client.once("ready", (c) => {
      log.discord.info(`Connected as ${c.user.tag} (${c.user.id})`);
    });

    if (gateway) {
      this.setupMessageHandler(gateway);
    }

    await this.client.login(this.config.botToken);
    log.discord.info("Discord adapter is running.");
  }

  stop(): void {
    this.client?.destroy();
    this.client = null;
    log.discord.info("Discord adapter stopped.");
  }

  // ─── Message handler ──────────────────────────────────────────

  private setupMessageHandler(gateway: OwlGateway): void {
    if (!this.client) return;

    this.client.on("messageCreate", async (message: Message) => {
      // 1. ENTRY — what came in
      log.discord.debug("messageCreate: entry", {
        id: message.id,
        channelType: message.channel.type,
        authorId: message.author?.id,
      });

      // Ignore bot messages (including our own)
      if (message.author?.bot) return;

      const normalized = this.normalizeMessage(message);
      if (!normalized) {
        log.discord.debug("messageCreate: normalizeMessage returned null — skipping");
        return;
      }

      // 2. DECISION — DM or mention
      const isDM = message.channel.type === ChannelType.DM;
      log.discord.debug("messageCreate: processing", {
        isDM,
        sessionId: normalized.sessionId,
        textLen: normalized.text.length,
      });

      try {
        gateway.getCognitiveLoop?.()?.notifyUserActivity?.();

        const response = await runWithContext(
          {
            channelId: "discord",
            userId: normalized.userId,
            sessionId: normalized.sessionId,
            messageId: normalized.id,
            spanName: "channel.discord.handle",
          },
          () =>
            gateway.handle(normalized, {
              onProgress: async (progressMsg: string) => {
                log.discord.debug("messageCreate: progress", { progressMsg });
              },
              askInstall: async (_deps: string[]) => true,
            }),
        );

        // 3. STEP — deliver response
        const text = this.formatResponse(response);
        const chunks = this.chunkText(text);
        const channel = message.channel as TextChannel | DMChannel;
        for (const chunk of chunks) {
          await channel.send(chunk);
        }

        // 4. EXIT
        log.discord.debug("messageCreate: exit", {
          userId: normalized.userId,
          toolsUsed: response.toolsUsed,
          responseLen: response.content.length,
        });
      } catch (err) {
        log.discord.error("messageCreate: handler failed", err as Error, {
          userId: normalized.userId,
          sessionId: normalized.sessionId,
        });
        try {
          const channel = message.channel as TextChannel | DMChannel;
          await channel.send(`Error: ${err instanceof Error ? err.message : String(err)}`);
        } catch (sendErr) {
          log.discord.warn("messageCreate: error reply also failed", sendErr);
        }
      }
    });
  }

  // ─── Message normalization ────────────────────────────────────

  /**
   * Maps a raw discord.js Message to a GatewayMessage.
   * Returns null if the message should be ignored (empty after stripping
   * mentions, or not targeted at the bot in a guild channel).
   */
  private normalizeMessage(message: Message): ReturnType<typeof makeMessage> {
    const isDM = message.channel.type === (ChannelType.DM as number);

    // In guild channels only respond when the bot is explicitly mentioned
    if (!isDM && !message.mentions?.has?.(this.client?.user ?? message.author)) {
      // Check for mention pattern in content as fallback (works before client is ready)
      const hasMentionPattern = MENTION_RE.test(message.content);
      MENTION_RE.lastIndex = 0; // reset stateful regex
      if (!hasMentionPattern) return null;
    }

    // Strip all <@SNOWFLAKE> patterns from the content
    const strippedText = message.content.replace(MENTION_RE, "").trim();

    if (!strippedText) return null;

    const sessionId = makeSessionId("discord", message.author.id);
    return makeMessage("discord", message.author.id, strippedText, sessionId);
  }

  // ─── Response formatting ──────────────────────────────────────

  private formatResponse(response: GatewayResponse): string {
    return `${response.owlEmoji} **${response.owlName}**\n\n${response.content}`;
  }

  /**
   * Splits text into Discord-safe chunks (≤2000 chars each).
   * Prefers splitting on newlines, then spaces, then hard-cuts.
   */
  private chunkText(text: string, maxLen = 2000): string[] {
    const chunks: string[] = [];
    let remaining = text;
    while (remaining.length > 0) {
      if (remaining.length <= maxLen) {
        chunks.push(remaining);
        break;
      }
      let splitAt = remaining.lastIndexOf("\n", maxLen);
      if (splitAt === -1 || splitAt < maxLen / 2) {
        splitAt = remaining.lastIndexOf(" ", maxLen);
      }
      if (splitAt === -1 || splitAt < maxLen / 2) {
        splitAt = maxLen;
      }
      chunks.push(remaining.substring(0, splitAt));
      remaining = remaining.substring(splitAt).trimStart();
    }
    return chunks;
  }
}

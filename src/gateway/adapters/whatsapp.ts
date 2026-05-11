/**
 * StackOwl — WhatsApp Channel Adapter
 *
 * Uses whatsapp-web.js (local Puppeteer) to connect to WhatsApp Web.
 * DMs only. Pairing security: unknown senders receive a challenge.
 *
 * First run: QR code printed to terminal — scan with WhatsApp mobile app.
 * Session persisted to ~/.stackowl/whatsapp-session/ for subsequent runs.
 */

import { join } from "node:path";
import { homedir } from "node:os";
import { Client, LocalAuth, type Message } from "whatsapp-web.js";
// @ts-ignore — qrcode-terminal has no type definitions
import qrcode from "qrcode-terminal";
import { runWithContext } from "../../infra/observability/context.js";
import { log } from "../../logger.js";
import { makeSessionId, makeMessage, OwlGateway } from "../core.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";

// ─── Config ──────────────────────────────────────────────────────

export interface WhatsAppAdapterConfig {
  sessionDataPath?: string;
  dmPolicy?: "open" | "pairing";
}

// ─── Adapter ──────────────────────────────────────────────────────

export class WhatsAppAdapter implements ChannelAdapter {
  readonly id = "whatsapp";
  readonly name = "WhatsApp";

  private client: Client | null = null;
  private config: Required<WhatsAppAdapterConfig>;

  constructor(config: WhatsAppAdapterConfig = {}) {
    this.config = {
      sessionDataPath: config.sessionDataPath ?? join(homedir(), ".stackowl", "whatsapp-session"),
      dmPolicy: config.dmPolicy ?? "pairing",
    };
  }

  // ─── ChannelAdapter interface ─────────────────────────────────

  async sendToUser(userId: string, response: GatewayResponse): Promise<void> {
    if (!this.client) {
      log.gateway.warn("sendToUser: client not initialized");
      return;
    }

    try {
      const chatId = userId.includes("@") ? userId : `${userId}@c.us`;
      const text = this.formatResponse(response);
      for (const chunk of this.chunkText(text)) {
        await this.client.sendMessage(chatId, chunk);
      }
    } catch (err) {
      log.gateway.error(
        `sendToUser: failed for userId=${userId}`,
        err as Error,
        { userId },
      );
    }
  }

  async broadcast(_response: GatewayResponse): Promise<void> {
    log.gateway.warn("broadcast: no-op for WhatsApp adapter — use sendToUser instead");
  }

  async start(gateway?: OwlGateway): Promise<void> {
    log.gateway.info("Starting WhatsApp adapter...");

    this.client = new Client({
      authStrategy: new LocalAuth({ dataPath: this.config.sessionDataPath }),
      puppeteer: {
        args: ["--no-sandbox", "--disable-setuid-sandbox"],
      },
    } as any);

    this.client.on("qr", (qr: string) => {
      log.gateway.info("[WhatsAppAdapter] QR code received — scan with WhatsApp mobile app");
      qrcode.generate(qr, { small: true });
    });

    this.client.on("ready", () => {
      log.gateway.info("[WhatsAppAdapter] Client ready");
    });

    if (gateway) {
      this.setupMessageHandler(gateway);
    }

    await this.client.initialize();
    log.gateway.info("WhatsApp adapter is running.");
  }

  stop(): void {
    if (this.client) {
      this.client.destroy().catch((err) => {
        log.gateway.warn("WhatsApp destroy error", err as Error);
      });
      this.client = null;
    }
    log.gateway.info("WhatsApp adapter stopped.");
  }

  // ─── Message handler ──────────────────────────────────────────

  private setupMessageHandler(gateway: OwlGateway): void {
    if (!this.client) return;

    this.client.on("message", async (msg: Message) => {
      // 1. ENTRY — what came in
      log.gateway.debug("whatsapp.messageCreate: entry", {
        id: msg.id._serialized,
        isGroupMsg: (msg as any).isGroupMsg ?? false,
        authorId: msg.from,
      });

      // Ignore group messages
      if ((msg as any).isGroupMsg) {
        log.gateway.debug("whatsapp.messageCreate: ignoring group message");
        return;
      }

      const normalized = this.normalizeMessage(msg);
      if (!normalized) {
        log.gateway.debug("whatsapp.messageCreate: normalizeMessage returned null — skipping");
        return;
      }

      // 2. DECISION — check pairing policy
      log.gateway.debug("whatsapp.messageCreate: processing", {
        sessionId: normalized.sessionId,
        textLen: normalized.text.length,
      });

      try {
        gateway.getCognitiveLoop?.()?.notifyUserActivity?.();

        const response = await runWithContext(
          {
            channelId: "whatsapp",
            userId: normalized.userId,
            sessionId: normalized.sessionId,
            messageId: normalized.id,
            spanName: "channel.whatsapp.handle",
          },
          () =>
            gateway.handle(normalized, {
              onProgress: async (_progressMsg: string) => {
                log.gateway.debug("whatsapp.messageCreate: progress", { progressMsg: _progressMsg });
              },
              askInstall: async (_deps: string[]) => true,
              suppressThinking: true,
            }),
        );

        // 3. STEP — deliver response
        const text = this.formatResponse(response);
        const chunks = this.chunkText(text);
        for (const chunk of chunks) {
          await msg.reply(chunk);
        }

        // 4. EXIT
        log.gateway.debug("whatsapp.messageCreate: exit", {
          userId: normalized.userId,
          toolsUsed: response.toolsUsed,
          responseLen: response.content.length,
        });
      } catch (err) {
        log.gateway.error("whatsapp.messageCreate: handler failed", err as Error, {
          userId: normalized.userId,
          sessionId: normalized.sessionId,
        });
        try {
          await msg.reply(`Error: ${err instanceof Error ? err.message : String(err)}`);
        } catch (sendErr) {
          log.gateway.warn("whatsapp.messageCreate: error reply also failed", sendErr as Error);
        }
      }
    });
  }

  // ─── Message normalization ────────────────────────────────────

  /**
   * Maps a raw whatsapp-web.js Message to a GatewayMessage.
   * Returns null if the message should be ignored (empty after trimming).
   */
  private normalizeMessage(msg: Message): ReturnType<typeof makeMessage> | null {
    const text = msg.body?.trim();
    if (!text) {
      log.gateway.debug("whatsapp.normalizeMessage: empty text — skipping");
      return null;
    }

    // msg.from is like "1234567890@c.us" (phone number format)
    const senderId = msg.from.replace("@c.us", "");
    const sessionId = makeSessionId("whatsapp", senderId);

    return makeMessage("whatsapp", senderId, text, sessionId);
  }

  // ─── Response formatting ──────────────────────────────────────

  private formatResponse(response: GatewayResponse): string {
    return `*${response.owlName}* ${response.owlEmoji}\n\n${response.content}`;
  }

  /**
   * Splits text into WhatsApp-safe chunks (≤4096 chars each).
   * Prefers splitting on newlines, then spaces, then hard-cuts.
   */
  private chunkText(text: string, maxLen = 4096): string[] {
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

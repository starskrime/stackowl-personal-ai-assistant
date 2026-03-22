/**
 * StackOwl — Telegram Channel Adapter
 *
 * Transport layer only. All business logic lives in OwlGateway.
 * This adapter's responsibilities:
 *   - Connect to Telegram via grammY
 *   - Normalize incoming messages to GatewayMessage
 *   - Provide GatewayCallbacks (progress, file sending, dep install prompts)
 *   - Format GatewayResponse for Telegram (MarkdownV2, chunking, photos)
 *   - Deliver proactive messages from the gateway
 *   - Run the ProactivePinger
 */

import { Bot, InputFile, type Context } from 'grammy';
import { autoRetry } from '@grammyjs/auto-retry';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, extname } from 'node:path';
import { ProactivePinger } from '../../heartbeat/proactive.js';
import { log } from '../../logger.js';
import { makeSessionId, makeMessageId, OwlGateway } from '../core.js';
import type { StreamEvent } from '../../providers/base.js';
import type { ChannelAdapter, GatewayResponse } from '../types.js';

// ─── Config ──────────────────────────────────────────────────────

export interface TelegramAdapterConfig {
    botToken: string;
    allowedUserIds?: number[];
    /** Path to persist known chat IDs across restarts */
    chatIdsPath?: string;
}

// ─── Pending state per user ───────────────────────────────────────

interface UserState {
    /** Resolver waiting for y/n on npm install */
    pendingInstallResolve?: (approved: boolean) => void;
}

// ─── Adapter ─────────────────────────────────────────────────────

export class TelegramAdapter implements ChannelAdapter {
    readonly id = 'telegram';
    readonly name = 'Telegram';

    private bot: Bot;
    private pinger: ProactivePinger | null = null;
    private activeChatIds: Set<number> = new Set();
    private userState: Map<number, UserState> = new Map();
    private chatIdsPath: string;

    constructor(
        private gateway: OwlGateway,
        private config: TelegramAdapterConfig,
    ) {
        if (!config.botToken?.trim()) {
            throw new Error('[TelegramAdapter] Bot token is required.');
        }
        this.bot = new Bot(config.botToken);
        this.bot.api.config.use(autoRetry({ maxRetryAttempts: 3, maxDelaySeconds: 300 }));
        this.chatIdsPath = config.chatIdsPath ?? join(process.cwd(), 'workspace', 'known_chat_ids.json');
        this.setupHandlers();
    }

    // ─── ChannelAdapter interface ─────────────────────────────────

    async sendToUser(userId: string, response: GatewayResponse): Promise<void> {
        const chatId = Number(userId);
        if (!chatId) return;
        const text = this.formatResponse(response);
        try {
            await this.sendChunked(chatId, text);
        } catch (err) {
            log.telegram.warn(`sendToUser failed for ${userId}: ${err instanceof Error ? err.message : err}`);
        }
    }

    async broadcast(response: GatewayResponse): Promise<void> {
        const text = this.formatResponse(response);
        for (const chatId of this.activeChatIds) {
            try {
                await this.sendChunked(chatId, text);
            } catch (err) {
                log.telegram.error(`Broadcast failed for ${chatId}: ${err instanceof Error ? err.message : err}`);
                this.activeChatIds.delete(chatId);
            }
        }
    }

    async start(): Promise<void> {
        log.telegram.info('Starting Telegram adapter...');
        await this.loadChatIds();

        const me = await this.bot.api.getMe();
        log.telegram.info(`Connected as @${me.username}`);
        log.telegram.info(`Owl: ${this.gateway.getOwl().persona.emoji} ${this.gateway.getOwl().persona.name}`);

        const self = this;
        await this.bot.start({
            onStart: () => {
                log.telegram.info('Bot is running. Send /start in Telegram.');
                this.startPinger(self);
            },
        });
    }

    stop(): void {
        this.pinger?.stop();
        this.bot.stop();
        log.telegram.info('Telegram adapter stopped.');
    }

    // ─── Bot handlers ─────────────────────────────────────────────

    private setupHandlers(): void {
        const owl = this.gateway.getOwl();

        this.bot.command('start', async (ctx) => {
            if (!this.isAllowed(ctx)) return;
            this.trackChat(ctx.chat.id);
            await ctx.reply(
                `${owl.persona.emoji} *${this.esc(owl.persona.name)}* reporting for duty\\!\n\n` +
                `I'm your personal AI assistant\\. Talk to me naturally — I'll handle the rest\\. 🦉`,
                { parse_mode: 'MarkdownV2' }
            );
        });

        const resetHandler = async (ctx: any) => {
            if (!this.isAllowed(ctx)) return;
            // endSession will handle consolidation; just clear the in-memory session
            const userId = String(ctx.from?.id ?? ctx.chat.id);
            const sessionId = makeSessionId(this.id, userId);
            await this.gateway.endSession(sessionId).catch(() => { });
            await ctx.reply('🔄 Context reset. Starting fresh.');
        };

        this.bot.command('reset', resetHandler);
        this.bot.command('clear', resetHandler);

        this.bot.command('status', async (ctx) => {
            if (!this.isAllowed(ctx)) return;
            const config = this.gateway.getConfig();
            const msg =
                `🦉 *StackOwl Status*\n\n` +
                `Model: ${this.esc(config.defaultModel)}\n` +
                `Owl: ${owl.persona.emoji} ${this.esc(owl.persona.name)}\n` +
                `Channel: Telegram`;
            await ctx.reply(msg, { parse_mode: 'MarkdownV2' });
        });

        this.bot.command('owls', async (ctx) => {
            if (!this.isAllowed(ctx)) return;
            const registry = this.gateway.getOwlRegistry();
            let msg = `🦉 *Available Owls*\n\n`;
            for (const o of registry.listOwls()) {
                msg += `${o.persona.emoji} *${this.esc(o.persona.name)}* — ${this.esc(o.persona.type)}\n`;
            }
            await ctx.reply(msg, { parse_mode: 'MarkdownV2' });
        });

        this.bot.on('message:text', async (ctx) => {
            if (!this.isAllowed(ctx)) return;
            const userId = ctx.from?.id;
            if (!userId) return;

            const text = ctx.message.text;
            if (!text || text.startsWith('/')) return;

            this.trackChat(ctx.chat.id);

            // ─── Pending npm install approval ────────────────────
            const state = this.getUserState(userId);
            if (state.pendingInstallResolve) {
                const resolve = state.pendingInstallResolve;
                state.pendingInstallResolve = undefined;
                const answer = text.trim().toLowerCase();
                resolve(answer === 'yes' || answer === 'y');
                return;
            }
            // ─────────────────────────────────────────────────────

            await ctx.api.sendChatAction(ctx.chat.id, 'typing');

            // Reset heartbeat suppression — user is active
            this.pinger?.notifyUserActivity();

            log.telegram.incoming(`user:${userId}`, text);

            try {
                const streamCtx = this.createStreamHandler(ctx);
                const response = await this.gateway.handle(
                    {
                        id: makeMessageId(),
                        channelId: this.id,
                        userId: String(userId),
                        sessionId: makeSessionId(this.id, String(userId)),
                        text,
                    },
                    {
                        onProgress: async (msg: string) => {
                            // Route tool status and skill usage into the stream message (edit-in-place)
                            // instead of sending separate messages for each event.
                            const isToolStatus = /^[⚙✅❌].*\b(?:Running|Tool finished|Tool failed)\b/.test(msg);
                            const isSkillUsage = /\bUsing skill:\b/.test(msg);
                            if (isToolStatus || isSkillUsage) {
                                streamCtx.pushToolStatus(msg);
                                return;
                            }
                            try {
                                const html = this.escHtml(msg)
                                    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
                                    .replace(/`(.+?)`/g, '<code>$1</code>');
                                await ctx.reply(html, { parse_mode: 'HTML' });
                                await ctx.api.sendChatAction(ctx.chat.id, 'typing');
                            } catch (err) {
                                log.telegram.warn(`onProgress failed: ${err instanceof Error ? err.message : err}`);
                            }
                        },
                        onFile: async (filePath: string, caption?: string) => {
                            const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp']);
                            const ext = extname(filePath).toLowerCase();
                            if (IMAGE_EXTS.has(ext)) {
                                await ctx.replyWithPhoto(new InputFile(filePath), caption ? { caption } : {});
                            } else {
                                await ctx.replyWithDocument(new InputFile(filePath), caption ? { caption } : {});
                            }
                        },
                        askInstall: async (deps: string[]) => {
                            await ctx.reply(
                                `📦 Install npm deps: <code>${this.escHtml(deps.join(' '))}</code>\n\nReply <b>yes</b> to install or <b>no</b> to skip.`,
                                { parse_mode: 'HTML' }
                            );
                            return new Promise<boolean>((resolve) => {
                                state.pendingInstallResolve = resolve;
                            });
                        },
                        onStreamEvent: streamCtx.handler,
                    }
                );

                log.telegram.outgoing(`user:${userId}`, response.content);
                log.telegram.info(
                    `tools:[${response.toolsUsed.join(', ') || 'none'}] ` +
                    `usage:${response.usage ? `${response.usage.promptTokens}→${response.usage.completionTokens}` : 'n/a'}`
                );

                // Only send the final response if streaming didn't already deliver it.
                // Compare streamed content against the final response — if they differ
                // (e.g. exhaustion handler generated a new response after streaming),
                // the user needs to see the new content.
                const streamed = streamCtx.status.streamedContent;
                const finalContent = response.content.trim();
                const streamedFinal = streamed.replace(/\[DONE\]/g, '').trim();
                const compareLen = Math.min(100, streamedFinal.length, finalContent.length);
                const alreadyDelivered = compareLen > 0
                    && finalContent.slice(0, compareLen) === streamedFinal.slice(0, compareLen);
                if (!alreadyDelivered) {
                    await this.sendResponse(ctx, response);
                }

                if (response.usage) {
                    await ctx.reply(
                        `_${response.usage.promptTokens}→${response.usage.completionTokens} tokens_`,
                        { parse_mode: 'MarkdownV2' }
                    );
                }
            } catch (error) {
                const msg = error instanceof Error ? error.message : String(error);
                log.telegram.error(`Error for user ${userId}: ${msg}`);
                await ctx.reply(`❌ Error: ${msg}`);
            }
        });

        this.bot.catch((err) => {
            log.telegram.error(`Bot error: ${err.message}`);
        });
    }

    // ─── Proactive Pinger ─────────────────────────────────────────

    private startPinger(self: TelegramAdapter): void {
        const owl = self.gateway.getOwl();
        const config = self.gateway.getConfig();

        this.pinger = new ProactivePinger({
            provider: self.gateway.getProvider(),
            owl,
            config,
            capabilityLedger: self.gateway.getCapabilityLedger()!,
            learningEngine: self.gateway.getLearningEngine(),
            preferenceStore: self.gateway.getPreferenceStore(),
            reflexionEngine: self.gateway.getReflexionEngine(),
            toolRegistry: self.gateway.getToolRegistry(),
            sendToUser: async (message: string) => {
                await self.broadcast({
                    content: message,
                    owlName: owl.persona.name,
                    owlEmoji: owl.persona.emoji,
                    toolsUsed: [],
                });
            },
        });
        this.pinger.start();
    }

    // ─── Streaming (edit-in-place) ──────────────────────────────────

    /**
     * Creates a stream handler and returns both the handler function and a
     * status object that tracks whether streaming successfully delivered content.
     *
     * `status.streamedContent` holds the text that was actually streamed to the
     * user (text_delta only — tool status messages are NOT counted). After the
     * gateway returns, the caller compares this against the final response to
     * decide whether `sendResponse` is needed.
     */
    private createStreamHandler(ctx: Context): {
        handler: (event: StreamEvent) => Promise<void>;
        status: { streamedContent: string };
        /** Push a tool status line into the streaming message (edit-in-place). */
        pushToolStatus: (msg: string) => void;
    } {
        const chatId = ctx.chat?.id;
        const status = { streamedContent: '' };
        if (!chatId) return { handler: async () => {}, status, pushToolStatus: () => {} };

        let messageId: number | null = null;
        let displayText = '';   // Full text shown in Telegram (includes tool status)
        let pureContent = '';   // Only text_delta content (no tool noise)
        let lastEditTime = 0;
        let pendingEdit: ReturnType<typeof setTimeout> | null = null;
        let hasToolStatus = false; // Track if we've shown tool status lines
        let contentStarted = false; // Track if actual content has started
        const THROTTLE_MS = 1000;

        const flushEdit = async () => {
            if (!messageId || !displayText) return;
            try {
                await this.bot.api.editMessageText(
                    chatId,
                    messageId,
                    this.escHtml(displayText),
                    { parse_mode: 'HTML' },
                );
                lastEditTime = Date.now();
            } catch {
                // Edit may fail if content unchanged or message too old — non-fatal
            }
        };

        const handler = async (event: StreamEvent) => {
            switch (event.type) {
                case 'text_delta': {
                    // Strip internal [DONE] signal — it's an engine marker, not user content
                    const chunk = event.content.replace(/\[DONE\]/g, '');
                    if (!chunk) break;

                    // Insert a separator between tool status and response content
                    if (hasToolStatus && !contentStarted) {
                        displayText += '\n\n';
                        contentStarted = true;
                    }
                    displayText += chunk;
                    pureContent += chunk;

                    if (!messageId) {
                        // Send initial message
                        try {
                            const sent = await this.bot.api.sendMessage(
                                chatId,
                                this.escHtml(displayText) || '...',
                                { parse_mode: 'HTML' },
                            );
                            messageId = sent.message_id;
                            lastEditTime = Date.now();
                        } catch {
                            // If initial send fails, streaming will fall back to final response
                        }
                        return;
                    }

                    // Throttled edit
                    const elapsed = Date.now() - lastEditTime;
                    if (elapsed >= THROTTLE_MS) {
                        if (pendingEdit) { clearTimeout(pendingEdit); pendingEdit = null; }
                        await flushEdit();
                    } else if (!pendingEdit) {
                        pendingEdit = setTimeout(async () => {
                            pendingEdit = null;
                            await flushEdit();
                        }, THROTTLE_MS - elapsed);
                    }
                    break;
                }
                case 'tool_start': {
                    // Skip — tool execution status is handled by onProgress
                    // via pushToolStatus to avoid duplicate lines.
                    break;
                }
                case 'tool_end': {
                    // Skip — handled by onProgress via pushToolStatus.
                    break;
                }
                case 'done': {
                    // Strip [DONE] from display text — it may have been split across
                    // multiple text_delta chunks, bypassing the per-chunk regex.
                    displayText = displayText.replace(/\[DONE\]/g, '').trimEnd();
                    pureContent = pureContent.replace(/\[DONE\]/g, '').trimEnd();

                    // Final flush to ensure all accumulated text is shown
                    if (pendingEdit) { clearTimeout(pendingEdit); pendingEdit = null; }
                    await flushEdit();
                    // Record what text_delta content was streamed (not tool status noise)
                    if (messageId && pureContent.length > 0) {
                        status.streamedContent = pureContent;
                    }
                    break;
                }
            }
        };

        /**
         * Push a tool execution status line into the streaming message.
         * Called from onProgress for tool events so they appear inline
         * (edit-in-place) instead of as separate Telegram messages.
         */
        const pushToolStatus = (msg: string) => {
            // Clean markdown bold/code for plain display
            const clean = msg
                .replace(/\*\*(.+?)\*\*/g, '$1')
                .replace(/`(.+?)`/g, '$1');
            displayText += `\n${clean}`;
            hasToolStatus = true;
            flushEdit().catch(() => {});

            // If no streaming message exists yet, create one
            if (!messageId) {
                this.bot.api.sendMessage(
                    chatId!,
                    this.escHtml(displayText) || '...',
                    { parse_mode: 'HTML' },
                ).then(sent => {
                    messageId = sent.message_id;
                    lastEditTime = Date.now();
                }).catch(() => {});
            }
        };

        return { handler, status, pushToolStatus };
    }

    // ─── Response formatting ──────────────────────────────────────

    private async sendResponse(ctx: Context, response: GatewayResponse): Promise<void> {
        const chatId = ctx.chat?.id;
        if (!chatId) return;
        const formatted = this.formatResponse(response);
        await this.sendChunked(chatId, formatted);
    }

    private formatResponse(response: GatewayResponse): string {
        return `${response.owlEmoji} *${this.esc(response.owlName)}*\n\n${this.esc(response.content)}`;
    }

    private async sendChunked(chatId: number, text: string): Promise<void> {
        if (text.length <= 4096) {
            await this.bot.api.sendMessage(chatId, text, { parse_mode: 'MarkdownV2' });
            return;
        }

        const chunks = this.splitMessage(text, 3800);
        const MAX_CHUNKS = 5;
        for (let i = 0; i < Math.min(chunks.length, MAX_CHUNKS); i++) {
            await this.bot.api.sendMessage(chatId, chunks[i], { parse_mode: 'MarkdownV2' });
            if (i < Math.min(chunks.length, MAX_CHUNKS) - 1) {
                await new Promise(r => setTimeout(r, 1000));
            }
        }
        if (chunks.length > MAX_CHUNKS) {
            await this.bot.api.sendMessage(
                chatId,
                `_...[${chunks.length - MAX_CHUNKS} chunks omitted]..._`,
                { parse_mode: 'MarkdownV2' }
            );
        }
    }

    private splitMessage(text: string, maxLen: number): string[] {
        const chunks: string[] = [];
        let remaining = text;
        while (remaining.length > 0) {
            if (remaining.length <= maxLen) { chunks.push(remaining); break; }
            let splitAt = remaining.lastIndexOf('\n', maxLen);
            if (splitAt === -1 || splitAt < maxLen / 2) splitAt = remaining.lastIndexOf(' ', maxLen);
            if (splitAt === -1 || splitAt < maxLen / 2) splitAt = maxLen;
            chunks.push(remaining.substring(0, splitAt));
            remaining = remaining.substring(splitAt).trimStart();
        }
        return chunks;
    }

    // ─── Helpers ─────────────────────────────────────────────────

    private isAllowed(ctx: Context): boolean {
        const userId = ctx.from?.id;
        if (!userId) return false;
        if (!this.config.allowedUserIds?.length) return true;
        const allowed = this.config.allowedUserIds.includes(userId);
        if (!allowed) ctx.reply('🔒 Not authorized.').catch(() => { });
        return allowed;
    }

    private getUserState(userId: number): UserState {
        if (!this.userState.has(userId)) this.userState.set(userId, {});
        return this.userState.get(userId)!;
    }

    private trackChat(chatId: number): void {
        if (!this.activeChatIds.has(chatId)) {
            this.activeChatIds.add(chatId);
            this.saveChatIds().catch(() => { });
        }
    }

    private async loadChatIds(): Promise<void> {
        if (!existsSync(this.chatIdsPath)) return;
        try {
            const ids: number[] = JSON.parse(await readFile(this.chatIdsPath, 'utf-8'));
            for (const id of ids) this.activeChatIds.add(id);
            log.telegram.info(`Loaded ${ids.length} known chat ID(s)`);
        } catch { /* non-fatal */ }
    }

    private async saveChatIds(): Promise<void> {
        try {
            const dir = join(this.chatIdsPath, '..');
            if (!existsSync(dir)) await mkdir(dir, { recursive: true });
            await writeFile(this.chatIdsPath, JSON.stringify([...this.activeChatIds]), 'utf-8');
        } catch (err) {
            log.telegram.warn(`Could not persist chat IDs: ${err instanceof Error ? err.message : err}`);
        }
    }

    /** Escape special characters for Telegram MarkdownV2. */
    private esc(text: string): string {
        return text.replace(/([_*\[\]()~`>#+\-=|{}.!\\])/g, '\\$1');
    }

    /** Escape for Telegram HTML mode. */
    private escHtml(text: string): string {
        return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
}

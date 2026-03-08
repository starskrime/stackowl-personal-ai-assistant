/**
 * StackOwl — Telegram Bot Channel
 *
 * Connects StackOwl to Telegram via grammY.
 * Users can chat with their owl through a Telegram bot.
 */

import { Bot, type Context } from 'grammy';
import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import { OwlEngine } from '../engine/runtime.js';
import { ProactivePinger } from '../heartbeat/proactive.js';

// ─── Types ───────────────────────────────────────────────────────

interface TelegramChannelConfig {
    botToken: string;
    allowedUserIds?: number[];
    provider: ModelProvider;
    owl: OwlInstance;
    model: string;
}

interface UserSession {
    history: ChatMessage[];
    owlName: string;
    lastActivity: number;
}

// ─── Session hygiene constants ───────────────────────────────────

const MAX_SESSION_HISTORY = 50;
const SESSION_TIMEOUT_MS = 2 * 60 * 60 * 1000; // 2 hours

// ─── Telegram Channel ────────────────────────────────────────────

export class TelegramChannel {
    private bot: Bot;
    private engine: OwlEngine;
    private config: TelegramChannelConfig;
    private sessions: Map<number, UserSession> = new Map();
    private pinger: ProactivePinger | null = null;
    private activeChatIds: Set<number> = new Set();

    constructor(config: TelegramChannelConfig) {
        if (!config.botToken || config.botToken.trim() === '') {
            throw new Error('[TelegramChannel] Bot token is required. Get one from @BotFather on Telegram.');
        }

        this.config = config;
        this.engine = new OwlEngine();
        this.bot = new Bot(config.botToken);

        this.setupHandlers();
    }

    /**
     * Set up bot message handlers.
     */
    private setupHandlers(): void {
        // /start command
        this.bot.command('start', async (ctx) => {
            if (!this.isAllowed(ctx)) return;

            const owl = this.config.owl;
            await ctx.reply(
                `${owl.persona.emoji} *${owl.persona.name}* reporting for duty\\!\n\n` +
                `I'm your personal executive assistant\\. ` +
                `I'll be proactively pinging you with reminders, ideas, and follow\\-ups\\.\n\n` +
                `Just talk to me naturally — I'll handle the rest\\. 🦉`,
                { parse_mode: 'MarkdownV2' }
            );

            // Track this chat for proactive pinging
            this.activeChatIds.add(ctx.chat.id);
        });

        // /owls command
        this.bot.command('owls', async (ctx) => {
            if (!this.isAllowed(ctx)) return;

            const owl = this.config.owl;
            let msg = `🦉 *Active Owl*\n\n`;
            msg += `${owl.persona.emoji} *${this.escapeMarkdown(owl.persona.name)}* — ${this.escapeMarkdown(owl.persona.type)}\n`;
            msg += `Challenge: ${owl.dna.evolvedTraits.challengeLevel}\n`;
            msg += `Specialties: ${owl.persona.specialties.map(s => this.escapeMarkdown(s)).join(', ')}\n`;
            msg += `DNA Generation: ${owl.dna.generation}`;

            await ctx.reply(msg, { parse_mode: 'MarkdownV2' });
        });

        // /reset command — clear session history
        this.bot.command('reset', async (ctx) => {
            if (!this.isAllowed(ctx)) return;

            const userId = ctx.from?.id;
            if (userId) {
                this.sessions.delete(userId);
            }
            await ctx.reply('🔄 Session reset. Starting fresh.');
        });

        // /status command
        this.bot.command('status', async (ctx) => {
            if (!this.isAllowed(ctx)) return;

            const userId = ctx.from?.id;
            const session = userId ? this.sessions.get(userId) : undefined;

            let msg = `🦉 *StackOwl Status*\n\n`;
            msg += `Provider: ${this.escapeMarkdown(this.config.provider.name)}\n`;
            msg += `Model: ${this.escapeMarkdown(this.config.model)}\n`;
            msg += `Owl: ${this.config.owl.persona.emoji} ${this.escapeMarkdown(this.config.owl.persona.name)}\n`;
            msg += `Session messages: ${session?.history.length ?? 0}`;

            await ctx.reply(msg, { parse_mode: 'MarkdownV2' });
        });

        // Handle text messages
        this.bot.on('message:text', async (ctx) => {
            if (!this.isAllowed(ctx)) return;

            const userId = ctx.from?.id;
            if (!userId) return;

            const text = ctx.message.text;
            if (!text || text.startsWith('/')) return;

            // Get or create session
            const session = this.getOrCreateSession(userId);

            // Track this chat for proactive pinging
            this.activeChatIds.add(ctx.chat.id);

            // Show typing indicator
            await ctx.api.sendChatAction(ctx.chat.id, 'typing');

            try {
                const response = await this.engine.run(text, {
                    provider: this.config.provider,
                    owl: this.config.owl,
                    sessionHistory: session.history,
                    model: this.config.model,
                });

                // Update session history
                session.history.push({ role: 'user', content: text });
                session.history.push({ role: 'assistant', content: response.content });
                session.lastActivity = Date.now();

                // Trim session if too long
                if (session.history.length > MAX_SESSION_HISTORY) {
                    session.history = session.history.slice(-MAX_SESSION_HISTORY);
                }

                // Format and send response
                const header = `${response.owlEmoji} *${this.escapeMarkdown(response.owlName)}*\n\n`;
                const fullMessage = header + this.escapeMarkdown(response.content);

                // Telegram has a 4096 char limit — split if needed
                if (fullMessage.length <= 4096) {
                    await ctx.reply(fullMessage, { parse_mode: 'MarkdownV2' });
                } else {
                    // Send in chunks
                    const chunks = this.splitMessage(response.content, 3800);
                    for (let i = 0; i < chunks.length; i++) {
                        const prefix = i === 0 ? header : '';
                        await ctx.reply(prefix + this.escapeMarkdown(chunks[i]), {
                            parse_mode: 'MarkdownV2',
                        });
                    }
                }

                // Show token usage as a subtle footer
                if (response.usage) {
                    await ctx.reply(
                        `_${response.usage.promptTokens}→${response.usage.completionTokens} tokens_`,
                        { parse_mode: 'MarkdownV2' }
                    );
                }
            } catch (error) {
                const msg = error instanceof Error ? error.message : String(error);
                console.error(`[TelegramChannel] Error for user ${userId}:`, msg);
                await ctx.reply(`❌ Error: ${msg}`);
            }
        });

        // Error handler
        this.bot.catch((err) => {
            console.error('[TelegramChannel] Bot error:', err.message);
        });
    }

    /**
     * Check if a user is allowed to interact with the bot.
     */
    private isAllowed(ctx: Context): boolean {
        const userId = ctx.from?.id;
        if (!userId) return false;

        // If no allowlist is set, allow everyone
        if (!this.config.allowedUserIds || this.config.allowedUserIds.length === 0) {
            return true;
        }

        const allowed = this.config.allowedUserIds.includes(userId);
        if (!allowed) {
            ctx.reply('🔒 You are not authorized to use this bot.').catch(() => { });
        }
        return allowed;
    }

    /**
     * Get or create a user's session.
     */
    private getOrCreateSession(userId: number): UserSession {
        let session = this.sessions.get(userId);

        if (!session || Date.now() - session.lastActivity > SESSION_TIMEOUT_MS) {
            session = {
                history: [],
                owlName: this.config.owl.persona.name,
                lastActivity: Date.now(),
            };
            this.sessions.set(userId, session);
        }

        return session;
    }

    /**
     * Split a long message into chunks.
     */
    private splitMessage(text: string, maxLen: number): string[] {
        const chunks: string[] = [];
        let remaining = text;

        while (remaining.length > 0) {
            if (remaining.length <= maxLen) {
                chunks.push(remaining);
                break;
            }

            // Try to split at a natural boundary
            let splitAt = remaining.lastIndexOf('\n', maxLen);
            if (splitAt === -1 || splitAt < maxLen / 2) {
                splitAt = remaining.lastIndexOf(' ', maxLen);
            }
            if (splitAt === -1 || splitAt < maxLen / 2) {
                splitAt = maxLen;
            }

            chunks.push(remaining.substring(0, splitAt));
            remaining = remaining.substring(splitAt).trimStart();
        }

        return chunks;
    }

    /**
     * Escape special characters for Telegram MarkdownV2.
     */
    private escapeMarkdown(text: string): string {
        return text.replace(/([_*\[\]()~`>#+\-=|{}.!\\])/g, '\\$1');
    }

    /**
     * Start the bot (long polling).
     */
    async start(): Promise<void> {
        console.log(`[TelegramChannel] 🤖 Bot starting...`);

        try {
            const me = await this.bot.api.getMe();
            console.log(`[TelegramChannel] ✓ Connected as @${me.username}`);
            console.log(`[TelegramChannel] ✓ Owl: ${this.config.owl.persona.emoji} ${this.config.owl.persona.name}`);

            await this.bot.start({
                onStart: () => {
                    console.log('[TelegramChannel] ✓ Bot is running. Send /start in Telegram.');

                    // Start proactive pinger
                    this.pinger = new ProactivePinger(
                        {
                            provider: this.config.provider,
                            owl: this.config.owl,
                            model: this.config.model,
                            sendToUser: async (message: string) => {
                                // Send to all active chats
                                for (const chatId of this.activeChatIds) {
                                    try {
                                        const owl = this.config.owl;
                                        const header = `${owl.persona.emoji} *${this.escapeMarkdown(owl.persona.name)}*\n\n`;
                                        await this.bot.api.sendMessage(
                                            chatId,
                                            header + this.escapeMarkdown(message),
                                            { parse_mode: 'MarkdownV2' }
                                        );
                                    } catch (err) {
                                        const errMsg = err instanceof Error ? err.message : String(err);
                                        console.error(`[TelegramChannel] Failed to send proactive ping to ${chatId}: ${errMsg}`);
                                        // Remove invalid chat IDs
                                        this.activeChatIds.delete(chatId);
                                    }
                                }
                            },
                            getRecentHistory: () => {
                                // Get the most recent session's history for context
                                const sessions = Array.from(this.sessions.values());
                                if (sessions.length === 0) return [];
                                const latest = sessions.sort((a, b) => b.lastActivity - a.lastActivity)[0];
                                return latest ? latest.history : [];
                            },
                        },
                    );
                    this.pinger.start();
                },
            });
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            throw new Error(`[TelegramChannel] Failed to start bot: ${msg}`);
        }
    }

    /**
     * Stop the bot gracefully.
     */
    stop(): void {
        if (this.pinger) {
            this.pinger.stop();
        }
        this.bot.stop();
        console.log('[TelegramChannel] Bot stopped.');
    }
}

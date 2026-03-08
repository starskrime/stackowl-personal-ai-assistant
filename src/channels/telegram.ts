/**
 * StackOwl — Telegram Bot Channel
 *
 * Connects StackOwl to Telegram via grammY.
 * Users can chat with their owl through a Telegram bot.
 */

import { Bot, type Context } from 'grammy';
import type { ModelProvider } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import { OwlEngine } from '../engine/runtime.js';
import { ProactivePinger } from '../heartbeat/proactive.js';
import type { ToolRegistry } from '../tools/registry.js';
import type { SessionStore, Session } from '../memory/store.js';
import type { StackOwlConfig } from '../config/loader.js';
import { EvolutionHandler, type ToolProposal } from '../evolution/handler.js';

// ─── Types ───────────────────────────────────────────────────────

export interface TelegramChannelConfig {
    botToken: string;
    allowedUserIds?: number[];
    provider: ModelProvider;
    owl: OwlInstance;
    config: StackOwlConfig;
    toolRegistry?: ToolRegistry;
    sessionStore: SessionStore;
    cwd?: string;
    evolution?: EvolutionHandler;
}

interface PendingApproval {
    proposal: ToolProposal;
    originalMessage: string;
}


interface UserSession {
    session: Session;
    lastActivity: number;
    pendingApproval?: PendingApproval;
    /** Resolver waiting for a y/n answer to the npm install question */
    pendingInstallResolve?: (approved: boolean) => void;
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
                const newSession = this.config.sessionStore.createSession(this.config.owl.persona.name);
                newSession.id = `telegram_${userId}`;
                await this.config.sessionStore.saveSession(newSession);
                this.sessions.set(userId, { session: newSession, lastActivity: Date.now() });
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
            msg += `Model: ${this.escapeMarkdown(this.config.config.defaultModel)}\n`;
            msg += `Owl: ${this.config.owl.persona.emoji} ${this.escapeMarkdown(this.config.owl.persona.name)}\n`;
            msg += `Session messages: ${session?.session.messages.length ?? 0}`;

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
            const userSession = await this.getOrCreateSession(userId);

            // Track this chat for proactive pinging
            this.activeChatIds.add(ctx.chat.id);

            // ─── Pending npm install approval ────────────────────────
            if (userSession.pendingInstallResolve) {
                const resolve = userSession.pendingInstallResolve;
                userSession.pendingInstallResolve = undefined;
                const answer = text.trim().toLowerCase();
                resolve(answer === 'yes' || answer === 'y');
                return;
            }

            // ─── Pending tool approval flow ───────────────────────────
            if (userSession.pendingApproval) {
                const { proposal, originalMessage } = userSession.pendingApproval;
                const answer = text.trim().toLowerCase();

                if (answer === 'yes' || answer === 'y') {
                    userSession.pendingApproval = undefined;
                    await ctx.reply(`🔧 Building *${this.escapeMarkdown(proposal.toolName)}*\\.\\.\\.`, { parse_mode: 'MarkdownV2' });
                    await ctx.api.sendChatAction(ctx.chat.id, 'typing');

                    try {
                        if (!this.config.evolution || !this.config.toolRegistry) {
                            await ctx.reply('❌ Self-improvement system not configured on this bot instance.');
                            return;
                        }

                        const engineContext = {
                            provider: this.config.provider,
                            owl: this.config.owl,
                            sessionHistory: userSession.session.messages,
                            config: this.config.config,
                            toolRegistry: this.config.toolRegistry,
                            cwd: this.config.cwd,
                        };

                        const askInstall = async (deps: string[]) => {
                            await ctx.reply(
                                `📦 Install npm deps: \`${this.escapeMarkdown(deps.join(' '))}\`\\?\n\nReply *yes* to install or *no* to skip\\.`,
                                { parse_mode: 'MarkdownV2' }
                            );
                            return new Promise<boolean>((resolve) => {
                                userSession.pendingInstallResolve = resolve;
                            });
                        };

                        const onProgress = async (msg: string) => {
                            await ctx.reply(this.escapeMarkdown(msg), { parse_mode: 'MarkdownV2' });
                        };

                        const { response: retryResponse, depsToInstall, depsInstalled } = await this.config.evolution.buildAndRetry(
                            proposal, originalMessage, engineContext, this.engine, askInstall, onProgress
                        );

                        let confirmMsg = `✅ Tool *${this.escapeMarkdown(proposal.toolName)}* is live\\!\n`;
                        if (depsInstalled) {
                            confirmMsg += `✅ npm deps installed\\.\n`;
                        } else if (depsToInstall.length > 0) {
                            confirmMsg += `⚠️ Deps not installed — run manually: \`npm install ${this.escapeMarkdown(depsToInstall.join(' '))}\`\n`;
                        }
                        confirmMsg += `\n🔄 Retrying your request\\.\\.\\.`;
                        await ctx.reply(confirmMsg, { parse_mode: 'MarkdownV2' });
                        await ctx.api.sendChatAction(ctx.chat.id, 'typing');

                        userSession.session.messages.push({ role: 'user', content: originalMessage });
                        userSession.session.messages.push({ role: 'assistant', content: retryResponse.content });
                        userSession.lastActivity = Date.now();
                        await this.config.sessionStore.saveSession(userSession.session);

                        await this.sendResponse(ctx, retryResponse.owlEmoji, retryResponse.owlName, retryResponse.content);
                    } catch (err) {
                        const msg = err instanceof Error ? err.message : String(err);
                        await ctx.reply(`❌ Synthesis failed: ${msg}`);
                    }
                } else if (answer === 'no' || answer === 'n') {
                    userSession.pendingApproval = undefined;
                    await ctx.reply('↩ Skipped. The owl will work with what it has.');
                } else {
                    await ctx.reply('Reply *yes* to build the tool or *no* to skip\\.', { parse_mode: 'MarkdownV2' });
                }
                return;
            }
            // ─────────────────────────────────────────────────────────

            // Show typing indicator
            await ctx.api.sendChatAction(ctx.chat.id, 'typing');

            try {
                console.log(`[Telegram] ← user ${userId}: "${text.slice(0, 80)}"`);

                const response = await this.engine.run(text, {
                    provider: this.config.provider,
                    owl: this.config.owl,
                    sessionHistory: userSession.session.messages,
                    config: this.config.config,
                    toolRegistry: this.config.toolRegistry,
                    cwd: this.config.cwd,
                });

                console.log(`[Telegram] engine done | gap=${!!response.pendingCapabilityGap} | evolution=${!!this.config.evolution} | tools=${response.toolsUsed.join(',') || 'none'}`);

                // ─── Self-Improvement: Capability Gap Detected ────────
                if (response.pendingCapabilityGap && this.config.evolution) {
                    console.log(`[Telegram] → starting proposal flow for gap: "${response.pendingCapabilityGap.description.slice(0, 60)}"`);
                    const gap = response.pendingCapabilityGap;

                    await this.sendResponse(ctx, response.owlEmoji, response.owlName, response.content);
                    await ctx.reply('🧠 Reasoning about what tool would help\\.\\.\\.', { parse_mode: 'MarkdownV2' });
                    await ctx.api.sendChatAction(ctx.chat.id, 'typing');

                    const engineContext = {
                        provider: this.config.provider,
                        owl: this.config.owl,
                        sessionHistory: userSession.session.messages,
                        config: this.config.config,
                        toolRegistry: this.config.toolRegistry,
                        cwd: this.config.cwd,
                    };
                    const proposal = await this.config.evolution.designSpec(gap, engineContext);

                    // Format and send the proposal
                    const params = proposal.parameters.length > 0
                        ? proposal.parameters.map(p => `  • ${p.name} \\(${p.type}\\) — ${this.escapeMarkdown(p.description)}`).join('\n')
                        : '  _none_';
                    const deps = proposal.dependencies.length > 0
                        ? this.escapeMarkdown(proposal.dependencies.join(', '))
                        : '_none_';

                    const proposalMsg =
                        `⚡ *Capability Gap — Tool Proposal*\n` +
                        `─────────────────────────────\n` +
                        `*Tool name:* \`${this.escapeMarkdown(proposal.toolName)}\`\n` +
                        `*What it does:* ${this.escapeMarkdown(proposal.description)}\n` +
                        `*Parameters:*\n${params}\n` +
                        `*npm deps:* ${deps}\n` +
                        `*Safety:* ${this.escapeMarkdown(proposal.safetyNote)}\n` +
                        `*Why:* ${this.escapeMarkdown(proposal.rationale)}\n` +
                        `*File:* \`src/tools/synthesized/${this.escapeMarkdown(proposal.toolName)}\\.ts\`\n` +
                        `─────────────────────────────\n` +
                        `Reply *yes* to build this tool or *no* to skip\\.`;

                    await ctx.reply(proposalMsg, { parse_mode: 'MarkdownV2' });

                    // Store pending approval — next message will be the answer
                    userSession.pendingApproval = { proposal, originalMessage: text };
                    userSession.lastActivity = Date.now();
                    return;
                }
                // ─────────────────────────────────────────────────────

                // Update session history
                userSession.session.messages.push({ role: 'user', content: text });
                userSession.session.messages.push({ role: 'assistant', content: response.content });
                userSession.lastActivity = Date.now();

                // Trim session if too long
                if (userSession.session.messages.length > MAX_SESSION_HISTORY) {
                    userSession.session.messages = userSession.session.messages.slice(-MAX_SESSION_HISTORY);
                }

                // Save to disk
                await this.config.sessionStore.saveSession(userSession.session);

                await this.sendResponse(ctx, response.owlEmoji, response.owlName, response.content);

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
    private async getOrCreateSession(userId: number): Promise<UserSession> {
        let userSession = this.sessions.get(userId);

        if (!userSession || Date.now() - userSession.lastActivity > SESSION_TIMEOUT_MS) {
            // Try loading from disk
            const sessionId = `telegram_${userId}`;
            let loadedSession = await this.config.sessionStore.loadSession(sessionId);

            if (!loadedSession) {
                loadedSession = this.config.sessionStore.createSession(this.config.owl.persona.name);
                loadedSession.id = sessionId;
                await this.config.sessionStore.saveSession(loadedSession);
            }

            userSession = {
                session: loadedSession,
                lastActivity: Date.now(),
            };
            this.sessions.set(userId, userSession);
        }

        return userSession;
    }

    /**
     * Send an owl response, chunking if needed to stay within Telegram's 4096 char limit.
     */
    private async sendResponse(ctx: Context, emoji: string, name: string, content: string): Promise<void> {
        const header = `${emoji} *${this.escapeMarkdown(name)}*\n\n`;
        const fullMessage = header + this.escapeMarkdown(content);

        if (fullMessage.length <= 4096) {
            await ctx.reply(fullMessage, { parse_mode: 'MarkdownV2' });
        } else {
            const chunks = this.splitMessage(content, 3800);
            for (let i = 0; i < chunks.length; i++) {
                const prefix = i === 0 ? header : '';
                await ctx.reply(prefix + this.escapeMarkdown(chunks[i]), { parse_mode: 'MarkdownV2' });
            }
        }
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
     * Broadcast a proactive message to all active chats (e.g. from a Perch point)
     */
    async broadcastProactiveMessage(message: string): Promise<void> {
        const owl = this.config.owl;
        const header = `${owl.persona.emoji} *${this.escapeMarkdown(owl.persona.name)}*\n\n`;
        const formatted = header + this.escapeMarkdown(message);

        for (const chatId of this.activeChatIds) {
            try {
                await this.bot.api.sendMessage(chatId, formatted, { parse_mode: 'MarkdownV2' });
            } catch (err) {
                const errMsg = err instanceof Error ? err.message : String(err);
                console.error(`[TelegramChannel] Failed to broadcast to ${chatId}: ${errMsg}`);
                this.activeChatIds.delete(chatId);
            }
        }
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
                            config: this.config.config,
                            sendToUser: async (message: string) => {
                                await this.broadcastProactiveMessage(message);
                            },
                            getRecentHistory: () => {
                                // Get the most recent session's history for context
                                const userSessions = Array.from(this.sessions.values());
                                if (userSessions.length === 0) return [];
                                const latest = userSessions.sort((a, b) => b.lastActivity - a.lastActivity)[0];
                                return latest ? latest.session.messages : [];
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

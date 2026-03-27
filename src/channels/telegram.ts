/**
 * StackOwl — Telegram Bot Channel
 *
 * Connects StackOwl to Telegram via grammY.
 * Users can chat with their owl through a Telegram bot.
 */

import { Bot, InputFile, type Context } from "grammy";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, extname } from "node:path";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import { OwlEngine } from "../engine/runtime.js";
import { ProactivePinger } from "../heartbeat/proactive.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { SessionStore, Session } from "../memory/store.js";
import type { StackOwlConfig } from "../config/loader.js";
import { EvolutionHandler, type ToolProposal } from "../evolution/handler.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { LearningEngine } from "../learning/self-study.js";
import type { LearningOrchestrator } from "../learning/orchestrator.js";
import type { PreferenceStore } from "../preferences/store.js";
import { PreferenceDetector } from "../preferences/detector.js";
import { AttemptLogRegistry } from "../memory/attempt-log.js";
import { log } from "../logger.js";

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
  capabilityLedger?: CapabilityLedger;
  learningEngine?: LearningEngine;
  learningOrchestrator?: LearningOrchestrator;
  preferenceStore?: PreferenceStore;
  /** When true, show token usage after each response (default: false) */
  showTokenUsage?: boolean;
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
  private chatIdsPath: string;

  /**
   * Lane Queue — one active Promise per userId.
   * Serializes messages so rapid sends don't cause race conditions on session state.
   */
  private lanes: Map<number, Promise<unknown>> = new Map();

  /**
   * Cross-turn attempt logs — one per active user.
   * Gives the model memory of what failed in earlier messages of the same conversation.
   */
  private attemptLogs = new AttemptLogRegistry();

  /** Preference detector — reused across messages (avoids re-constructing the provider ref) */
  private preferenceDetector: PreferenceDetector | null = null;

  constructor(config: TelegramChannelConfig) {
    if (!config.botToken || config.botToken.trim() === "") {
      throw new Error(
        "[TelegramChannel] Bot token is required. Get one from @BotFather on Telegram.",
      );
    }

    this.config = config;
    this.engine = new OwlEngine();
    this.bot = new Bot(config.botToken);
    this.chatIdsPath = join(config.cwd ?? process.cwd(), "known_chat_ids.json");

    if (config.preferenceStore) {
      this.preferenceDetector = new PreferenceDetector(config.provider);
    }

    // Evict stale sessions every 30 minutes so the Map doesn't grow forever
    setInterval(() => this.evictStaleSessions(), 30 * 60 * 1000).unref();

    this.setupHandlers();
  }

  /** Remove sessions that haven't been active within SESSION_TIMEOUT_MS */
  private evictStaleSessions(): void {
    const now = Date.now();
    for (const [userId, session] of this.sessions) {
      if (now - session.lastActivity > SESSION_TIMEOUT_MS) {
        this.sessions.delete(userId);
        this.attemptLogs.delete(`telegram_${userId}`);
        log.telegram.info(
          `[session-evict] Evicted stale session for user ${userId}`,
        );
      }
    }
  }

  /** Load persisted chat IDs from disk into activeChatIds */
  private async loadChatIds(): Promise<void> {
    if (!existsSync(this.chatIdsPath)) return;
    try {
      const raw = await readFile(this.chatIdsPath, "utf-8");
      const ids: number[] = JSON.parse(raw);
      for (const id of ids) this.activeChatIds.add(id);
      log.telegram.info(`Loaded ${ids.length} known chat ID(s)`);
    } catch {
      // Non-fatal — file may be malformed
    }
  }

  /** Persist current activeChatIds to disk */
  private async saveChatIds(): Promise<void> {
    try {
      const dir = join(this.chatIdsPath, "..");
      if (!existsSync(dir)) await mkdir(dir, { recursive: true });
      await writeFile(
        this.chatIdsPath,
        JSON.stringify([...this.activeChatIds]),
        "utf-8",
      );
    } catch (err) {
      log.telegram.warn(
        `Could not persist chat IDs: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * Set up bot message handlers.
   */
  private setupHandlers(): void {
    // /start command
    this.bot.command("start", async (ctx) => {
      if (!this.isAllowed(ctx)) return;

      const owl = this.config.owl;
      await ctx.reply(
        `${owl.persona.emoji} *${owl.persona.name}* reporting for duty\\!\n\n` +
          `I'm your personal executive assistant\\. ` +
          `I'll be proactively pinging you with reminders, ideas, and follow\\-ups\\.\n\n` +
          `Just talk to me naturally — I'll handle the rest\\. 🦉`,
        { parse_mode: "MarkdownV2" },
      );

      // Track this chat for proactive pinging and persist for restarts
      this.activeChatIds.add(ctx.chat!.id);
      this.saveChatIds().catch(() => {});
    });

    // /owls command
    this.bot.command("owls", async (ctx) => {
      if (!this.isAllowed(ctx)) return;

      const owl = this.config.owl;
      let msg = `🦉 *Active Owl*\n\n`;
      msg += `${owl.persona.emoji} *${this.escapeMarkdown(owl.persona.name)}* — ${this.escapeMarkdown(owl.persona.type)}\n`;
      msg += `Challenge: ${owl.dna.evolvedTraits.challengeLevel}\n`;
      msg += `Specialties: ${owl.persona.specialties.map((s) => this.escapeMarkdown(s)).join(", ")}\n`;
      msg += `DNA Generation: ${owl.dna.generation}`;

      await ctx.reply(msg, { parse_mode: "MarkdownV2" });
    });

    // /help command — show available commands
    this.bot.command("help", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      const owl = this.config.owl;
      await ctx.reply(
        `${owl.persona.emoji} *${this.escapeMarkdown(owl.persona.name)}* — available commands:\n\n` +
          `/start — introduce the owl\n` +
          `/help — show this message\n` +
          `/reset — clear conversation history and start fresh\n` +
          `/status — show provider, model, and session info\n` +
          `/owls — show owl DNA and traits\n` +
          `/skill \\<name\\> — invoke a named skill directly\n\n` +
          `_Just chat naturally — no commands needed for most things\\._`,
        { parse_mode: "MarkdownV2" },
      );
    });

    // /reset command — clear session history
    this.bot.command("reset", async (ctx) => {
      if (!this.isAllowed(ctx)) return;

      const userId = ctx.from?.id;
      if (userId) {
        this.sessions.delete(userId);
        const newSession = this.config.sessionStore.createSession(
          this.config.owl.persona.name,
        );
        newSession.id = `telegram_${userId}`;
        await this.config.sessionStore.saveSession(newSession);
        this.sessions.set(userId, {
          session: newSession,
          lastActivity: Date.now(),
        });
      }
      await ctx.reply("🔄 Session reset. Starting fresh.");
    });

    // /status command
    this.bot.command("status", async (ctx) => {
      if (!this.isAllowed(ctx)) return;

      const userId = ctx.from?.id;
      const session = userId ? this.sessions.get(userId) : undefined;

      let msg = `🦉 *StackOwl Status*\n\n`;
      msg += `Provider: ${this.escapeMarkdown(this.config.provider.name)}\n`;
      msg += `Model: ${this.escapeMarkdown(this.config.config.defaultModel)}\n`;
      msg += `Owl: ${this.config.owl.persona.emoji} ${this.escapeMarkdown(this.config.owl.persona.name)}\n`;
      msg += `Session messages: ${session?.session.messages.length ?? 0}`;

      await ctx.reply(msg, { parse_mode: "MarkdownV2" });
    });

    // Handle text messages — serialized per user via Lane Queue
    this.bot.on("message:text", async (ctx) => {
      if (!this.isAllowed(ctx)) return;

      const userId = ctx.from?.id;
      if (!userId) return;

      const text = ctx.message.text;
      if (!text || text.startsWith("/")) return;

      // Lane Queue: serialize messages from the same user to prevent race conditions
      const prev = this.lanes.get(userId) ?? Promise.resolve();
      const next = prev.then(() => this.handleMessageInLane(ctx, userId, text));
      this.lanes.set(
        userId,
        next.catch(() => {}),
      );
      await next;
    });

    // Error handler
    this.bot.catch((err) => {
      log.telegram.error(`Bot error: ${err.message}`);
    });
  }

  /**
   * Process a message inside the lane queue — safe to mutate session state here.
   */
  private async handleMessageInLane(
    ctx: Context,
    userId: number,
    text: string,
  ): Promise<void> {
    // Get or create session
    const userSession = await this.getOrCreateSession(userId);

    // Track this chat for proactive pinging and persist for restarts
    if (!this.activeChatIds.has(ctx.chat!.id)) {
      this.activeChatIds.add(ctx.chat!.id);
      this.saveChatIds().catch(() => {});
    }

    // ─── Pending npm install approval ────────────────────────
    if (userSession.pendingInstallResolve) {
      const resolve = userSession.pendingInstallResolve;
      userSession.pendingInstallResolve = undefined;
      const answer = text.trim().toLowerCase();
      resolve(answer === "yes" || answer === "y");
      return;
    }

    // ─── Pending tool approval flow ───────────────────────────
    if (userSession.pendingApproval) {
      const { proposal, originalMessage } = userSession.pendingApproval;
      const answer = text.trim().toLowerCase();

      if (answer === "yes" || answer === "y") {
        userSession.pendingApproval = undefined;
        await ctx.reply(
          `🔧 Building <code>${this.escapeHtml(proposal.toolName)}</code>...`,
          { parse_mode: "HTML" },
        );
        await ctx.api.sendChatAction(ctx.chat!.id, "typing");

        try {
          if (!this.config.evolution || !this.config.toolRegistry) {
            await ctx.reply(
              "❌ Self-improvement system not configured on this bot instance.",
              { parse_mode: "HTML" },
            );
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
              `📦 Install npm deps: <code>${this.escapeHtml(deps.join(" "))}</code>\n\nReply <b>yes</b> to install or <b>no</b> to skip.`,
              { parse_mode: "HTML" },
            );
            return new Promise<boolean>((resolve) => {
              userSession.pendingInstallResolve = resolve;
            });
          };

          const onProgress = async (msg: string) => {
            // Plain text — progress messages may contain any characters
            await ctx.reply(msg);
          };

          const {
            response: retryResponse,
            depsToInstall,
            depsInstalled,
          } = await this.config.evolution.buildAndRetry(
            proposal,
            originalMessage,
            engineContext,
            this.engine,
            askInstall,
            onProgress,
          );

          let confirmMsg = `✅ Tool <code>${this.escapeHtml(proposal.toolName)}</code> is live!\n`;
          if (depsInstalled) {
            confirmMsg += `✅ npm deps installed.\n`;
          } else if (depsToInstall.length > 0) {
            confirmMsg += `⚠️ Deps not installed — run manually: <code>npm install ${this.escapeHtml(depsToInstall.join(" "))}</code>\n`;
          }
          confirmMsg += `\n🔄 Retrying your request...`;
          await ctx.reply(confirmMsg, { parse_mode: "HTML" });
          await ctx.api.sendChatAction(ctx.chat!.id, "typing");

          userSession.session.messages.push({
            role: "user",
            content: originalMessage,
          });
          userSession.session.messages.push({
            role: "assistant",
            content: retryResponse.content,
          });
          userSession.lastActivity = Date.now();
          await this.config.sessionStore.saveSession(userSession.session);

          await this.sendResponse(
            ctx,
            retryResponse.owlEmoji,
            retryResponse.owlName,
            retryResponse.content,
          );
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          await ctx.reply(`❌ Synthesis failed: ${msg}`);
        }
      } else if (answer === "no" || answer === "n") {
        userSession.pendingApproval = undefined;
        await ctx.reply("↩ Skipped. The owl will work with what it has.");
      } else {
        await ctx.reply(
          "Reply <b>yes</b> to build the tool or <b>no</b> to skip.",
          { parse_mode: "HTML" },
        );
      }
      return;
    }
    // ─────────────────────────────────────────────────────────

    // Show typing indicator
    await ctx.api.sendChatAction(ctx.chat!.id, "typing");

    try {
      log.telegram.incoming(`user:${userId}`, text);
      log.telegram.separator();

      // Signal new turn to the attempt log before running the engine
      const sessionKey = `telegram_${userId}`;
      const attemptLog = this.attemptLogs.get(sessionKey);
      attemptLog.newTurn();

      const response = await this.engine.run(text, {
        provider: this.config.provider,
        owl: this.config.owl,
        sessionHistory: userSession.session.messages,
        config: this.config.config,
        toolRegistry: this.config.toolRegistry,
        capabilityLedger: this.config.capabilityLedger,
        cwd: this.config.cwd,
        attemptLog,
        onProgress: async (msg: string) => {
          try {
            // Convert simple markdown patterns to HTML — much safer than Markdown/MarkdownV2
            // which chokes on unbalanced backticks or asterisks from tool output
            const html = this.escapeHtml(msg)
              .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
              .replace(/`(.+?)`/g, "<code>$1</code>")
              .replace(/^_(.+)_$/gm, "<i>$1</i>");
            await ctx.reply(html, { parse_mode: "HTML" });
            await ctx.api.sendChatAction(ctx.chat!.id, "typing");
          } catch (err) {
            log.telegram.warn(
              `onProgress send failed: ${err instanceof Error ? err.message : String(err)}`,
            );
          }
        },
        sendFile: async (filePath: string, caption?: string) => {
          const IMAGE_EXTS = new Set([
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
          ]);
          const ext = extname(filePath).toLowerCase();
          if (IMAGE_EXTS.has(ext)) {
            await ctx.replyWithPhoto(
              new InputFile(filePath),
              caption ? { caption } : {},
            );
          } else {
            await ctx.replyWithDocument(
              new InputFile(filePath),
              caption ? { caption } : {},
            );
          }
        },
      });

      log.telegram.outgoing(`user:${userId}`, response.content);
      log.telegram.info(
        `model:${response.modelUsed} | tools:[${response.toolsUsed.join(", ") || "none"}] | gap:${!!response.pendingCapabilityGap}`,
      );

      // ─── Self-Improvement: Capability Gap → AUTO-BUILD ───────
      if (response.pendingCapabilityGap && this.config.evolution) {
        const gap = response.pendingCapabilityGap;
        log.evolution.evolve(
          `Auto-building tool for gap: "${gap.description.slice(0, 80)}"`,
        );

        // Send the owl's original (apologetic) response first,
        // then immediately start building
        await this.sendResponse(
          ctx,
          response.owlEmoji,
          response.owlName,
          response.content,
        );
        await ctx.reply(
          "🧠 I don't have that capability yet — building it now...",
        );
        await ctx.api.sendChatAction(ctx.chat!.id, "typing");

        const engineContext = {
          provider: this.config.provider,
          owl: this.config.owl,
          sessionHistory: userSession.session.messages,
          config: this.config.config,
          toolRegistry: this.config.toolRegistry,
          capabilityLedger: this.config.capabilityLedger,
          cwd: this.config.cwd,
        };

        try {
          const proposal = await this.config.evolution.designSpec(
            gap,
            engineContext,
          );

          if (proposal.existingTool) {
            log.evolution.evolve(`Reusing existing tool: ${proposal.toolName}`);
            await ctx.reply(
              `♻️ Found existing tool <code>${this.escapeHtml(proposal.toolName)}</code> — retrying...`,
              { parse_mode: "HTML" },
            );
          } else {
            log.evolution.evolve(`Synthesizing new tool: ${proposal.toolName}`);
            await ctx.reply(
              `⚡ Synthesizing <code>${this.escapeHtml(proposal.toolName)}.ts</code>...`,
              { parse_mode: "HTML" },
            );
          }
          await ctx.api.sendChatAction(ctx.chat!.id, "typing");

          // Auto-install npm deps silently (no user question)
          const autoInstall = async (_deps: string[]) => true;

          const onProgress = async (msg: string) => {
            // Only surface meaningful steps, not every log line
            if (
              msg.startsWith("✅") ||
              msg.startsWith("❌") ||
              msg.startsWith("⚠️")
            ) {
              await ctx.reply(msg);
            }
          };

          const { response: retryResponse } =
            await this.config.evolution.buildAndRetry(
              proposal,
              text,
              engineContext,
              this.engine,
              autoInstall,
              onProgress,
            );

          // Update session history with the retry response transcript
          userSession.session.messages.push({ role: "user", content: text });
          for (const msg of retryResponse.newMessages) {
            userSession.session.messages.push(msg);
          }
          userSession.lastActivity = Date.now();
          await this.config.sessionStore.saveSession(userSession.session);

          await this.sendResponse(
            ctx,
            retryResponse.owlEmoji,
            retryResponse.owlName,
            retryResponse.content,
          );
          return;
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          log.evolution.error(`Auto-build failed: ${msg}`);
          await ctx.reply(
            `❌ Couldn't build that capability cleanly. Re-evaluating strategy...`,
          );

          // ─── RELENTLESS REACT FALLBACK ────────────────────────
          // Instead of giving up, we force the AI to observe its failure and try
          // a different approach using ONLY existing tools.
          const fallbackInstruction =
            `[SYSTEM UPDATE] Your attempt to synthesize a new tool failed with error:\n` +
            `\`\`\`\n${msg}\n\`\`\`\n` +
            `You MUST attempt to fulfill the user's original request ("${text}") using a completely DIFFERENT strategy. ` +
            `Use ONLY your existing tools. Do NOT attempt to synthesize another tool.`;

          userSession.session.messages.push({
            role: "system",
            content: fallbackInstruction,
          });

          await ctx.api.sendChatAction(ctx.chat!.id, "typing");

          // We skip gap detection entirely so the agent doesn't get stuck in an infinite synthesis loop
          const fallbackContext = {
            ...engineContext,
            skipGapDetection: true,
          };

          const fallbackResponse = await this.engine.run(text, fallbackContext);

          // Update session history with the new fallback attempt transcript
          userSession.session.messages.push({ role: "user", content: text });
          for (const msg of fallbackResponse.newMessages) {
            userSession.session.messages.push(msg);
          }
          userSession.lastActivity = Date.now();
          await this.config.sessionStore.saveSession(userSession.session);

          await this.sendResponse(
            ctx,
            fallbackResponse.owlEmoji,
            fallbackResponse.owlName,
            fallbackResponse.content,
          );
          return;
        }
      }
      // ─────────────────────────────────────────────────────────

      // Update session history with the full continuous transcript
      userSession.session.messages.push({ role: "user", content: text });
      for (const msg of response.newMessages) {
        userSession.session.messages.push(msg);
      }
      userSession.lastActivity = Date.now();

      // Trim session if too long
      if (userSession.session.messages.length > MAX_SESSION_HISTORY) {
        userSession.session.messages =
          userSession.session.messages.slice(-MAX_SESSION_HISTORY);
      }

      // Save to disk
      await this.config.sessionStore.saveSession(userSession.session);

      // Reactive learning — fire-and-forget after saving session
      if (this.config.learningOrchestrator) {
        this.config.learningOrchestrator
          .processConversation(userSession.session.messages)
          .catch((err) =>
            log.telegram.warn(
              `Learning (orchestrator) failed: ${err instanceof Error ? err.message : err}`,
            ),
          );
      } else if (this.config.learningEngine) {
        this.config.learningEngine
          .processConversation(userSession.session.messages)
          .catch((err) =>
            log.telegram.warn(
              `Learning failed: ${err instanceof Error ? err.message : err}`,
            ),
          );
      }

      await this.sendResponse(
        ctx,
        response.owlEmoji,
        response.owlName,
        response.content,
      );

      // Show token usage only when explicitly enabled — hidden by default to reduce noise
      if (response.usage && this.config.showTokenUsage) {
        await ctx.reply(
          `_${response.usage.promptTokens}→${response.usage.completionTokens} tokens_`,
          { parse_mode: "MarkdownV2" },
        );
      }

      // Detect and persist preferences expressed in this message (fire-and-forget)
      if (this.preferenceDetector && this.config.preferenceStore) {
        this.preferenceDetector
          .detect(text, this.config.preferenceStore, `telegram_${userId}`)
          .catch((err) =>
            log.telegram.warn(
              `Preference detection failed: ${err instanceof Error ? err.message : err}`,
            ),
          );
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      log.telegram.error(`Unhandled error for user ${userId}: ${msg}`);
      await ctx.reply(`❌ Error: ${msg}`);
    }
  }

  /**
   * Check if a user is allowed to interact with the bot.
   */
  private isAllowed(ctx: Context): boolean {
    const userId = ctx.from?.id;
    if (!userId) return false;

    // If no allowlist is set, allow everyone
    if (
      !this.config.allowedUserIds ||
      this.config.allowedUserIds.length === 0
    ) {
      return true;
    }

    const allowed = this.config.allowedUserIds.includes(userId);
    if (!allowed) {
      ctx.reply("🔒 You are not authorized to use this bot.").catch(() => {});
    }
    return allowed;
  }

  /**
   * Get or create a user's session.
   */
  private async getOrCreateSession(userId: number): Promise<UserSession> {
    let userSession = this.sessions.get(userId);

    if (
      !userSession ||
      Date.now() - userSession.lastActivity > SESSION_TIMEOUT_MS
    ) {
      // Try loading from disk
      const sessionId = `telegram_${userId}`;
      let loadedSession = await this.config.sessionStore.loadSession(sessionId);

      if (!loadedSession) {
        loadedSession = this.config.sessionStore.createSession(
          this.config.owl.persona.name,
        );
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
  private async sendResponse(
    ctx: Context,
    emoji: string,
    name: string,
    content: string,
  ): Promise<void> {
    // ── Normal text reply ─────────────────────────────────────────
    const header = `${emoji} *${this.escapeMarkdown(name)}*\n\n`;
    const fullMessage = header + this.escapeMarkdown(content);

    if (fullMessage.length <= 4096) {
      await ctx.reply(fullMessage, { parse_mode: "MarkdownV2" });
    } else {
      const chunks = this.splitMessage(content, 3800);
      const MAX_CHUNKS = 5; // Prevent rate-limit bans from infinite loops
      for (let i = 0; i < Math.min(chunks.length, MAX_CHUNKS); i++) {
        const prefix = i === 0 ? header : "";
        await ctx.reply(prefix + this.escapeMarkdown(chunks[i]), {
          parse_mode: "MarkdownV2",
        });
        // Rate limit protection
        if (i < Math.min(chunks.length, MAX_CHUNKS) - 1) {
          await new Promise((r) => setTimeout(r, 1000));
        }
      }
      if (chunks.length > MAX_CHUNKS) {
        await ctx.reply(
          `_...[Output truncated, ${chunks.length - MAX_CHUNKS} chunks omitted to prevent rate limits]..._`,
          { parse_mode: "MarkdownV2" },
        );
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

  /**
   * Escape special characters for Telegram MarkdownV2.
   */
  private escapeMarkdown(text: string): string {
    return text.replace(/([_*\[\]()~`>#+\-=|{}.!\\])/g, "\\$1");
  }

  /**
   * Escape special characters for Telegram HTML parse mode.
   * Much simpler and safer for dynamic content than MarkdownV2.
   */
  private escapeHtml(text: string): string {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
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
        await this.bot.api.sendMessage(chatId, formatted, {
          parse_mode: "MarkdownV2",
        });
        log.telegram.outgoing(`chat:${chatId}`, message);
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        log.telegram.error(`Failed to broadcast to chat:${chatId}: ${errMsg}`);
        this.activeChatIds.delete(chatId);
      }
    }
  }

  /**
   * Start the bot (long polling).
   */
  async start(): Promise<void> {
    log.telegram.info(`Bot starting...`);

    try {
      // Pre-populate activeChatIds from disk so greeting reaches user on restart
      await this.loadChatIds();

      const me = await this.bot.api.getMe();
      console.log(`[TelegramChannel] ✓ Connected as @${me.username}`);
      console.log(
        `[TelegramChannel] ✓ Owl: ${this.config.owl.persona.emoji} ${this.config.owl.persona.name}`,
      );

      const self = this;

      await this.bot.start({
        onStart: () => {
          console.log(
            "[TelegramChannel] ✓ Bot is running. Send /start in Telegram.",
          );

          // Start proactive pinger
          this.pinger = new ProactivePinger({
            provider: this.config.provider,
            owl: this.config.owl,
            config: this.config.config,
            capabilityLedger: this.config.capabilityLedger!,
            learningEngine: this.config.learningEngine,
            learningOrchestrator: this.config.learningOrchestrator,
            preferenceStore: this.config.preferenceStore,
            sessionStore: this.config.sessionStore,
            sendToUser: async (message: string) => {
              await this.broadcastProactiveMessage(message);
            },
            get userId() {
              // Default to the most recently active chat for consolidation
              const userSessions = Array.from(
                self.sessions?.values() || [],
              ) as any[];
              if (userSessions.length === 0) return undefined;
              const latest = userSessions.sort(
                (a, b) => b.lastActivity - a.lastActivity,
              )[0];
              return latest ? latest.userId : undefined;
            },
            getRecentHistory: () => {
              // Get the most recent session's history for context
              const userSessions = Array.from(self.sessions.values());
              if (userSessions.length === 0) return [];
              const latest = userSessions.sort(
                (a, b) => b.lastActivity - a.lastActivity,
              )[0];
              return latest ? latest.session.messages : [];
            },
          });
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
    console.log("[TelegramChannel] Bot stopped.");
  }
}

/**
 * StackOwl — Owl Gateway (Core)
 *
 * The single point of entry for all incoming messages.
 * All business logic lives here:
 *   - Session management
 *   - Instinct evaluation
 *   - ReAct engine execution
 *   - Capability gap detection + auto-synthesis
 *   - Post-processing: memory, learning, DNA evolution
 *
 * Channel adapters are pure transport — they call handle() and receive
 * a GatewayResponse. They don't know anything about owls, sessions, or tools.
 */

import { v4 as uuidv4 } from 'uuid';
import type { ChatMessage } from '../providers/base.js';
import type { Session } from '../memory/store.js';
import type { EngineContext, EngineResponse } from '../engine/runtime.js';
import { OwlEngine } from '../engine/runtime.js';
import { MemoryConsolidator } from '../memory/consolidator.js';
import { PreferenceDetector } from '../preferences/detector.js';
import { log } from '../logger.js';
import type {
    GatewayMessage,
    GatewayResponse,
    GatewayCallbacks,
    ChannelAdapter,
    GatewayContext,
} from './types.js';

// ─── Constants ───────────────────────────────────────────────────

const MAX_SESSION_HISTORY = 50;
const SESSION_TIMEOUT_MS  = 2 * 60 * 60 * 1000; // 2 hours

interface SessionCache {
    session: Session;
    lastActivity: number;
}

// ─── Gateway ─────────────────────────────────────────────────────

export class OwlGateway {
    private engine: OwlEngine;
    private adapters: Map<string, ChannelAdapter> = new Map();
    private sessions: Map<string, SessionCache> = new Map();
    private messageCount = 0;

    constructor(private ctx: GatewayContext) {
        this.engine = new OwlEngine();
    }

    // ─── Adapter Registry ────────────────────────────────────────

    register(adapter: ChannelAdapter): void {
        this.adapters.set(adapter.id, adapter);
        log.engine.info(`Channel registered: ${adapter.name} [${adapter.id}]`);
    }

    // ─── Main Entry Point ────────────────────────────────────────

    /**
     * Process an incoming message from any channel.
     * The adapter provides per-message callbacks for streaming (onProgress, onFile).
     */
    async handle(message: GatewayMessage, callbacks: GatewayCallbacks = {}): Promise<GatewayResponse> {
        const session = await this.getOrCreateSession(message);

        // Evaluate instincts — may inject behavioral constraints
        let text = message.text;
        if (this.ctx.instinctEngine && this.ctx.instinctRegistry) {
            const instincts = this.ctx.instinctRegistry.getContextInstincts(this.ctx.owl.persona.name);
            const triggered = await this.ctx.instinctEngine.evaluate(text, instincts, {
                provider: this.ctx.provider,
                owl: this.ctx.owl,
                config: this.ctx.config,
            });
            if (triggered) {
                log.engine.info(`Instinct triggered: ${triggered.name}`);
                text = `User Input: ${text}\n\n[SYSTEM OVERRIDE - INSTINCT TRIGGERED]\n${triggered.actionPrompt}`;
            }
        }

        log.engine.incoming(message.channelId, message.text);

        const engineCtx = this.buildEngineContext(session, callbacks);
        const response = await this.engine.run(text, engineCtx);

        // Capability gap detected — try to synthesize the missing tool and retry
        if (response.pendingCapabilityGap && this.ctx.evolution) {
            return await this.handleCapabilityGap(message, response, session, engineCtx, callbacks);
        }

        await this.saveSession(session, message.text, response.newMessages);
        this.postProcess(session.messages);

        // Detect and persist preferences expressed in this message (fire-and-forget)
        this.detectPreferences(message.text, message.channelId);

        return toGatewayResponse(response);
    }

    // ─── Session Lifecycle ───────────────────────────────────────

    /**
     * Gracefully end a session: run memory consolidation + DNA evolution.
     * Call this when a user explicitly ends their session (/quit in CLI).
     */
    async endSession(sessionId: string): Promise<void> {
        const cache = this.sessions.get(sessionId);
        if (!cache) return;

        const messages = cache.session.messages;

        // Memory consolidation
        try {
            const consolidator = new MemoryConsolidator(this.ctx.provider, this.ctx.owl, this.ctx.cwd ?? process.cwd());
            await consolidator.extractAndAppend(messages);
            log.engine.info('Memory consolidated.');
        } catch (err) {
            log.engine.warn(`Memory consolidation failed: ${err instanceof Error ? err.message : err}`);
        }

        // Reactive learning
        if (this.ctx.learningEngine) {
            await this.ctx.learningEngine.processConversation(messages).catch(() => {});
        }

        // DNA evolution
        if (this.ctx.evolutionEngine) {
            await this.ctx.evolutionEngine.evolve(this.ctx.owl.persona.name).catch(() => {});
        }
    }

    // ─── Proactive Messaging ─────────────────────────────────────

    /**
     * Send a proactive message to a specific user on a specific channel.
     */
    async sendProactive(channelId: string, userId: string, text: string): Promise<void> {
        const adapter = this.adapters.get(channelId);
        if (!adapter) return;
        const response: GatewayResponse = {
            content: text,
            owlName: this.ctx.owl.persona.name,
            owlEmoji: this.ctx.owl.persona.emoji,
            toolsUsed: [],
        };
        await adapter.sendToUser(userId, response);
    }

    /**
     * Broadcast a proactive message to all active users across all channels.
     */
    async broadcastProactive(text: string): Promise<void> {
        const response: GatewayResponse = {
            content: text,
            owlName: this.ctx.owl.persona.name,
            owlEmoji: this.ctx.owl.persona.emoji,
            toolsUsed: [],
        };
        for (const adapter of this.adapters.values()) {
            await adapter.broadcast(response).catch(err =>
                log.engine.warn(`Broadcast failed on ${adapter.id}: ${err instanceof Error ? err.message : err}`)
            );
        }
    }

    // ─── Status Queries ──────────────────────────────────────────

    getOwl() { return this.ctx.owl; }
    getProvider() { return this.ctx.provider; }
    getConfig() { return this.ctx.config; }
    getOwlRegistry() { return this.ctx.owlRegistry; }
    getEvolution() { return this.ctx.evolution; }
    getLearningEngine() { return this.ctx.learningEngine; }
    getCapabilityLedger() { return this.ctx.capabilityLedger; }
    getPreferenceStore() { return this.ctx.preferenceStore; }

    // ─── Private: Capability Gap ─────────────────────────────────

    private async handleCapabilityGap(
        message: GatewayMessage,
        response: EngineResponse,
        session: Session,
        engineCtx: EngineContext,
        callbacks: GatewayCallbacks,
    ): Promise<GatewayResponse> {
        const gap = response.pendingCapabilityGap!;
        log.evolution.evolve(`Capability gap: "${gap.description.slice(0, 80)}"`);

        await callbacks.onProgress?.(`🧠 I don't have that capability yet — building it now...`);

        try {
            const proposal = await this.ctx.evolution!.designSpec(gap, engineCtx);

            if (proposal.existingTool) {
                log.evolution.evolve(`Reusing existing tool: ${proposal.toolName}`);
                await callbacks.onProgress?.(`♻️ Found ${proposal.toolName} — retrying...`);
            } else {
                log.evolution.evolve(`Synthesizing: ${proposal.toolName}`);
                await callbacks.onProgress?.(`⚡ Synthesizing ${proposal.toolName}...`);
            }

            const askInstall = callbacks.askInstall ?? (async (_deps: string[]) => true);
            const onProgress = callbacks.onProgress ?? (async (_msg: string) => {});

            const { response: retryResponse } = await this.ctx.evolution!.buildAndRetry(
                proposal, message.text, engineCtx, this.engine, askInstall, onProgress
            );

            await this.saveSession(session, message.text, retryResponse.newMessages);
            this.postProcess(session.messages);
            return toGatewayResponse(retryResponse);

        } catch (err) {
            log.evolution.error(`Gap handling failed: ${err instanceof Error ? err.message : err}`);
            // Fallback: return original apologetic response
            await this.saveSession(session, message.text, response.newMessages);
            this.postProcess(session.messages);
            return toGatewayResponse(response);
        }
    }

    // ─── Private: Session ────────────────────────────────────────

    private async getOrCreateSession(message: GatewayMessage): Promise<Session> {
        const key = message.sessionId;
        const cached = this.sessions.get(key);

        if (cached && Date.now() - cached.lastActivity <= SESSION_TIMEOUT_MS) {
            cached.lastActivity = Date.now();
            return cached.session;
        }

        // Load from disk or create fresh
        let session = await this.ctx.sessionStore.loadSession(key);
        if (!session) {
            session = this.ctx.sessionStore.createSession(this.ctx.owl.persona.name);
            session.id = key;
            await this.ctx.sessionStore.saveSession(session);
        }

        this.sessions.set(key, { session, lastActivity: Date.now() });
        return session;
    }

    private async saveSession(
        session: Session,
        userText: string,
        newMessages: ChatMessage[],
    ): Promise<void> {
        session.messages.push({ role: 'user', content: userText });
        for (const msg of newMessages) {
            session.messages.push(msg);
        }

        // Trim to avoid unbounded growth
        if (session.messages.length > MAX_SESSION_HISTORY) {
            session.messages = session.messages.slice(-MAX_SESSION_HISTORY);
        }

        await this.ctx.sessionStore.saveSession(session);

        // Update cache timestamp
        const key = session.id;
        const cached = this.sessions.get(key);
        if (cached) cached.lastActivity = Date.now();
    }

    // ─── Private: Post-processing ────────────────────────────────

    /**
     * Fire-and-forget tasks that run after every response:
     * learning signal extraction and mid-session DNA evolution.
     */
    private postProcess(messages: ChatMessage[]): void {
        if (this.ctx.learningEngine) {
            this.ctx.learningEngine.processConversation(messages).catch(() => {});
        }

        this.messageCount++;
        const evolutionInterval = this.ctx.config.owlDna?.evolutionBatchSize ?? 10;
        if (this.messageCount % evolutionInterval === 0 && this.ctx.evolutionEngine) {
            this.ctx.evolutionEngine
                .evolve(this.ctx.owl.persona.name)
                .catch(err => log.evolution.warn(`Mid-session evolution failed: ${err instanceof Error ? err.message : err}`));
        }
    }

    // ─── Private: Engine Context ─────────────────────────────────

    private buildEngineContext(session: Session, callbacks: GatewayCallbacks): EngineContext {
        const preferencesContext = this.ctx.preferenceStore?.toContextString() ?? '';
        return {
            provider:           this.ctx.provider,
            owl:                this.ctx.owl,
            sessionHistory:     session.messages,
            config:             this.ctx.config,
            toolRegistry:       this.ctx.toolRegistry,
            pelletStore:        this.ctx.pelletStore,
            capabilityLedger:   this.ctx.capabilityLedger,
            cwd:                this.ctx.cwd,
            memoryContext:      this.ctx.memoryContext,
            preferencesContext: preferencesContext || undefined,
            onProgress:         callbacks.onProgress,
            sendFile:           callbacks.onFile,
        };
    }

    /** Fire-and-forget: detect preference statements and persist them. */
    private detectPreferences(userMessage: string, channelId: string): void {
        if (!this.ctx.preferenceStore) return;
        const detector = new PreferenceDetector(this.ctx.provider);
        detector.detect(userMessage, this.ctx.preferenceStore, channelId).catch(() => {});
    }
}

// ─── Helpers ─────────────────────────────────────────────────────

function toGatewayResponse(r: EngineResponse): GatewayResponse {
    return {
        content:   r.content,
        owlName:   r.owlName,
        owlEmoji:  r.owlEmoji,
        toolsUsed: r.toolsUsed,
        usage:     r.usage,
    };
}

export function makeSessionId(channelId: string, userId: string): string {
    return `${channelId}:${userId}`;
}

export function makeMessageId(): string {
    return uuidv4();
}

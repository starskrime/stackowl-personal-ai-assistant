/**
 * cli-v2.ts — TUI v2 Channel Adapter.
 *
 * Phase 1: Full REPL adapter that calls gateway.handle() and feeds
 * engine signals into the UiBridge as typed UiEvents.
 *
 * Key contracts:
 * - emit() routes all engine signals through UiBridge (the ONE translator)
 * - capabilities() declares tuiV2: true so heartbeat + parliament choose the right delivery path
 * - wireToolNarration from v1 is intentionally NOT used here — tool events come via emit()
 * - Ink owns stdin; the adapter exposes submitMessage() for the Composer component to call
 */

import { v4 as uuidv4 } from "uuid";
import type { ChannelAdapter, ChannelCapabilities, GatewayResponse } from "../types.js";
import type { UiEvent } from "../../cli/v2/events/UiEvent.js";
import { globalBridge, type OwlMeta } from "../../cli/v2/events/bridge.js";
import { OwlGateway, makeMessage, makeSessionId } from "../core.js";
import { runWithContext } from "../../infra/observability/context.js";
import { createCommandDispatcher } from "../../cli/v2/commands/dispatcher.js";
import type { CommandDispatcher } from "../../cli/v2/commands/dispatcher.js";

export interface CliV2AdapterConfig {
  userId?: string;
  workspacePath?: string;
}

export class CliV2Adapter implements ChannelAdapter {
  readonly id = "cli-v2";
  readonly name = "CLI v2";

  private readonly _gateway: OwlGateway;
  private readonly _userId: string;
  private readonly _channelId = "cli-v2";
  private _sessionId: string;

  /** Resolves when stop() is called — start() awaits this. */
  private _quitResolve: (() => void) | null = null;
  private _quitPromise: Promise<void>;
  private _commandDispatcher: CommandDispatcher | null = null;
  /** Active turn ID during assistant response; null between turns. */
  private _currentTurnId: string | null = null;

  constructor(gateway: OwlGateway, config: CliV2AdapterConfig = {}) {
    this._gateway = gateway;
    this._userId = config.userId ?? "local";
    this._sessionId = makeSessionId(this._channelId, this._userId);

    // Pre-construct the quit promise so stop() is safe to call before start().
    this._quitPromise = new Promise<void>((resolve) => {
      this._quitResolve = resolve;
    });
  }

  // ─── ChannelAdapter ───────────────────────────────────────────────────────

  async start(): Promise<void> {
    // Resolve initial owl meta from gateway.
    const owl = this._gateway.getOwl();
    const owlEmoji = owl.persona.emoji;
    const owlName = owl.persona.name;

    // Seed the store with the current session.
    globalBridge.emit({ kind: "session.changed", sessionId: this._sessionId });

    // Seed the owl identity + model without starting a "turn" (which sets generating: true).
    // Using changeOwl keeps generating: false so the Composer is ready for input immediately.
    const initModel = this._gateway.getConfig().defaultModel ?? "";
    globalBridge.changeOwl(owlName, owlEmoji, initModel);

    // Load recent sessions asynchronously — must NOT block the UI.
    // The store is pre-seeded with an empty list; sessions.loaded fills it in.
    this._loadSessionsAsync();

    // Load palette data (owls, skills, MCP) asynchronously — non-blocking.
    this._loadOwlsAsync();
    this._loadSkillsAsync();
    this._loadMcpAsync();

    // Subscribe to memory:written events — forward to TUI as memory.written UiEvents.
    this._gateway.gatewayEventBus.on("memory:written", (e) => {
      globalBridge.emit({
        kind: "memory.written",
        turnId: this._currentTurnId ?? "unknown",
        memoryKind: e.kind,
        importance: e.importance,
      });
    });

    // Stay alive until stop() is called (Ink owns the event loop via stdin).
    await this._quitPromise;
  }

  /**
   * Switch to a previously-used session. New messages will continue that session's thread.
   * Called by the SessionsScreen when the user selects a session.
   */
  resumeSession(sessionId: string, title?: string): void {
    this._sessionId = sessionId;
    globalBridge.emit({ kind: "session.changed", sessionId, title });
    globalBridge.dismissSessionsView();
  }

  stop(): void {
    this._quitResolve?.();
    this._quitResolve = null;
  }

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    this._emitCommitted(response);
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    this._emitCommitted(response);
  }

  // ─── TUI v2 extensions ────────────────────────────────────────────────────

  emit(event: UiEvent): void {
    globalBridge.emit(event);
  }

  getCommandDispatcher(): CommandDispatcher {
    if (!this._commandDispatcher) {
      this._commandDispatcher = createCommandDispatcher(() => ({
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        getMemoryRepo: () => this._gateway.getMemoryRepo()!,
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        getMcpManager: () => this._gateway.getMcpManager()!,
        getOwlGateway: () => this._gateway,
      }));
    }
    return this._commandDispatcher;
  }

  capabilities(): ChannelCapabilities {
    return {
      tuiV2: true,
      richText: false,
      fileAttachments: false,
    };
  }

  // ─── REPL surface (called by Ink Composer component) ─────────────────────

  /**
   * Process a user message through the gateway.
   * Called by the Composer Ink component on Enter.
   */
  async submitMessage(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) return;

    const msg = makeMessage(this._channelId, this._userId, trimmed, this._sessionId);
    if (!msg) return;

    const turnId = uuidv4();
    const debateId = uuidv4();

    // Resolve current owl meta for the bridge.
    const owl = this._gateway.getOwl();
    const activeModel = this._gateway.getConfig().defaultModel ?? "";
    let owlMeta: OwlMeta = {
      owlEmoji: owl.persona.emoji,
      owlName: owl.persona.name,
      model: activeModel,
    };

    // Show user's message in the Transcript immediately.
    globalBridge.emit({ kind: "user.message", turnId: uuidv4(), text: trimmed });

    // Announce turn start (current owl + model).
    this._currentTurnId = turnId;
    globalBridge.translateOwlChange(turnId, owlMeta.owlEmoji, owlMeta.owlName, undefined, owlMeta.model);

    // Accumulate streamed text so we can supply fullText on "done".
    let accumulated = "";
    // Track whether the streaming path already emitted turn.committed via the
    // "done" StreamEvent so the fallback below doesn't fire a second time.
    let committedViaStream = false;

    // Build debate callbacks — will be lazily resolved with real participants when
    // the gateway fires parliament.  We pass a factory here because we don't know
    // who the participants are until the gateway chooses them.
    const debateCallbacks = globalBridge.makeDebateCallbacks(debateId, [
      { owlName: owl.persona.name, owlEmoji: owl.persona.emoji },
    ]);

    try {
      const response = await runWithContext({
        channelId: "cli-v2",
        userId: this._userId,
        sessionId: this._sessionId,
        messageId: msg.id,
        spanName: "channel.cli-v2.handle",
      }, () => this._gateway.handle(msg, {
        suppressThinking: true,

        onStreamEvent: async (event) => {
          // Track accumulated text for the "done" event.
          if (event.type === "text_delta") {
            accumulated += event.content;
          }
          // translateStreamEvent emits turn.committed when event.type === "done".
          if (event.type === "done") {
            committedViaStream = true;
          }
          globalBridge.translateStreamEvent(turnId, event, owlMeta, accumulated);
        },

        onOwlChange: (owlEmoji: string, owlName: string) => {
          // Re-emit turn.started with the new specialist owl.
          // Reassign to a new object instead of mutating, to avoid a race where
          // onStreamEvent reads owlMeta concurrently and sees a half-updated value.
          owlMeta = { ...owlMeta, owlEmoji, owlName };
          globalBridge.translateOwlChange(turnId, owlEmoji, owlName, undefined, owlMeta.model);
        },

        onProgress: async (_text: string) => {
          // Ink handles progress via stream events — no-op here.
        },

        askInstall: async (_deps: string[]) => {
          // Phase 1: always approve. Phase 3 will wire a modal.
          return true;
        },

        debateCallbacks,
      }));

      // Fallback: if the engine did NOT stream a "done" event (non-streaming
      // providers), emit turn.committed from the final GatewayResponse.
      if (!committedViaStream) {
        const finalText = response.content || accumulated;
        globalBridge.emit({
          kind: "turn.committed",
          turnId,
          text: finalText,
          usage: response.usage
            ? {
                promptTokens: response.usage.promptTokens,
                completionTokens: response.usage.completionTokens,
                costUsd: response.estimatedCostUsd ?? 0,
              }
            : undefined,
        });
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      globalBridge.emit({
        kind: "notice",
        source: "error",
        text: errMsg,
        severity: "error",
      });
    } finally {
      this._currentTurnId = null;
    }
  }

  // ─── Internals ────────────────────────────────────────────────────────────

  /** Load recent sessions from the SessionStore and populate the TUI store. */
  private _loadSessionsAsync(): void {
    const sessionStore = this._gateway.getSessionStore();
    if (!sessionStore) return;

    sessionStore.listSessions().then((sessions) => {
      const summaries = sessions.slice(0, 20).map((s) => ({
        sessionId: s.id,
        title: s.metadata.title ?? s.metadata.owlName ?? s.id,
        lastActiveAt: s.metadata.lastUpdatedAt,
      }));
      globalBridge.loadSessions(summaries);
    }).catch(() => {
      // Non-critical — silently ignore failures
    });
  }

  /** Load available owls from the registry and populate the TUI store. */
  private _loadOwlsAsync(): void {
    const owlRegistry = this._gateway.getOwlRegistry();
    if (!owlRegistry) return;
    const activeOwlName = this._gateway.getOwl().persona.name.toLowerCase();
    const owls = owlRegistry.listOwls().map((instance) => ({
      name: instance.persona.name,
      emoji: instance.persona.emoji,
      description: instance.persona.specialties.slice(0, 3).join(", ") || instance.persona.type,
      isActive: instance.persona.name.toLowerCase() === activeOwlName,
    }));
    globalBridge.loadOwls(owls);
  }

  /** Load installed skills and populate the TUI store. */
  private _loadSkillsAsync(): void {
    const skillsLoader = this._gateway.getSkillsLoader();
    if (!skillsLoader) return;
    const registry = skillsLoader.getRegistry();
    const skills = registry.listAll().map((s) => ({
      name: s.name,
      description: s.description ?? "",
      enabled: s.enabled !== false,
    }));
    globalBridge.loadSkills(skills);
  }

  /** Load MCP server status and populate the TUI store. */
  private _loadMcpAsync(): void {
    const mcpManager = this._gateway.getMcpManager();
    if (!mcpManager) return;
    const servers = mcpManager.listServers().map((s) => ({
      name: s.name,
      transport: s.transport,
      connected: s.connected,
      toolCount: s.toolCount,
    }));
    globalBridge.loadMcpServers(servers);
  }

  private _emitCommitted(response: GatewayResponse): void {
    const turnId = uuidv4();
    // Do NOT call translateOwlChange here — that emits turn.started which sets
    // generating: true, causing a flicker if a streaming turn is already in
    // progress.  Instead, carry owl identity directly on the committed event so
    // the Transcript component can identify the source without a preceding
    // turn.started.
    globalBridge.emit({
      kind: "turn.committed",
      turnId,
      text: response.content,
      owlEmoji: response.owlEmoji,
      owlName: response.owlName,
      usage: response.usage
        ? {
            promptTokens: response.usage.promptTokens,
            completionTokens: response.usage.completionTokens,
            costUsd: response.estimatedCostUsd ?? 0,
          }
        : undefined,
    });
  }
}

/**
 * StackOwl — Telegram Config Menu Controller
 *
 * TelegramConfigMenu handles all "cfg:" callback_query events and
 * the /config command.  It owns:
 *   - MenuStateManager (per-user session state)
 *   - Screen-to-screen navigation via edit-in-place
 *   - Provider add/edit/remove flows
 *   - Model picker (with pagination for large lists)
 *   - Model role assignment
 *   - Smart routing roster management
 *   - Live health check
 *   - Option C API-key security: delete-immediately + secure web form
 *
 * This class is intentionally kept separate from TelegramAdapter
 * to prevent the adapter from growing beyond ~1000 lines.
 */

import type { Context } from "grammy";
import type { StackOwlConfig } from "../../../config/loader.js";
import { log } from "../../../logger.js";
import { MenuStateManager, type MenuState } from "./state.js";
import type { HealthMap } from "./screens.js";
import {
  renderMain,
  renderProviders,
  renderProviderDetail,
  renderProviderRemoveConfirm,
  renderAddProviderType,
  renderAddProviderUrl,
  renderAddProviderKey,
  renderModelPicker,
  renderModelRoles,
  renderRoleProviderPicker,
  renderSmartRouting,
  renderSmartRoutingProviderPicker,
  renderSmartRoutingModelPicker,
  renderHealthCheck,
  renderWebFormLink,
  renderError,
  renderSuccess,
  ANTHROPIC_MODELS,
  PROVIDER_TYPE_META,
} from "./screens.js";
import type { ProviderConfigEntry } from "../../../config/loader.js";
import { getModelLoader } from "../../../models/loader.js";

// ─── One-time secure web form tokens ──────────────────────────────

interface KeyToken {
  providerKey: string;
  field: "apiKey";
  userId: number;
  expiresAt: number;
}

// ─── Controller ───────────────────────────────────────────────────

export class TelegramConfigMenu {
  private stateManager = new MenuStateManager();
  /** key = random token string, value = pending key entry metadata */
  private keyTokens: Map<string, KeyToken> = new Map();
  /** Live health data refreshed on each /hc screen open */
  private lastHealth: HealthMap = {};

  constructor(
    private getConfig: () => StackOwlConfig,
    private saveConfigFn: (config: StackOwlConfig) => Promise<void>,
    private gatewayPort: number,
    /** Allows the menu to instantiate providers for health checks + model listing */
    private providerRegistry: {
      get(name: string): { healthCheck(): Promise<boolean>; listModels(): Promise<string[]> };
      listProviders(): string[];
    },
  ) {}

  // ─── Entry points ─────────────────────────────────────────────

  /**
   * Handle /config command — create or resume a menu session.
   */
  async handleCommand(ctx: Context): Promise<void> {
    const userId = ctx.from?.id;
    const chatId = ctx.chat?.id;
    if (!userId || !chatId) return;

    const config  = this.getConfig();
    const content = renderMain(config);

    const sent = await ctx.reply(content.text, {
      parse_mode: "HTML",
      reply_markup: content.keyboard,
    });

    this.stateManager.set({
      userId,
      chatId,
      messageId: sent.message_id,
      screen: "main",
      breadcrumb: [],
      lastActivity: Date.now(),
    });
  }

  /**
   * Handle a cfg:* callback_query.
   * Returns true if the callback was handled, false if unrecognised.
   */
  async handleCallback(ctx: Context, data: string): Promise<boolean> {
    const userId = ctx.from?.id;
    if (!userId) return false;

    const state = this.stateManager.get(userId);
    if (!state) {
      // Session expired — acknowledge and prompt to restart
      await ctx.answerCallbackQuery({ text: "⏱ Session expired. Send /config to restart." });
      return true;
    }

    this.stateManager.touch(userId);

    // Silence Telegram loading spinner immediately
    try { await ctx.answerCallbackQuery(); } catch { /* expired — harmless */ }

    const cmd = data.slice("cfg:".length); // everything after "cfg:"

    try {
      await this.route(ctx, state, cmd);
    } catch (err) {
      log.telegram.error(`[ConfigMenu] Error handling ${data}: ${err}`);
      await this.editScreen(ctx, state, renderError(
        err instanceof Error ? err.message : String(err),
      ));
    }

    return true;
  }

  /**
   * Handle a plain-text message from a user who has a pendingInput waiting.
   * Returns true if the message was consumed by the config menu, false otherwise.
   */
  async handleTextInput(ctx: Context, text: string): Promise<boolean> {
    const userId = ctx.from?.id;
    if (!userId) return false;

    const state = this.stateManager.get(userId);
    if (!state?.pendingInput) return false;

    const { field, contextKey } = state.pendingInput;
    state.pendingInput = undefined;

    if (field === "apiKey") {
      // Option C path A: Delete the user's message immediately
      try {
        const msgId = ctx.message?.message_id;
        if (msgId) {
          await ctx.api.deleteMessage(state.chatId, msgId);
        }
      } catch (err) {
        // Telegram only allows deleting messages <48h old from bots with delete perms
        log.telegram.warn(`[ConfigMenu] Could not delete key message: ${err}`);
      }

      await this.applyApiKey(ctx, state, contextKey, text.trim());
      return true;
    }

    if (field === "baseUrl") {
      await this.applyBaseUrl(ctx, state, contextKey, text.trim());
      return true;
    }

    return false;
  }

  // ─── Router ───────────────────────────────────────────────────

  private async route(ctx: Context, state: MenuState, cmd: string): Promise<void> {
    // ── Navigation ───────────────────────────────────────────
    if (cmd === "~" || cmd === "main") {
      state.screen      = "main";
      state.breadcrumb  = [];
      state.pendingInput = undefined;
      await this.editScreen(ctx, state, renderMain(this.getConfig()));
      return;
    }

    if (cmd === "bc") {
      this.stateManager.back(state.userId);
      await this.renderCurrentScreen(ctx, state);
      return;
    }

    if (cmd === "cl") {
      this.stateManager.delete(state.userId);
      await ctx.editMessageText("✅ Configuration closed.", { parse_mode: "HTML" });
      return;
    }

    if (cmd === "noop") return; // pagination label button

    // ── Providers ────────────────────────────────────────────
    if (cmd === "pr") {
      this.stateManager.navigate(state.userId, "providers");
      await this.editScreen(ctx, state, renderProviders(this.getConfig(), this.lastHealth));
      return;
    }

    if (cmd.startsWith("pd:")) {
      const key = cmd.slice(3);
      state.pendingProviderKey = key;
      this.stateManager.navigate(state.userId, "provider_detail");
      await this.editScreen(ctx, state, renderProviderDetail(key, this.getConfig(), this.lastHealth));
      return;
    }

    if (cmd.startsWith("pt:")) {
      // Test a single provider
      const key = cmd.slice(3);
      await this.testProvider(ctx, state, key);
      return;
    }

    if (cmd.startsWith("pd_def:")) {
      const key = cmd.slice(7);
      await this.setDefaultProvider(ctx, state, key);
      return;
    }

    if (cmd.startsWith("pd_rm:")) {
      const key = cmd.slice(6);
      state.pendingProviderKey = key;
      await this.editScreen(ctx, state, renderProviderRemoveConfirm(key));
      return;
    }

    if (cmd.startsWith("pd_rx:")) {
      const key = cmd.slice(6);
      await this.removeProvider(ctx, state, key);
      return;
    }

    if (cmd.startsWith("pk:")) {
      // Change model for an existing provider
      const key = cmd.slice(3);
      state.pendingProviderKey = key;
      await this.openModelPicker(ctx, state, key, "provider");
      return;
    }

    // ── Add Provider ─────────────────────────────────────────
    if (cmd === "pa") {
      this.stateManager.navigate(state.userId, "provider_add_type");
      await this.editScreen(ctx, state, renderAddProviderType());
      return;
    }

    if (cmd.startsWith("pa:")) {
      const providerType = cmd.slice(3);
      state.pendingEntry = { providerType };
      // Anthropic doesn't need a URL
      if (providerType === "anthropic") {
        this.stateManager.navigate(state.userId, "provider_add_key");
        await this.editScreen(ctx, state, renderAddProviderKey(providerType, this.gatewayPort));
      } else if (providerType === "openai") {
        // OpenAI: no custom URL needed, go straight to key
        state.pendingEntry.baseUrl = "https://api.openai.com/v1";
        this.stateManager.navigate(state.userId, "provider_add_key");
        await this.editScreen(ctx, state, renderAddProviderKey(providerType, this.gatewayPort));
      } else {
        // Need URL first
        this.stateManager.navigate(state.userId, "provider_add_url");
        await this.editScreen(ctx, state, renderAddProviderUrl(providerType));
        // Mark as awaiting URL input
        state.pendingInput = { field: "baseUrl", contextKey: providerType };
      }
      return;
    }

    // Skip (use default URL)
    if (cmd.startsWith("pu_skip:")) {
      const providerType = cmd.slice(8);
      const defaults: Record<string, string> = {
        ollama:              "http://127.0.0.1:11434",
        "ollama-cloud":      "",
        lmstudio:            "http://127.0.0.1:1234/v1",
        "openai-compatible": "",
      };
      state.pendingEntry = state.pendingEntry ?? { providerType };
      state.pendingEntry.baseUrl = defaults[providerType] ?? "";
      // Check if needs an API key
      if (["ollama", "lmstudio"].includes(providerType)) {
        // No key needed — go straight to auto-detect/model picker
        await this.tryAutoDetectAndPickModel(ctx, state, providerType);
      } else {
        this.stateManager.navigate(state.userId, "provider_add_key");
        await this.editScreen(ctx, state, renderAddProviderKey(providerType, this.gatewayPort));
      }
      return;
    }

    // Key: delete-immediately (user has already sent the key as plain text — handled in handleTextInput)
    // This branch handles: user taps "Send key in chat" implicitly (no button, just types)
    // The UI instructs them to type — handleTextInput picks it up.
    // Here we just set the pendingInput so the next text message is consumed:
    if (cmd.startsWith("ky:")) {
      const providerType = cmd.slice(3);
      state.pendingInput = { field: "apiKey", contextKey: providerType };
      // Already on the key screen, nothing to re-render
      return;
    }

    // Skip key (e.g. local Ollama, no auth)
    if (cmd.startsWith("ky_skip:")) {
      const providerType = cmd.slice(8);
      await this.finalizeProviderAdd(ctx, state, providerType, undefined);
      return;
    }

    // Use secure web form (Option C path B)
    if (cmd.startsWith("ky_web:")) {
      const providerType = cmd.slice(7);
      await this.openWebForm(ctx, state, providerType);
      return;
    }

    // Web form done confirmation
    if (cmd.startsWith("ky_done:")) {
      const providerType = cmd.slice(8);
      // Look for the key that was entered via web form for this provider
      const config = this.getConfig();
      const entry  = config.providers[providerType];
      if (entry?.apiKey) {
        await this.openModelPicker(ctx, state, providerType, "add");
      } else {
        await this.editScreen(ctx, state, renderError(
          `No API key was saved for <b>${providerType}</b> yet. Please enter it via the web form or try again.`,
        ));
      }
      return;
    }

    // ── Model Picker ─────────────────────────────────────────
    if (cmd.startsWith("mp:")) {
      const idx = parseInt(cmd.slice(3), 10);
      await this.selectModel(ctx, state, idx);
      return;
    }

    if (cmd.startsWith("mp_pg:")) {
      const page = parseInt(cmd.slice(6), 10);
      const contextLabel = state.pendingProviderKey ?? state.pendingRole ?? "?";
      const current = this.getConfig().providers[state.pendingProviderKey ?? ""]?.defaultModel;
      await this.editScreen(ctx, state, renderModelPicker(
        state.modelList ?? [], current, contextLabel, page,
      ));
      return;
    }

    // ── Model Roles ──────────────────────────────────────────
    if (cmd === "rl") {
      this.stateManager.navigate(state.userId, "model_roles");
      await this.editScreen(ctx, state, renderModelRoles(this.getConfig()));
      return;
    }

    if (cmd.startsWith("rl:")) {
      const role = cmd.slice(3);
      state.pendingRole = role;
      const providers   = Object.keys(this.getConfig().providers);
      state.providerList = providers;
      this.stateManager.navigate(state.userId, "model_role_prov_pick");
      await this.editScreen(ctx, state, renderRoleProviderPicker(role, providers, this.getConfig()));
      return;
    }

    if (cmd.startsWith("pp:")) {
      const idx = parseInt(cmd.slice(3), 10);
      const selectedProvider = state.providerList?.[idx];
      if (!selectedProvider) return;
      state.pendingRoleProvider = selectedProvider;
      await this.openModelPicker(ctx, state, selectedProvider, "role");
      return;
    }

    // ── Smart Routing ────────────────────────────────────────────
    if (cmd === "sr") {
      this.stateManager.navigate(state.userId, "smart_routing");
      await this.editScreen(ctx, state, renderSmartRouting(this.getConfig()));
      return;
    }

    if (cmd === "sr_tog") {
      await this.toggleSmartRouting(ctx, state);
      return;
    }

    if (cmd === "sr_add") {
      const providers = getModelLoader().getAll().map(d => d.name);
      this.stateManager.navigate(state.userId, "sr_prov_pick");
      await this.editScreen(ctx, state, renderSmartRoutingProviderPicker(providers));
      return;
    }

    if (cmd.startsWith("sr_ap:")) {
      const providerName = cmd.slice(6);
      state.pendingSrProvider = providerName;
      const def = getModelLoader().get(providerName);
      const models = def?.availableModels ?? [];
      this.stateManager.navigate(state.userId, "sr_model_pick");
      await this.editScreen(ctx, state, renderSmartRoutingModelPicker(providerName, models));
      return;
    }

    if (cmd.startsWith("sr_am:")) {
      const parts = cmd.slice(6).split(":");
      const providerName = parts[0];
      const modelName    = parts.slice(1).join(":");
      await this.addRosterEntry(ctx, state, providerName, modelName);
      return;
    }

    if (cmd.startsWith("sr_rm:")) {
      const idx = parseInt(cmd.slice(6), 10);
      await this.removeRosterEntry(ctx, state, idx);
      return;
    }

    if (cmd.startsWith("sr_up:")) {
      const idx = parseInt(cmd.slice(6), 10);
      await this.moveRosterEntry(ctx, state, idx, -1);
      return;
    }

    if (cmd.startsWith("sr_dn:")) {
      const idx = parseInt(cmd.slice(6), 10);
      await this.moveRosterEntry(ctx, state, idx, 1);
      return;
    }

    // ── Health Check ─────────────────────────────────────────
    if (cmd === "hc" || cmd === "hc_r") {
      this.stateManager.navigate(state.userId, "health_check");
      // Show "loading" immediately, then run checks
      await this.editScreen(ctx, state, renderHealthCheck({}, this.getConfig(), true));
      await this.runHealthCheck(ctx, state);
      return;
    }

    log.telegram.warn(`[ConfigMenu] Unhandled cfg command: ${cmd}`);
  }

  // ─── Screen helpers ───────────────────────────────────────────

  private async editScreen(
    ctx: Context,
    state: MenuState,
    content: { text: string; keyboard: import("grammy").InlineKeyboard },
  ): Promise<void> {
    try {
      await ctx.api.editMessageText(
        state.chatId,
        state.messageId,
        content.text,
        { parse_mode: "HTML", reply_markup: content.keyboard },
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // "message is not modified" — content identical, safe to ignore
      if (!msg.includes("message is not modified")) {
        log.telegram.warn(`[ConfigMenu] editScreen failed: ${msg}`);
      }
    }
  }

  private async renderCurrentScreen(ctx: Context, state: MenuState): Promise<void> {
    const config = this.getConfig();
    switch (state.screen) {
      case "main":
        await this.editScreen(ctx, state, renderMain(config));
        break;
      case "providers":
        await this.editScreen(ctx, state, renderProviders(config, this.lastHealth));
        break;
      case "provider_detail":
        await this.editScreen(ctx, state, renderProviderDetail(
          state.pendingProviderKey ?? "", config, this.lastHealth,
        ));
        break;
      case "provider_add_type":
        await this.editScreen(ctx, state, renderAddProviderType());
        break;
      case "model_roles":
        await this.editScreen(ctx, state, renderModelRoles(config));
        break;
      case "smart_routing":
        await this.editScreen(ctx, state, renderSmartRouting(config));
        break;
      case "health_check":
        await this.editScreen(ctx, state, renderHealthCheck(this.lastHealth, config, false));
        break;
      default:
        await this.editScreen(ctx, state, renderMain(config));
    }
  }

  // ─── Provider actions ─────────────────────────────────────────

  private async setDefaultProvider(
    ctx: Context,
    state: MenuState,
    providerKey: string,
  ): Promise<void> {
    const config = this.getConfig();
    if (!config.providers[providerKey]) {
      await this.editScreen(ctx, state, renderError(`Provider "${providerKey}" not found.`));
      return;
    }

    const entry = config.providers[providerKey]!;
    config.defaultProvider = providerKey;
    config.defaultModel    = entry.defaultModel ?? config.defaultModel;

    await this.saveConfigFn(config);
    log.telegram.info(`[ConfigMenu] Default provider set to "${providerKey}"`);

    await this.editScreen(ctx, state, renderProviderDetail(providerKey, config, this.lastHealth));
  }

  private async removeProvider(
    ctx: Context,
    state: MenuState,
    providerKey: string,
  ): Promise<void> {
    const config = this.getConfig();
    if (!config.providers[providerKey]) {
      await this.editScreen(ctx, state, renderError(`Provider "${providerKey}" not found.`));
      return;
    }

    if (providerKey === config.defaultProvider) {
      await this.editScreen(ctx, state, renderError(
        `Cannot remove the default provider (<b>${providerKey}</b>). Set another as default first.`,
      ));
      return;
    }

    delete config.providers[providerKey];
    await this.saveConfigFn(config);
    log.telegram.info(`[ConfigMenu] Removed provider "${providerKey}"`);

    // Go back to providers list
    this.stateManager.back(state.userId);
    await this.editScreen(ctx, state, renderProviders(config, this.lastHealth));
  }

  private async testProvider(
    ctx: Context,
    state: MenuState,
    providerKey: string,
  ): Promise<void> {
    // Show intermediate "testing…" state
    await this.editScreen(ctx, state, {
      text: `🔬 Testing <b>${providerKey}</b>…`,
      keyboard: new (await import("grammy")).InlineKeyboard().text("← Back", "cfg:bc"),
    });

    const start = Date.now();
    let ok = false;
    try {
      const provider = this.providerRegistry.get(providerKey);
      ok = await provider.healthCheck();
    } catch {
      ok = false;
    }
    const latencyMs = Date.now() - start;

    this.lastHealth[providerKey] = { ok, latencyMs };

    await this.editScreen(ctx, state, renderProviderDetail(
      providerKey, this.getConfig(), this.lastHealth,
    ));
  }

  // ─── Provider add flow ────────────────────────────────────────

  private async applyBaseUrl(
    ctx: Context,
    state: MenuState,
    providerType: string,
    url: string,
  ): Promise<void> {
    state.pendingEntry = state.pendingEntry ?? { providerType };
    state.pendingEntry.baseUrl = url;

    // Ollama / LMStudio don't need a key — auto-detect models
    if (["ollama", "lmstudio"].includes(providerType)) {
      await this.tryAutoDetectAndPickModel(ctx, state, providerType);
    } else {
      this.stateManager.navigate(state.userId, "provider_add_key");
      await this.editScreen(ctx, state, renderAddProviderKey(providerType, this.gatewayPort));
      state.pendingInput = { field: "apiKey", contextKey: providerType };
    }
  }

  private async applyApiKey(
    ctx: Context,
    state: MenuState,
    providerType: string,
    key: string,
  ): Promise<void> {
    // Basic format validation
    const validationError = this.validateKeyFormat(providerType, key);
    if (validationError) {
      await this.editScreen(ctx, state, renderError(validationError));
      // Re-set pending so they can try again
      state.pendingInput = { field: "apiKey", contextKey: providerType };
      return;
    }

    state.pendingEntry = state.pendingEntry ?? { providerType };
    state.pendingEntry.apiKey = key;

    // Move to model picker
    await this.openModelPicker(ctx, state, providerType, "add");
  }

  private validateKeyFormat(providerType: string, key: string): string | null {
    if (!key || key.length < 8) return "Key too short — please try again.";
    if (providerType === "anthropic" && !key.startsWith("sk-ant-")) {
      return "Invalid Anthropic key format. It should start with <code>sk-ant-</code>.";
    }
    if (providerType === "openai" && !key.startsWith("sk-")) {
      return "Invalid OpenAI key format. It should start with <code>sk-</code>.";
    }
    return null;
  }

  private async tryAutoDetectAndPickModel(
    ctx: Context,
    state: MenuState,
    providerType: string,
  ): Promise<void> {
    // Temporarily register a provider to fetch its model list
    const entry = state.pendingEntry ?? { providerType };
    const baseUrl = entry.baseUrl;

    // Show loading
    await this.editScreen(ctx, state, {
      text: `🔍 Connecting to <b>${PROVIDER_TYPE_META[providerType]?.label ?? providerType}</b>…`,
      keyboard: new (await import("grammy")).InlineKeyboard().text("← Cancel", "cfg:pa"),
    });

    let models: string[] = [];
    try {
      // Use provider-agnostic model list via the registry
      const provider = this.providerRegistry.get(providerType);
      models = await provider.listModels();
    } catch {
      // Provider not yet registered (being added for first time)
      // Try fetching directly based on type
      try {
        models = await this.fetchModelsDirectly(providerType, baseUrl);
      } catch (err) {
        await this.editScreen(ctx, state, renderError(
          `Could not fetch models: ${err instanceof Error ? err.message : err}\n\n` +
          `Make sure ${PROVIDER_TYPE_META[providerType]?.label ?? providerType} is running.`,
        ));
        return;
      }
    }

    state.modelList = models;
    this.stateManager.navigate(state.userId, "provider_model_pick");
    await this.editScreen(ctx, state, renderModelPicker(
      models,
      state.pendingEntry?.defaultModel,
      PROVIDER_TYPE_META[providerType]?.label ?? providerType,
    ));
  }

  private async fetchModelsDirectly(providerType: string, baseUrl?: string): Promise<string[]> {
    const ollamaUrl   = baseUrl ?? "http://127.0.0.1:11434";
    const lmstudioUrl = baseUrl ?? "http://127.0.0.1:1234";

    if (providerType === "ollama" || providerType === "ollama-cloud") {
      const res  = await fetch(`${ollamaUrl}/api/tags`);
      const json = await res.json() as { models?: Array<{ name: string }> };
      return (json.models ?? []).map((m) => m.name);
    }

    if (providerType === "lmstudio") {
      const res  = await fetch(`${lmstudioUrl}/v1/models`);
      const json = await res.json() as { data?: Array<{ id: string }> };
      return (json.data ?? []).map((m) => m.id);
    }

    if (providerType === "openai") {
      // Will fail without key — handled upstream
      return [];
    }

    if (providerType === "anthropic") {
      return ANTHROPIC_MODELS.map((m) => m.id);
    }

    return [];
  }

  private async openModelPicker(
    ctx: Context,
    state: MenuState,
    providerKey: string,
    context: "provider" | "add" | "role",
  ): Promise<void> {
    // Show loading first
    await this.editScreen(ctx, state, {
      text: `🔍 Loading models for <b>${providerKey}</b>…`,
      keyboard: new (await import("grammy")).InlineKeyboard().text("← Back", "cfg:bc"),
    });

    let models: string[] = [];
    try {
      if (providerKey === "anthropic") {
        models = ANTHROPIC_MODELS.map((m) => m.id);
      } else {
        const provider = this.providerRegistry.get(providerKey);
        models = await provider.listModels();
      }
    } catch (err) {
      await this.editScreen(ctx, state, renderError(
        `Could not load models: ${err instanceof Error ? err.message : err}`,
      ));
      return;
    }

    state.modelList        = models;
    state.pendingProviderKey = providerKey;

    const config   = this.getConfig();
    const current  = context === "role"
      ? ((config as any).modelRoles?.[state.pendingRole ?? ""]?.model)
      : config.providers[providerKey]?.defaultModel;
    const label    = context === "role"
      ? `${state.pendingRole} role via ${providerKey}`
      : providerKey;

    this.stateManager.navigate(state.userId, "provider_model_pick");
    await this.editScreen(ctx, state, renderModelPicker(models, current, label));
  }

  private async selectModel(
    ctx: Context,
    state: MenuState,
    idx: number,
  ): Promise<void> {
    const model = state.modelList?.[idx];
    if (!model) {
      await this.editScreen(ctx, state, renderError("Model not found — please try again."));
      return;
    }

    const role = state.pendingRole;

    if (role) {
      // Assigning to a model role
      await this.applyRoleModel(ctx, state, role, state.pendingRoleProvider ?? "", model);
    } else if (state.pendingEntry) {
      // Completing a provider add flow
      state.pendingEntry.defaultModel = model;
      await this.finalizeProviderAdd(ctx, state, state.pendingEntry.providerType, state.pendingEntry.apiKey);
    } else if (state.pendingProviderKey) {
      // Changing model for existing provider
      await this.applyProviderModel(ctx, state, state.pendingProviderKey, model);
    }
  }

  private async applyProviderModel(
    ctx: Context,
    state: MenuState,
    providerKey: string,
    model: string,
  ): Promise<void> {
    const config = this.getConfig();
    if (!config.providers[providerKey]) {
      await this.editScreen(ctx, state, renderError(`Provider "${providerKey}" not found.`));
      return;
    }

    config.providers[providerKey]!.defaultModel = model;
    if (providerKey === config.defaultProvider) {
      config.defaultModel = model;
    }

    await this.saveConfigFn(config);
    log.telegram.info(`[ConfigMenu] Model for "${providerKey}" → "${model}"`);

    // Return to provider detail
    this.stateManager.back(state.userId);
    await this.editScreen(ctx, state, renderProviderDetail(providerKey, config, this.lastHealth));
  }

  private async finalizeProviderAdd(
    ctx: Context,
    state: MenuState,
    providerType: string,
    apiKey: string | undefined,
  ): Promise<void> {
    const entry  = state.pendingEntry ?? { providerType };
    const config = this.getConfig();

    // Build the new ProviderConfigEntry
    const newEntry: ProviderConfigEntry = {
      baseUrl:      entry.baseUrl || undefined,
      apiKey:       apiKey || undefined,
      defaultModel: entry.defaultModel,
    };

    config.providers[providerType] = newEntry;
    await this.saveConfigFn(config);

    // Clear pending
    state.pendingEntry      = undefined;
    state.pendingProviderKey = providerType;

    log.telegram.info(`[ConfigMenu] Provider added: "${providerType}"`);

    // Go back to provider list with success context
    this.stateManager.set({ ...state, screen: "providers", breadcrumb: [] });
    await this.editScreen(ctx, state, renderSuccess(
      `Provider <b>${providerType}</b> added!\n` +
      `Model: <code>${entry.defaultModel ?? "—"}</code>`,
    ));
  }

  // ─── Model Roles ──────────────────────────────────────────────

  private async applyRoleModel(
    ctx: Context,
    state: MenuState,
    role: string,
    provider: string,
    model: string,
  ): Promise<void> {
    const config = this.getConfig();

    // Special cases: roles that map to top-level config keys
    if (role === "chat") {
      config.defaultProvider = provider;
      config.defaultModel    = model;
      if (config.providers[provider]) {
        config.providers[provider]!.defaultModel = model;
      }
    } else if (role === "synthesis") {
      config.synthesis = { provider, model };
    } else if (role === "embedding") {
      config.pellets = { ...config.pellets, embeddingModel: model };
    } else {
      // Generic modelRoles record
      (config as any).modelRoles = {
        ...((config as any).modelRoles ?? {}),
        [role]: { provider, model },
      };
    }

    await this.saveConfigFn(config);
    state.pendingRole         = undefined;
    state.pendingRoleProvider = undefined;

    log.telegram.info(`[ConfigMenu] Role "${role}" → ${provider}/${model}`);
    await this.editScreen(ctx, state, renderSuccess(
      `Role <b>${role}</b> assigned to:\n` +
      `<code>${provider}</code> · <code>${model}</code>`,
    ));
  }

  // ─── Smart Routing ────────────────────────────────────────────

  private async toggleSmartRouting(ctx: Context, state: MenuState): Promise<void> {
    const config  = this.getConfig();
    const enabled = !(config.smartRouting?.enabled ?? false);
    config.smartRouting = {
      ...config.smartRouting,
      enabled,
      availableModels: config.smartRouting?.availableModels ?? [],
    };
    await this.saveConfigFn(config);
    this.stateManager.navigate(state.userId, "smart_routing");
    await this.editScreen(ctx, state, renderSmartRouting(config));
  }

  private async addRosterEntry(
    ctx: Context,
    state: MenuState,
    providerName: string,
    modelName: string,
  ): Promise<void> {
    const config  = this.getConfig();
    const roster  = config.smartRouting?.availableModels ?? [];
    roster.push({ modelName, providerName });
    config.smartRouting = {
      ...config.smartRouting,
      enabled: config.smartRouting?.enabled ?? false,
      availableModels: roster,
    };
    await this.saveConfigFn(config);
    this.stateManager.back(state.userId);
    this.stateManager.back(state.userId);
    await this.editScreen(ctx, state, renderSmartRouting(config));
  }

  private async removeRosterEntry(
    ctx: Context,
    state: MenuState,
    idx: number,
  ): Promise<void> {
    const config = this.getConfig();
    const roster = config.smartRouting?.availableModels ?? [];
    roster.splice(idx, 1);
    config.smartRouting = {
      ...config.smartRouting,
      enabled: config.smartRouting?.enabled ?? false,
      availableModels: roster,
    };
    await this.saveConfigFn(config);
    await this.editScreen(ctx, state, renderSmartRouting(config));
  }

  private async moveRosterEntry(
    ctx: Context,
    state: MenuState,
    idx: number,
    direction: -1 | 1,
  ): Promise<void> {
    const config  = this.getConfig();
    const roster  = config.smartRouting?.availableModels ?? [];
    const swapIdx = idx + direction;
    if (swapIdx < 0 || swapIdx >= roster.length) return;
    [roster[idx], roster[swapIdx]] = [roster[swapIdx], roster[idx]];
    config.smartRouting = {
      ...config.smartRouting,
      enabled: config.smartRouting?.enabled ?? false,
      availableModels: roster,
    };
    await this.saveConfigFn(config);
    await this.editScreen(ctx, state, renderSmartRouting(config));
  }

  // ─── Health Check ─────────────────────────────────────────────

  private async runHealthCheck(ctx: Context, state: MenuState): Promise<void> {
    const config    = this.getConfig();
    const providers = Object.keys(config.providers);

    const results = await Promise.all(
      providers.map(async (key) => {
        const start = Date.now();
        let ok = false;
        try {
          const provider = this.providerRegistry.get(key);
          ok = await Promise.race([
            provider.healthCheck(),
            new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 5000)),
          ]);
        } catch { ok = false; }
        return { key, ok, latencyMs: Date.now() - start };
      }),
    );

    for (const r of results) {
      this.lastHealth[r.key] = { ok: r.ok, latencyMs: r.latencyMs };
    }

    await this.editScreen(ctx, state, renderHealthCheck(this.lastHealth, config, false));
  }

  // ─── Secure web form (Option C path B) ───────────────────────

  private async openWebForm(
    ctx: Context,
    state: MenuState,
    providerType: string,
  ): Promise<void> {
    const { randomUUID } = await import("node:crypto");
    const token = randomUUID();

    this.keyTokens.set(token, {
      providerKey: providerType,
      field: "apiKey",
      userId: state.userId,
      expiresAt: Date.now() + 5 * 60 * 1000,
    });

    // Evict expired tokens
    for (const [t, data] of this.keyTokens) {
      if (Date.now() > data.expiresAt) this.keyTokens.delete(t);
    }

    await this.editScreen(ctx, state, renderWebFormLink(providerType, token, this.gatewayPort));
  }

  /**
   * Called by the Express server when a web form key submission arrives.
   * Saves the key to config and updates the token as "used".
   */
  async consumeWebFormToken(token: string, apiKey: string): Promise<{ ok: boolean; message: string }> {
    const data = this.keyTokens.get(token);
    if (!data) return { ok: false, message: "Token not found or expired." };
    if (Date.now() > data.expiresAt) {
      this.keyTokens.delete(token);
      return { ok: false, message: "Token expired. Please try again." };
    }

    // Validate
    const err = this.validateKeyFormat(data.providerKey, apiKey);
    if (err) return { ok: false, message: err };

    const config = this.getConfig();
    config.providers[data.providerKey] = {
      ...config.providers[data.providerKey],
      apiKey,
    };
    await this.saveConfigFn(config);
    this.keyTokens.delete(token);
    log.telegram.info(`[ConfigMenu] API key for "${data.providerKey}" saved via web form.`);
    return { ok: true, message: `Key saved for ${data.providerKey}.` };
  }

  destroy(): void {
    this.stateManager.destroy();
  }
}

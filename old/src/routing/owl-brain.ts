// src/routing/owl-brain.ts
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";
import type { GatewayCallbacks, GatewayMessage } from "../gateway/types.js";
import type { EngineContext } from "../engine/runtime.js";
import type { Session } from "../memory/store.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { UserProfileService } from "./user-profile-service.js";
import type { SecretaryRouter } from "./secretary.js";
import type { ConversationDigestManager } from "../memory/conversation-digest.js";
import { log } from "../logger.js";

export interface OwlBrainResult {
  text: string;
  activeOwlName: string;
  parliamentHandled: boolean;
}

export class OwlBrain {
  private getSecretaryRouter: () => SecretaryRouter | null = () => null;
  private classifyFn: ((prompt: string) => Promise<string>) | null = null;

  constructor(
    private specializedRegistry: Pick<SpecializedOwlRegistry, "listSpecialists" | "get" | "getDefault"> | undefined,
    private db: Pick<MemoryDatabase, "userProfiles" | "owlPins">,
    private defaultOwlName: string,
    private userProfileService: UserProfileService | undefined,
    private digestManager: ConversationDigestManager | undefined,
  ) {}

  setSecretaryRouterGetter(fn: () => SecretaryRouter | null): void {
    this.getSecretaryRouter = fn;
  }

  setClassifyFn(fn: (prompt: string) => Promise<string>): void {
    this.classifyFn = fn;
  }

  private async parseNaturalLanguageMention(
    text: string,
    activeRoster: string[],
  ): Promise<{ targeted: string | null; confidence: number }> {
    if (activeRoster.length === 0 || !this.classifyFn) {
      return { targeted: null, confidence: 0 };
    }

    if (text.trim().split(/\s+/).length < 2) {
      return { targeted: null, confidence: 0 };
    }

    try {
      const prompt =
        `Message: "${text}"\n` +
        `Active helpers: [${activeRoster.join(", ")}]\n` +
        `Is the user explicitly addressing one of these helpers by name?\n` +
        `Reply JSON only: {"targeted": string|null, "confidence": 0-1}`;
      const raw = await this.classifyFn(prompt);
      return JSON.parse(raw);
    } catch {
      return { targeted: null, confidence: 0 };
    }
  }

  async resolve(
    text: string,
    message: GatewayMessage,
    engineCtx: EngineContext,
    callbacks: GatewayCallbacks,
    session?: Session,
  ): Promise<OwlBrainResult> {
    let activeOwlName = this.defaultOwlName;

    // 1. Restore SQLite pin on first message of session
    if (!session?.metadata.activeOwlName && message.userId && this.specializedRegistry) {
      const savedPin = this.db.owlPins.get(message.userId, message.channelId)
        ?? this.db.userProfiles.getPin(message.userId); // legacy global fallback
      if (savedPin && session) {
        const spec = this.specializedRegistry.get(savedPin);
        if (spec) {
          session.metadata.activeOwlName = savedPin;
          log.engine.info(`[OwlBrain] Restored SQLite pin "${savedPin}" for ${message.userId}`);
        } else {
          // Owl no longer exists — clear stale pin
          this.db.owlPins.set(message.userId, message.channelId, null, new Date().toISOString());
          log.engine.warn(`[OwlBrain] Cleared stale pin "${savedPin}" for ${message.userId} (owl not found)`);
        }
      }
    }

    // 2. Explicit @mention (allow hyphens in owl names, e.g. @ts-owl)
    const explicitMention = text.match(/^@([\w-]+)(?:\s+(.+))?$/s);
    if (explicitMention && this.specializedRegistry) {
      const [, owlName, rest] = explicitMention;
      if (owlName.toLowerCase() === this.defaultOwlName.toLowerCase()) {
        // @coordinator — clear pin
        if (session) session.metadata.activeOwlName = undefined;
        this.db.owlPins.set(message.userId, message.channelId, null, new Date().toISOString());
        text = rest?.trim() || "Hello";
        this.appendHistory(message.userId, this.defaultOwlName, "@coordinator clear");
        return { text, activeOwlName: this.defaultOwlName, parliamentHandled: false };
      }
      const spec = this.specializedRegistry.get(owlName);
      if (spec) {
        text = rest?.trim() || "Hello";
        if (session) session.metadata.activeOwlName = spec.name;
        this.db.owlPins.set(message.userId, message.channelId, spec.name, new Date().toISOString());
        this.applySpecialist(spec, engineCtx, callbacks);
        await this.injectMemoryContext(spec.name, message.sessionId, text, engineCtx);
        activeOwlName = spec.name;
        this.appendHistory(message.userId, spec.name, "@mention");
        log.engine.info(`[OwlBrain] @mention → "${spec.name}" (pinned)`);
        return { text, activeOwlName, parliamentHandled: false };
      } else {
        log.engine.warn(`[OwlBrain] @mention "${owlName}" not found in registry — ignored`);
      }
    }

    // 2b. Natural-language mention (runs when message doesn't start with @)
    if (!text.startsWith("@") && this.specializedRegistry && message.userId && this.classifyFn) {
      const roster = this.specializedRegistry.listSpecialists().map(s => s.name);
      const { targeted, confidence } = await this.parseNaturalLanguageMention(text, roster);
      if (targeted && confidence >= 0.75) {
        const spec = this.specializedRegistry.get(targeted);
        if (spec) {
          if (session) session.metadata.activeOwlName = spec.name;
          this.db.owlPins.set(message.userId, message.channelId, spec.name, new Date().toISOString());
          this.applySpecialist(spec, engineCtx, callbacks);
          await this.injectMemoryContext(spec.name, message.sessionId, text, engineCtx);
          activeOwlName = spec.name;
          this.appendHistory(message.userId, spec.name, `nl-mention@${confidence.toFixed(2)}`);
          log.engine.info(`[OwlBrain] NL mention → "${spec.name}" (conf=${confidence.toFixed(2)})`);
          return { text, activeOwlName, parliamentHandled: false };
        }
      }
    }

    // 3. Soft-pin miss counter check (runs before session pin resume to allow TTL clearing)
    if (session?.metadata.activeOwlName && this.specializedRegistry && message.userId) {
      const router = this.getSecretaryRouter();
      if (router) {
        const signals = this.userProfileService
          ? await this.userProfileService.buildSignals(message.userId, text)
          : { activePin: null, domainStack: [], recentEpisodes: [], relevantFacts: [], trustLevel: "standard" as const };

        const decision = await router.routeWithSignals(text, message.userId, signals);

        if (decision.type === "specialist" && decision.owl.name === session.metadata.activeOwlName) {
          session.metadata.softPinMissCount = 0;
        } else {
          session.metadata.softPinMissCount = (session.metadata.softPinMissCount ?? 0) + 1;
          if (session.metadata.softPinMissCount >= 3) {
            session.metadata.activeOwlName = undefined;
            session.metadata.softPinMissCount = 0;
            log.engine.info(`[OwlBrain] Soft-pin cleared after 3 consecutive misses`);
          }
        }
      }
    }

    // 3b. Session pin resume (only if soft-pin TTL hasn't cleared the pin above)
    if (session?.metadata.activeOwlName && this.specializedRegistry) {
      const pinnedSpec = this.specializedRegistry.get(session.metadata.activeOwlName);
      if (pinnedSpec) {
        this.applySpecialist(pinnedSpec, engineCtx, callbacks);
        await this.injectMemoryContext(pinnedSpec.name, message.sessionId, text, engineCtx);
        this.appendHistory(message.userId, pinnedSpec.name, "pin-resume");
        return { text, activeOwlName: pinnedSpec.name, parliamentHandled: false };
      } else {
        session.metadata.activeOwlName = undefined;
        // Also clear SQLite pin — owl no longer exists
        if (message.userId) {
          this.db.owlPins.set(message.userId, message.channelId, null, new Date().toISOString());
        }
      }
    }

    // 4. Signal-aware routing (soft-pin — session only, no SQLite write)
    if (this.specializedRegistry && message.userId) {
      const router = this.getSecretaryRouter();
      if (router && !session?.metadata.activeOwlName) {
        const signals = this.userProfileService
          ? await this.userProfileService.buildSignals(message.userId, text)
          : { activePin: null, domainStack: [], recentEpisodes: [], relevantFacts: [], trustLevel: "standard" as const };

        const decision = await router.routeWithSignals(text, message.userId, signals);

        if (decision.type === "specialist") {
          if (session) {
            session.metadata.activeOwlName = decision.owl.name;
            session.metadata.softPinMissCount = 0;
          }
          // NOTE: do NOT call db.owlPins.set() here — soft pin is session-only
          this.applySpecialist(decision.owl, engineCtx, callbacks);
          await this.injectMemoryContext(decision.owl.name, message.sessionId, text, engineCtx);
          activeOwlName = decision.owl.name;
          this.appendHistory(message.userId, decision.owl.name, decision.reason);
          log.engine.info(`[OwlBrain] signals → "${decision.owl.name}" (soft-pin, ${decision.reason})`);
        } else if (decision.type === "parliament") {
          this.appendHistory(message.userId, "parliament", "parliament trigger");
          return { text, activeOwlName, parliamentHandled: true };
        } else {
          this.appendHistory(message.userId, this.defaultOwlName, decision.reason);
        }
      }
    }

    // 5. No routing — default owl, record history
    if (!this.specializedRegistry || !this.getSecretaryRouter()) {
      this.appendHistory(message.userId, this.defaultOwlName, "no routing configured");
    }

    return { text, activeOwlName, parliamentHandled: false };
  }

  private appendHistory(userId: string, owl: string, reason: string): void {
    try {
      this.db.userProfiles.appendRoutingHistory(userId, { ts: new Date().toISOString(), owl, reason });
    } catch { /* non-critical */ }
  }

  private buildSpecialistPrompt(spec: SpecializedOwlSpec): string {
    return [
      `You are ${spec.name}, ${spec.role}.`,
      spec.expertise.length > 0 ? `Your expertise: ${spec.expertise.join(", ")}.` : "",
      `Communication style: ${spec.personality.challengeLevel} challenge level, ${spec.personality.verbosity} verbosity, ${spec.personality.tone} tone.`,
      spec.permissions.capabilityConstraints.length > 0 ? `Constraints: ${spec.permissions.capabilityConstraints.join("; ")}.` : "",
      spec.additionalPrompt ? spec.additionalPrompt : "",
    ].filter(Boolean).join(" ");
  }

  private applySpecialist(spec: SpecializedOwlSpec, engineCtx: EngineContext, callbacks: GatewayCallbacks): void {
    const specialistPrompt = this.buildSpecialistPrompt(spec);
    engineCtx.owl = {
      ...engineCtx.owl,
      specialistPrompt,
      specialistRoutingRules: spec.routingRules?.keywords,
      specialistPermissions: spec.permissions,
    };
    engineCtx.specialistPrompt = specialistPrompt;
    callbacks?.onOwlChange?.(spec.emoji || "🦉", spec.name);
  }

  private async injectMemoryContext(owlName: string, sessionId: string, _userMessage: string, engineCtx: EngineContext): Promise<void> {
    void owlName;
    const parts: string[] = [];
    if (this.digestManager) {
      try {
        const digest = await this.digestManager.load(sessionId);
        if (digest?.task) {
          parts.push(`## Session Context\nTask: ${digest.task}`);
        }
      } catch { /* non-critical */ }
    }
    if (parts.length > 0) {
      const existing = engineCtx.specialistPrompt ?? "";
      engineCtx.specialistPrompt = existing + "\n\n" + parts.join("\n\n");
      engineCtx.owl = { ...engineCtx.owl, specialistPrompt: engineCtx.specialistPrompt };
    }
  }
}

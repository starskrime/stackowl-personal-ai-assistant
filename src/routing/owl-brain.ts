// src/routing/owl-brain.ts
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";
import type { GatewayCallbacks, GatewayMessage } from "../gateway/types.js";
import type { EngineContext } from "../engine/runtime.js";
import type { Session } from "../memory/store.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { UserProfileService } from "./user-profile-service.js";
import type { SecretaryRouter } from "./secretary.js";
import type { PelletStore } from "../pellets/store.js";
import type { ConversationDigestManager } from "../memory/conversation-digest.js";
import { log } from "../logger.js";

export interface OwlBrainResult {
  text: string;
  activeOwlName: string;
  parliamentHandled: boolean;
}

export class OwlBrain {
  private getSecretaryRouter: () => SecretaryRouter | null = () => null;

  constructor(
    private specializedRegistry: Pick<SpecializedOwlRegistry, "listSpecialists" | "get" | "getDefault"> | undefined,
    private db: Pick<MemoryDatabase, "userProfiles">,
    private defaultOwlName: string,
    private userProfileService: UserProfileService | undefined,
    private pelletStore: PelletStore | undefined,
    private digestManager: ConversationDigestManager | undefined,
  ) {}

  setSecretaryRouterGetter(fn: () => SecretaryRouter | null): void {
    this.getSecretaryRouter = fn;
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
      const savedPin = this.db.userProfiles.getPin(message.userId);
      if (savedPin && session) {
        const spec = this.specializedRegistry.get(savedPin);
        if (spec) {
          session.metadata.activeOwlName = savedPin;
          log.engine.info(`[OwlBrain] Restored SQLite pin "${savedPin}" for ${message.userId}`);
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
        this.db.userProfiles.setPin(message.userId, null);
        text = rest?.trim() || "Hello";
        this.appendHistory(message.userId, this.defaultOwlName, "@coordinator clear");
        return { text, activeOwlName: this.defaultOwlName, parliamentHandled: false };
      }
      const spec = this.specializedRegistry.get(owlName);
      if (spec) {
        text = rest?.trim() || "Hello";
        if (session) session.metadata.activeOwlName = spec.name;
        this.db.userProfiles.setPin(message.userId, spec.name);
        this.applySpecialist(spec, engineCtx, callbacks);
        await this.injectMemoryContext(spec.name, message.sessionId, text, engineCtx);
        activeOwlName = spec.name;
        this.appendHistory(message.userId, spec.name, "@mention");
        log.engine.info(`[OwlBrain] @mention → "${spec.name}" (pinned)`);
        return { text, activeOwlName, parliamentHandled: false };
      }
    }

    // 3. Session pin resume
    if (session?.metadata.activeOwlName && this.specializedRegistry) {
      const pinnedSpec = this.specializedRegistry.get(session.metadata.activeOwlName);
      if (pinnedSpec) {
        this.applySpecialist(pinnedSpec, engineCtx, callbacks);
        await this.injectMemoryContext(pinnedSpec.name, message.sessionId, text, engineCtx);
        this.appendHistory(message.userId, pinnedSpec.name, "pin-resume");
        return { text, activeOwlName: pinnedSpec.name, parliamentHandled: false };
      }
      session.metadata.activeOwlName = undefined;
    }

    // 4. Signal-aware routing
    if (this.specializedRegistry && message.userId) {
      const router = this.getSecretaryRouter();
      if (router) {
        const signals = this.userProfileService
          ? await this.userProfileService.buildSignals(message.userId, text)
          : { activePin: null, domainStack: [], recentEpisodes: [], relevantFacts: [], trustLevel: "standard" as const };

        const decision = await router.routeWithSignals(text, message.userId, signals);

        if (decision.type === "specialist") {
          if (session) session.metadata.activeOwlName = decision.owl.name;
          this.db.userProfiles.setPin(message.userId, decision.owl.name);
          this.applySpecialist(decision.owl, engineCtx, callbacks);
          await this.injectMemoryContext(decision.owl.name, message.sessionId, text, engineCtx);
          activeOwlName = decision.owl.name;
          this.appendHistory(message.userId, decision.owl.name, decision.reason);
          log.engine.info(`[OwlBrain] signals → "${decision.owl.name}" (${decision.reason})`);
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

  private async injectMemoryContext(owlName: string, sessionId: string, userMessage: string, engineCtx: EngineContext): Promise<void> {
    const parts: string[] = [];
    if (this.digestManager) {
      try {
        const digest = await this.digestManager.load(sessionId);
        if (digest?.task) {
          parts.push(`## Session Context\nTask: ${digest.task}`);
        }
      } catch { /* non-critical */ }
    }
    if (this.pelletStore) {
      try {
        const pellets = await this.pelletStore.search(userMessage, 3);
        const lines = pellets
          .filter(p => p.owls.includes(owlName) || p.owls.length === 0)
          .map(p => `- ${p.title}: ${p.content.slice(0, 120)}`)
          .join("\n");
        if (lines) parts.push(`## Related Memory\n${lines}`);
      } catch { /* non-critical */ }
    }
    if (parts.length > 0) {
      const existing = engineCtx.specialistPrompt ?? "";
      engineCtx.specialistPrompt = existing + "\n\n" + parts.join("\n\n");
      engineCtx.owl = { ...engineCtx.owl, specialistPrompt: engineCtx.specialistPrompt };
    }
  }
}

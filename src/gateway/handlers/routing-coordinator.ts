import type { SpecializedOwlRegistry } from "../../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../../owls/specialized-types.js";
import type { SecretaryRouter } from "../../routing/secretary.js";
import type { GatewayCallbacks, GatewayMessage } from "../types.js";
import type { EngineContext } from "../../engine/runtime.js";
import type { Session } from "../../memory/store.js";
import type { SessionStateStore } from "../../routing/session-state.js";
import { log } from "../../logger.js";

export interface RoutingResult {
  text: string;
  activeOwlName: string;
  parliamentHandled: boolean;
}

export class RoutingCoordinator {
  constructor(
    private specializedRegistry: SpecializedOwlRegistry | undefined,
    private getSecretaryRouter: () => SecretaryRouter | null,
    private defaultOwlName: string,
    private sessionStateStore?: SessionStateStore,
  ) {}

  async resolve(
    text: string,
    message: GatewayMessage,
    engineCtx: EngineContext,
    callbacks: GatewayCallbacks,
    session?: Session,
  ): Promise<RoutingResult> {
    let activeOwlName = this.defaultOwlName;

    // ─── Restore pin from file on first message ──────────────────
    if (!session?.metadata.activeOwlName && message.userId && this.sessionStateStore) {
      const saved = await this.sessionStateStore.load(message.userId);
      if (saved && session) {
        session.metadata.activeOwlName = saved.activeOwlName;
        log.engine.info(`[RoutingCoordinator] Restored pin "${saved.activeOwlName}" for user ${message.userId}`);
      }
    }

    // ─── Explicit @mention ──────────────────────────────────────
    const explicitMention = text.match(/^@(\w+)(?:\s+(.+))?$/s);
    if (explicitMention && this.specializedRegistry) {
      const [, owlName, remainingMessage] = explicitMention;
      const coordinatorName = this.specializedRegistry.getDefault()?.name ?? "";

      if (owlName.toLowerCase() === coordinatorName.toLowerCase()) {
        // @noctua (or any coordinator name) — clear session pin
        if (session) session.metadata.activeOwlName = undefined;
        if (this.sessionStateStore && message.userId) {
          this.sessionStateStore.clear(message.userId).catch(() => {});
        }
        text = remainingMessage?.trim() || "Hello";
        log.engine.info(`[RoutingCoordinator] @${owlName} cleared specialist pin`);
        return { text, activeOwlName: this.defaultOwlName, parliamentHandled: false };
      }

      const spec = this.specializedRegistry.get(owlName);
      if (spec) {
        text = remainingMessage?.trim() || "Hello";
        if (session) session.metadata.activeOwlName = spec.name;
        if (this.sessionStateStore && message.userId) {
          this.sessionStateStore.save(message.userId, { activeOwlName: spec.name, pinnedAt: new Date().toISOString() }).catch(() => {});
        }
        this.applySpecialist(spec, engineCtx, callbacks);
        activeOwlName = spec.name;
        log.engine.info(`[RoutingCoordinator] @mention → "${spec.name}" (pinned)`);
        return { text, activeOwlName, parliamentHandled: false };
      }
      log.engine.warn(`[RoutingCoordinator] @mention "${owlName}" not found in registry`);
    }

    // ─── Session pin check ───────────────────────────────────────
    if (session?.metadata.activeOwlName && this.specializedRegistry) {
      const pinnedSpec = this.specializedRegistry.get(session.metadata.activeOwlName);
      if (pinnedSpec) {
        this.applySpecialist(pinnedSpec, engineCtx, callbacks);
        log.engine.info(`[RoutingCoordinator] Resuming pinned specialist "${pinnedSpec.name}"`);
        return { text, activeOwlName: pinnedSpec.name, parliamentHandled: false };
      }
      // Pinned owl no longer exists — clear stale pin
      session.metadata.activeOwlName = undefined;
    }

    // ─── SecretaryRouter implicit routing ───────────────────────
    if (this.specializedRegistry && message.userId) {
      const router = this.getSecretaryRouter();
      if (!router) {
        log.engine.warn("[RoutingCoordinator] SecretaryRouter not available — skipping specialist routing");
        return { text, activeOwlName, parliamentHandled: false };
      }

      const routingDecision = await router.route(text, message.userId);

      if (routingDecision.type === "specialist") {
        const spec = routingDecision.owl;
        if (session) session.metadata.activeOwlName = spec.name;
        if (this.sessionStateStore && message.userId) {
          this.sessionStateStore.save(message.userId, { activeOwlName: spec.name, pinnedAt: new Date().toISOString() }).catch(() => {});
        }
        this.applySpecialist(spec, engineCtx, callbacks);
        activeOwlName = spec.name;
        log.engine.info(`[RoutingCoordinator] Routed to "${spec.name}" (pinned)`);
      } else if (routingDecision.type === "parliament") {
        log.engine.info(`[RoutingCoordinator] Parliament triggered`);
        return { text, activeOwlName, parliamentHandled: true };
      }
    } else if (!this.specializedRegistry) {
      log.engine.warn("[RoutingCoordinator] specializedRegistry not loaded — specialist routing skipped");
    }

    return { text, activeOwlName, parliamentHandled: false };
  }

  private buildSpecialistPrompt(spec: SpecializedOwlSpec): string {
    return [
      `You are ${spec.name}, ${spec.role}.`,
      spec.expertise.length > 0 ? `Your expertise: ${spec.expertise.join(", ")}.` : "",
      `Communication style: ${spec.personality.challengeLevel} challenge level, ${spec.personality.verbosity} verbosity, ${spec.personality.tone} tone.`,
      spec.permissions.capabilityConstraints.length > 0
        ? `Constraints: ${spec.permissions.capabilityConstraints.join("; ")}.`
        : "",
      spec.additionalPrompt ? spec.additionalPrompt : "",
    ].filter(Boolean).join(" ");
  }

  private applySpecialist(spec: SpecializedOwlSpec, engineCtx: EngineContext, callbacks: GatewayCallbacks): void {
    const specialistPrompt = this.buildSpecialistPrompt(spec);
    engineCtx.owl = {
      ...engineCtx.owl,
      specialistPrompt,
      specialistRoutingRules: spec.routingRules.keywords,
      specialistPermissions: spec.permissions,
    };
    engineCtx.specialistPrompt = specialistPrompt;
    callbacks?.onOwlChange?.(spec.emoji || "🦉", spec.name);
  }
}

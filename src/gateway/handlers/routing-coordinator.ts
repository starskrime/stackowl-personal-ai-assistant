import type { SpecializedOwlRegistry } from "../../owls/specialized-registry.js";
import type { SecretaryRouter } from "../../routing/secretary.js";
import type { GatewayCallbacks, GatewayMessage } from "../types.js";
import type { EngineContext } from "../../engine/runtime.js";
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
    private owlRegistry: { get(name: string): unknown; getDefault(): unknown } | undefined,
    private defaultOwlName: string,
  ) {}

  async resolve(
    text: string,
    message: GatewayMessage,
    engineCtx: EngineContext,
    callbacks: GatewayCallbacks,
  ): Promise<RoutingResult> {
    let activeOwlName = this.defaultOwlName;

    // ─── Explicit @mention ──────────────────────────────────────
    const explicitMention = text.match(/^@(\w+)(?:\s+(.+))?$/s);
    if (explicitMention && this.specializedRegistry) {
      const [, owlName, remainingMessage] = explicitMention;
      const spec = this.specializedRegistry.get(owlName);
      if (spec) {
        text = remainingMessage?.trim() || "Hello";
        const baseOwl = (this.owlRegistry?.getDefault() ?? engineCtx.owl) as typeof engineCtx.owl;
        const specialistPrompt = [
          `You are ${spec.name}, ${spec.role}.`,
          spec.expertise.length > 0 ? `Your expertise: ${spec.expertise.join(", ")}.` : "",
          `Communication style: ${spec.personality.challengeLevel} challenge level, ${spec.personality.verbosity} verbosity, ${spec.personality.tone} tone.`,
          spec.permissions.capabilityConstraints.length > 0
            ? `Constraints: ${spec.permissions.capabilityConstraints.join("; ")}.`
            : "",
        ].filter(Boolean).join(" ");
        engineCtx.owl = {
          ...baseOwl,
          specialistPrompt,
          specialistRoutingRules: spec.routingRules.keywords,
          specialistPermissions: spec.permissions,
        };
        engineCtx.specialistPrompt = specialistPrompt;
        activeOwlName = spec.name;
        callbacks?.onOwlChange?.(spec.emoji || "🦉", spec.name);
        log.engine.info(`[RoutingCoordinator] @mention → "${spec.name}"`);
      } else {
        log.engine.warn(`[RoutingCoordinator] @mention "${owlName}" not found in registry`);
      }
    }

    // ─── SecretaryRouter implicit routing ───────────────────────
    if (this.specializedRegistry && message.userId && activeOwlName === this.defaultOwlName) {
      const router = this.getSecretaryRouter();
      if (!router) {
        log.engine.warn("[RoutingCoordinator] SecretaryRouter not available — skipping specialist routing");
        return { text, activeOwlName, parliamentHandled: false };
      }

      const routingDecision = await router.route(text, message.userId);

      if (routingDecision.type === "specialist") {
        const specializedOwl = routingDecision.owl;
        const spec = this.specializedRegistry.get(specializedOwl.name);
        const baseOwl = (
          (this.owlRegistry as { get(n: string): unknown; getDefault(): unknown } | undefined)
            ?.get(specializedOwl.name)
          ?? this.owlRegistry?.getDefault()
          ?? engineCtx.owl
        ) as typeof engineCtx.owl;
        engineCtx.owl = {
          ...baseOwl,
          specialistPrompt: specializedOwl.personalityPrompt,
          specialistRoutingRules: specializedOwl.routingRules,
          specialistPermissions: spec?.permissions,
        };
        engineCtx.specialistPrompt = specializedOwl.personalityPrompt;
        activeOwlName = specializedOwl.name;
        callbacks?.onOwlChange?.(spec?.emoji || "🦉", specializedOwl.name);
        log.engine.info(`[RoutingCoordinator] Routed to "${specializedOwl.name}"`);
      } else if (routingDecision.type === "parliament") {
        log.engine.info(`[RoutingCoordinator] Parliament triggered`);
        return { text, activeOwlName, parliamentHandled: true };
      }
    } else if (!this.specializedRegistry && message.userId && activeOwlName === this.defaultOwlName) {
      log.engine.warn("[RoutingCoordinator] specializedRegistry not loaded — specialist routing skipped");
    }

    return { text, activeOwlName, parliamentHandled: false };
  }
}

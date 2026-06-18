/**
 * StackOwl — Conversation-Driven Auto-Config
 *
 * Watches for infrastructure mentions in conversations and
 * suggests connector configurations and health checks.
 * Zero LLM cost — uses pattern matching only.
 */

import type { ChatMessage } from "../providers/base.js";
import type { InfraProfileStore } from "./profile.js";
import type { ConnectorResolver } from "../connectors/resolver.js";
import { listPresets } from "../connectors/presets.js";
import type { HealthCheck } from "../monitoring/types.js";
import { log } from "../logger.js";

export interface ConfigSuggestion {
  type: "connector" | "health-check";
  name: string;
  description: string;
  presetId?: string; // for connector suggestions
  healthCheck?: HealthCheck; // for monitoring suggestions
  confidence: number; // 0-1
}

// Maps infrastructure keywords to connector preset IDs
const KEYWORD_TO_PRESET: Array<{ pattern: RegExp; presetId: string }> = [
  { pattern: /\bgithub\b/i, presetId: "github" },
  { pattern: /\bgitlab\b/i, presetId: "gitlab" },
  { pattern: /\b(?:aws|amazon|ec2|s3|lambda)\b/i, presetId: "aws" },
  {
    pattern: /\b(?:kubernetes|k8s|kubectl|pods?|deploy(?:ment)?s?)\b/i,
    presetId: "kubernetes",
  },
  { pattern: /\b(?:postgres(?:ql)?|pg)\b/i, presetId: "postgres" },
  { pattern: /\bsqlite\b/i, presetId: "sqlite" },
  { pattern: /\bsentry\b/i, presetId: "sentry" },
  { pattern: /\bslack\b/i, presetId: "slack" },
  { pattern: /\blinear\b/i, presetId: "linear" },
  { pattern: /\bssh\b/i, presetId: "ssh" },
];

// URL patterns that suggest health checks
const URL_PATTERN = /https?:\/\/[^\s<>"']+/gi;

export class AutoConfigDetector {
  private suggestedPresets = new Set<string>();

  constructor(
    _profileStore: InfraProfileStore | undefined,
    private connectorResolver: ConnectorResolver | undefined,
  ) {}

  /**
   * Analyze recent messages for infrastructure mentions.
   * Returns suggestions for connectors and health checks.
   */
  analyze(messages: ChatMessage[]): ConfigSuggestion[] {
    const suggestions: ConfigSuggestion[] = [];
    const alreadyConfigured = new Set(
      this.connectorResolver?.getInstances().map((i) => i.presetId) ?? [],
    );

    for (const msg of messages) {
      if (typeof msg.content !== "string") continue;
      const text = msg.content;

      // Check for connector preset matches
      for (const { pattern, presetId } of KEYWORD_TO_PRESET) {
        if (!pattern.test(text)) continue;
        if (alreadyConfigured.has(presetId)) continue;
        if (this.suggestedPresets.has(presetId)) continue;

        const preset = listPresets().find((p) => p.id === presetId);
        if (!preset) continue;

        suggestions.push({
          type: "connector",
          name: preset.name,
          description: `I noticed you mentioned ${preset.name}. Want me to connect to it? I'll need: ${preset.requiredEnv.join(", ")}`,
          presetId,
          confidence: 0.7,
        });

        this.suggestedPresets.add(presetId);
      }

      // Detect URLs that could be health-checked
      const urls = text.match(URL_PATTERN);
      if (urls && msg.role === "user") {
        for (const url of urls) {
          try {
            const parsed = new URL(url);
            // Only suggest for non-common URLs (not google, stackoverflow, etc.)
            const common = [
              "google.com",
              "stackoverflow.com",
              "github.com",
              "npmjs.com",
              "docs.",
            ];
            if (common.some((c) => parsed.hostname.includes(c))) continue;

            suggestions.push({
              type: "health-check",
              name: parsed.hostname,
              description: `Want me to monitor ${parsed.hostname}?`,
              healthCheck: {
                id: `check-${parsed.hostname.replace(/\./g, "-")}`,
                name: parsed.hostname,
                type: "http",
                target: `${parsed.protocol}//${parsed.host}`,
                intervalSeconds: 300,
                timeoutMs: 10_000,
                failThreshold: 2,
                enabled: true,
                tags: ["auto-detected"],
              },
              confidence: 0.5,
            });
          } catch {
            // invalid URL
          }
        }
      }
    }

    if (suggestions.length > 0) {
      log.engine.info(
        `[AutoConfig] ${suggestions.length} suggestion(s) detected`,
      );
    }

    return suggestions;
  }

  /**
   * Generate a prompt injection snippet for suggesting configs to the user.
   */
  toPromptSnippet(suggestions: ConfigSuggestion[]): string {
    if (suggestions.length === 0) return "";

    const lines = [
      "\n[SYSTEM NOTE: Infrastructure detected in conversation. You may suggest these to the user naturally:]",
    ];
    for (const s of suggestions) {
      lines.push(`- ${s.description}`);
    }
    return lines.join("\n");
  }
}

/**
 * StackOwl — Infrastructure Detector
 *
 * Extracts infrastructure mentions from conversation messages using
 * keyword matching (zero LLM cost). Feeds into InfraProfileStore.
 */

import type { ChatMessage } from "../providers/base.js";
import type { InfraProfileStore } from "./profile.js";
import type { InfraService } from "./types.js";
import { log } from "../logger.js";

// Pattern-based detection — zero LLM cost
const SERVICE_PATTERNS: Array<{
  pattern: RegExp;
  type: InfraService["type"];
  provider?: string;
}> = [
  // Databases
  {
    pattern: /\b(?:postgres(?:ql)?|pg)\b/i,
    type: "database",
    provider: undefined,
  },
  { pattern: /\bmysql\b/i, type: "database" },
  { pattern: /\bmongo(?:db)?\b/i, type: "database" },
  { pattern: /\bredis\b/i, type: "cache" },
  { pattern: /\bmemcached\b/i, type: "cache" },
  { pattern: /\belasticsearch\b/i, type: "database" },
  { pattern: /\bdynamodb\b/i, type: "database", provider: "aws" },
  { pattern: /\b(?:rds)\b/i, type: "database", provider: "aws" },
  // Queues
  { pattern: /\b(?:rabbitmq|amqp)\b/i, type: "queue" },
  { pattern: /\bkafka\b/i, type: "queue" },
  { pattern: /\bsqs\b/i, type: "queue", provider: "aws" },
  // Cloud
  {
    pattern: /\b(?:aws|amazon web services)\b/i,
    type: "other",
    provider: "aws",
  },
  { pattern: /\b(?:gcp|google cloud)\b/i, type: "other", provider: "gcp" },
  { pattern: /\bazure\b/i, type: "other", provider: "azure" },
  { pattern: /\bvercel\b/i, type: "other", provider: "vercel" },
  { pattern: /\brailway\b/i, type: "other", provider: "railway" },
  // CI/CD
  { pattern: /\bgithub actions?\b/i, type: "ci", provider: "github" },
  { pattern: /\bjenkins\b/i, type: "ci" },
  { pattern: /\bgitlab ci\b/i, type: "ci", provider: "gitlab" },
  // Monitoring
  { pattern: /\bgrafana\b/i, type: "monitoring" },
  { pattern: /\bdatadog\b/i, type: "monitoring" },
  { pattern: /\bprometheus\b/i, type: "monitoring" },
  { pattern: /\bsentry\b/i, type: "monitoring" },
  // Storage
  { pattern: /\bs3\b/i, type: "storage", provider: "aws" },
  { pattern: /\bgcs\b/i, type: "storage", provider: "gcp" },
  // CDN
  { pattern: /\bcloudflare\b/i, type: "cdn" },
  { pattern: /\bcloudfront\b/i, type: "cdn", provider: "aws" },
  // Auth
  { pattern: /\bauth0\b/i, type: "auth" },
  { pattern: /\bcognito\b/i, type: "auth", provider: "aws" },
  { pattern: /\bkeycloak\b/i, type: "auth" },
  // Apps
  { pattern: /\bnginx\b/i, type: "app" },
  { pattern: /\bkubernetes|k8s\b/i, type: "app" },
  { pattern: /\bdocker\b/i, type: "app" },
];

const TECH_STACK_PATTERNS: RegExp[] = [
  /\b(?:react|next\.?js|vue|angular|svelte)\b/i,
  /\b(?:node\.?js|express|fastify|nest\.?js)\b/i,
  /\b(?:python|django|flask|fastapi)\b/i,
  /\b(?:go(?:lang)?|rust|java|kotlin|swift)\b/i,
  /\b(?:typescript|javascript)\b/i,
  /\b(?:ruby|rails)\b/i,
  /\b(?:php|laravel)\b/i,
  /\b(?:terraform|pulumi|cdk)\b/i,
];

const URL_PATTERN = /https?:\/\/[^\s<>"']+/gi;

export class InfraDetector {
  constructor(private profileStore: InfraProfileStore) {}

  /**
   * Scan messages for infrastructure mentions and update the profile.
   * Designed to run after every conversation turn — zero LLM cost.
   */
  processMessages(messages: ChatMessage[]): void {
    let detected = 0;
    const providers = new Set<string>();
    const techStack = new Set<string>();

    for (const msg of messages) {
      if (msg.role !== "user" || typeof msg.content !== "string") continue;
      const text = msg.content;

      // Detect services
      for (const { pattern, type, provider } of SERVICE_PATTERNS) {
        const match = text.match(pattern);
        if (match) {
          const name = match[0].toLowerCase();
          this.profileStore.addService("default", {
            name,
            type,
            provider,
            tags: [],
            notes: "",
          });
          if (provider) providers.add(provider);
          detected++;
        }
      }

      // Detect tech stack
      for (const pattern of TECH_STACK_PATTERNS) {
        const match = text.match(pattern);
        if (match) {
          techStack.add(match[0].toLowerCase());
        }
      }

      // Extract URLs and associate with mentioned services
      const urls = text.match(URL_PATTERN);
      if (urls) {
        for (const url of urls) {
          try {
            const hostname = new URL(url).hostname;
            // Try to match URL to a known service
            for (const { pattern } of SERVICE_PATTERNS) {
              if (pattern.test(hostname)) {
                const services = this.profileStore.findService(hostname);
                if (services.length > 0) {
                  services[0].service.url = url;
                }
              }
            }
          } catch {
            // invalid URL, skip
          }
        }
      }
    }

    if (techStack.size > 0) {
      this.profileStore.setTechStack([...techStack]);
    }
    if (providers.size > 0) {
      // Set most frequently mentioned as primary
      this.profileStore.setPrimaryProvider([...providers][0]);
    }

    if (detected > 0) {
      log.engine.info(
        `[InfraDetector] Detected ${detected} infrastructure mentions`,
      );
    }
  }
}

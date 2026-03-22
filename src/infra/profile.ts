/**
 * StackOwl — Infrastructure Profile Store
 *
 * Persists and queries the user's infrastructure model.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join, dirname } from "node:path";
import { existsSync } from "node:fs";
import type { InfraProfile, InfraService, InfraConnection, InfraEnvironment } from "./types.js";
import { log } from "../logger.js";

const EMPTY_PROFILE: InfraProfile = {
  version: 1,
  environments: [],
  lastUpdated: 0,
  metadata: { totalServices: 0, techStack: [] },
};

export class InfraProfileStore {
  private profile: InfraProfile = { ...EMPTY_PROFILE, environments: [] };
  private filePath: string;
  private dirty = false;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "infra-profile.json");
  }

  async load(): Promise<void> {
    try {
      if (existsSync(this.filePath)) {
        const raw = await readFile(this.filePath, "utf-8");
        this.profile = JSON.parse(raw);
        log.engine.info(`[InfraProfile] Loaded ${this.profile.metadata.totalServices} services`);
      }
    } catch (err) {
      log.engine.warn(`[InfraProfile] Failed to load: ${err}`);
      this.profile = { ...EMPTY_PROFILE, environments: [] };
    }
  }

  async save(): Promise<void> {
    if (!this.dirty) return;
    try {
      const dir = dirname(this.filePath);
      if (!existsSync(dir)) await mkdir(dir, { recursive: true });
      this.profile.lastUpdated = Date.now();
      this.profile.metadata.totalServices = this.profile.environments
        .reduce((sum, env) => sum + env.services.length, 0);
      await writeFile(this.filePath, JSON.stringify(this.profile, null, 2), "utf-8");
      this.dirty = false;
      log.engine.info(`[InfraProfile] Saved ${this.profile.metadata.totalServices} services`);
    } catch (err) {
      log.engine.warn(`[InfraProfile] Failed to save: ${err}`);
    }
  }

  getProfile(): InfraProfile {
    return this.profile;
  }

  getEnvironment(name: string): InfraEnvironment | undefined {
    return this.profile.environments.find(e => e.name === name);
  }

  getOrCreateEnvironment(name: string): InfraEnvironment {
    let env = this.profile.environments.find(e => e.name === name);
    if (!env) {
      env = { name, services: [], connections: [] };
      this.profile.environments.push(env);
      this.dirty = true;
    }
    return env;
  }

  addService(envName: string, service: Omit<InfraService, "discoveredAt" | "lastMentioned">): InfraService {
    const env = this.getOrCreateEnvironment(envName);
    const existing = env.services.find(s => s.name === service.name);
    if (existing) {
      Object.assign(existing, service, { lastMentioned: Date.now() });
      this.dirty = true;
      return existing;
    }
    const full: InfraService = {
      ...service,
      discoveredAt: Date.now(),
      lastMentioned: Date.now(),
    };
    env.services.push(full);
    this.dirty = true;
    return full;
  }

  addConnection(envName: string, connection: InfraConnection): void {
    const env = this.getOrCreateEnvironment(envName);
    const exists = env.connections.some(
      c => c.from === connection.from && c.to === connection.to && c.type === connection.type
    );
    if (!exists) {
      env.connections.push(connection);
      this.dirty = true;
    }
  }

  findService(name: string): Array<{ env: string; service: InfraService }> {
    const results: Array<{ env: string; service: InfraService }> = [];
    for (const env of this.profile.environments) {
      for (const svc of env.services) {
        if (svc.name.toLowerCase().includes(name.toLowerCase())) {
          results.push({ env: env.name, service: svc });
        }
      }
    }
    return results;
  }

  findByType(type: InfraService["type"]): Array<{ env: string; service: InfraService }> {
    const results: Array<{ env: string; service: InfraService }> = [];
    for (const env of this.profile.environments) {
      for (const svc of env.services) {
        if (svc.type === type) {
          results.push({ env: env.name, service: svc });
        }
      }
    }
    return results;
  }

  setTechStack(stack: string[]): void {
    const unique = [...new Set([...this.profile.metadata.techStack, ...stack])];
    if (unique.length !== this.profile.metadata.techStack.length) {
      this.profile.metadata.techStack = unique;
      this.dirty = true;
    }
  }

  setPrimaryProvider(provider: string): void {
    if (this.profile.metadata.primaryProvider !== provider) {
      this.profile.metadata.primaryProvider = provider;
      this.dirty = true;
    }
  }

  /** Generate a compact summary for injection into system prompts */
  toContextString(): string {
    if (this.profile.metadata.totalServices === 0) return "";

    const lines: string[] = ["## User Infrastructure"];
    if (this.profile.metadata.primaryProvider) {
      lines.push(`Primary cloud: ${this.profile.metadata.primaryProvider}`);
    }
    if (this.profile.metadata.techStack.length > 0) {
      lines.push(`Tech stack: ${this.profile.metadata.techStack.join(", ")}`);
    }
    for (const env of this.profile.environments) {
      lines.push(`\n### ${env.name} (${env.services.length} services)`);
      for (const svc of env.services) {
        const parts = [`- **${svc.name}** (${svc.type})`];
        if (svc.provider) parts.push(`on ${svc.provider}`);
        if (svc.url) parts.push(`@ ${svc.url}`);
        lines.push(parts.join(" "));
      }
      if (env.connections.length > 0) {
        lines.push("Connections:");
        for (const conn of env.connections) {
          lines.push(`  ${conn.from} → ${conn.to} (${conn.type})`);
        }
      }
    }
    return lines.join("\n");
  }
}

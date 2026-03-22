/**
 * StackOwl — Connector Resolver
 *
 * Resolves connector presets into MCP server configurations
 * that can be passed to the existing MCP client infrastructure.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join, dirname } from "node:path";
import { existsSync } from "node:fs";
import type { ConnectorInstance, ConnectorConfig } from "./types.js";
import { getPreset } from "./presets.js";
import { log } from "../logger.js";

export class ConnectorResolver {
  private config: ConnectorConfig = { instances: [] };
  private filePath: string;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "connectors.json");
  }

  async load(): Promise<void> {
    try {
      if (existsSync(this.filePath)) {
        const raw = await readFile(this.filePath, "utf-8");
        this.config = JSON.parse(raw);
        log.engine.info(`[Connectors] Loaded ${this.config.instances.length} connector(s)`);
      }
    } catch (err) {
      log.engine.warn(`[Connectors] Failed to load: ${err}`);
    }
  }

  async save(): Promise<void> {
    try {
      const dir = dirname(this.filePath);
      if (!existsSync(dir)) await mkdir(dir, { recursive: true });
      await writeFile(this.filePath, JSON.stringify(this.config, null, 2), "utf-8");
    } catch (err) {
      log.engine.warn(`[Connectors] Failed to save: ${err}`);
    }
  }

  /**
   * Configure a connector from a preset with user-provided env vars.
   */
  async configure(presetId: string, env: Record<string, string>, name?: string): Promise<ConnectorInstance> {
    const preset = getPreset(presetId);
    if (!preset) throw new Error(`Unknown connector preset: ${presetId}`);

    // Validate required env vars
    const missing = preset.requiredEnv.filter(k => !env[k]);
    if (missing.length > 0) {
      throw new Error(`Missing required environment variables: ${missing.join(", ")}`);
    }

    const instance: ConnectorInstance = {
      presetId,
      name: name ?? preset.name,
      enabled: true,
      env,
      configuredAt: Date.now(),
    };

    // Replace existing or add new
    const idx = this.config.instances.findIndex(i => i.presetId === presetId);
    if (idx >= 0) {
      this.config.instances[idx] = instance;
    } else {
      this.config.instances.push(instance);
    }

    await this.save();
    log.engine.info(`[Connectors] Configured ${preset.name} (${presetId})`);
    return instance;
  }

  /**
   * Resolve all enabled connectors to MCP server configs.
   * Returns configs compatible with the existing mcp.servers config format.
   */
  resolveToMcpConfigs(): Array<{
    name: string;
    transport: "stdio" | "sse";
    command?: string;
    args?: string[];
    url?: string;
    env?: Record<string, string>;
  }> {
    const configs: Array<{
      name: string;
      transport: "stdio" | "sse";
      command?: string;
      args?: string[];
      url?: string;
      env?: Record<string, string>;
    }> = [];

    for (const instance of this.config.instances) {
      if (!instance.enabled) continue;
      const preset = getPreset(instance.presetId);
      if (!preset) continue;

      configs.push({
        name: instance.name,
        transport: preset.transport,
        command: preset.command,
        args: preset.args,
        url: preset.url,
        env: instance.env,
      });
    }

    return configs;
  }

  getInstances(): ConnectorInstance[] {
    return [...this.config.instances];
  }

  getEnabledInstances(): ConnectorInstance[] {
    return this.config.instances.filter(i => i.enabled);
  }

  async toggle(presetId: string, enabled: boolean): Promise<void> {
    const instance = this.config.instances.find(i => i.presetId === presetId);
    if (instance) {
      instance.enabled = enabled;
      await this.save();
    }
  }

  async remove(presetId: string): Promise<void> {
    this.config.instances = this.config.instances.filter(i => i.presetId !== presetId);
    await this.save();
  }

  updateHealth(presetId: string, healthy: boolean): void {
    const instance = this.config.instances.find(i => i.presetId === presetId);
    if (instance) {
      instance.lastHealthCheck = Date.now();
      instance.healthy = healthy;
    }
  }
}

/**
 * StackOwl — Configuration Loader
 *
 * Loads and validates stackowl.config.json.
 */

import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";

// ─── Config Types ────────────────────────────────────────────────

export interface StackOwlConfig {
  providers: Record<string, ProviderConfigEntry>;
  defaultProvider: string;
  defaultModel: string;
  workspace: string;
  gateway: {
    port: number;
    host: string;
    rateLimit?: {
      maxPerMinute: number;
      maxPerHour: number;
    };
  };
  parliament: {
    maxRounds: number;
    maxOwls: number;
  };
  heartbeat: {
    enabled: boolean;
    intervalMinutes: number;
  };
  owlDna: {
    enabled: boolean;
    evolutionBatchSize: number;
    decayRatePerWeek: number;
  };
  engine?: {
    /**
     * Maximum number of tool-calling iterations per ReAct loop.
     * Increase for complex multi-step tasks. Default: 15.
     * Was previously hardcoded at 10 — too low for real workflows.
     */
    maxToolIterations?: number;
    /** Max estimated tokens before context compression triggers. Default: 8000. */
    maxContextTokens?: number;
    /** Max chars per tool result before truncation. Default: 6000. */
    maxToolResultLength?: number;
    /** Number of recent messages to keep verbatim during compression. Default: 10. */
    contextKeepRecent?: number;
    /** Enable plan-then-execute mode for complex tasks. */
    planning?: {
      enabled: boolean;
    };
  };
  /**
   * Tool/skill synthesis configuration.
   * Controls which provider and model are used for generating new tools.
   * By default uses Anthropic Claude Sonnet 4.6 for highest quality synthesis.
   */
  synthesis?: {
    /** Provider name to use for synthesis (must be registered in providers). Default: 'anthropic' */
    provider: string;
    /** Model to use for synthesis. Default: 'claude-sonnet-4-5-20241022' */
    model: string;
  };
  /** MCP server connections */
  mcp?: {
    servers: Array<{
      name: string;
      transport: "stdio" | "sse";
      command?: string;
      args?: string[];
      url?: string;
      env?: Record<string, string>;
    }>;
  };
  /** Tool permission gating by category */
  tools?: {
    permissions?: Record<string, "allowed" | "prompt" | "denied">;
  };
  smartRouting?: {
    enabled: boolean;
    fallbackProvider?: string; // e.g. 'anthropic'
    fallbackModel?: string; // e.g. 'claude-3-5-sonnet-latest'
    availableModels: {
      name: string;
      description: string;
    }[];
  };
  skills?: {
    enabled: boolean;
    directories: string[];
    watch?: boolean;
    watchDebounceMs?: number;
  };
  sandboxing?: {
    enabled: boolean;
    debugOutput?: boolean;
  };
}

export interface ProviderConfigEntry {
  baseUrl?: string;
  apiKey?: string;
  defaultModel?: string;
  defaultEmbeddingModel?: string;
}

// ─── Defaults ────────────────────────────────────────────────────

const DEFAULT_CONFIG: StackOwlConfig = {
  providers: {
    ollama: {
      baseUrl: "http://127.0.0.1:11434",
      defaultModel: "llama3.2",
      defaultEmbeddingModel: "nomic-embed-text",
    },
  },
  defaultProvider: "ollama",
  defaultModel: "llama3.2",
  workspace: "./workspace",
  gateway: {
    port: 3077,
    host: "127.0.0.1",
  },
  parliament: {
    maxRounds: 3,
    maxOwls: 6,
  },
  heartbeat: {
    enabled: false,
    intervalMinutes: 30,
  },
  owlDna: {
    enabled: true,
    evolutionBatchSize: 5,
    decayRatePerWeek: 0.01,
  },
  smartRouting: {
    enabled: false,
    fallbackProvider: "anthropic",
    fallbackModel: "claude-3-5-sonnet-latest",
    availableModels: [],
  },
  skills: {
    enabled: false,
    directories: [],
    watch: false,
    watchDebounceMs: 250,
  },
  sandboxing: {
    enabled: true,
    debugOutput: false,
  },
  engine: {
    maxToolIterations: 15,
  },
  synthesis: {
    provider: "anthropic",
    model: "claude-sonnet-4-5-20241022",
  },
};

// ─── Loader ──────────────────────────────────────────────────────

export async function loadConfig(basePath: string): Promise<StackOwlConfig> {
  const configPath = join(basePath, "stackowl.config.json");

  if (!existsSync(configPath)) {
    console.log(
      "[Config] No config file found, creating default at",
      configPath,
    );
    await writeFile(
      configPath,
      JSON.stringify(DEFAULT_CONFIG, null, 2),
      "utf-8",
    );
    return { ...DEFAULT_CONFIG };
  }

  try {
    const raw = await readFile(configPath, "utf-8");
    const userConfig = JSON.parse(raw) as Partial<StackOwlConfig>;

    // Deep merge with defaults
    const config: StackOwlConfig = {
      ...DEFAULT_CONFIG,
      ...userConfig,
      providers: {
        ...DEFAULT_CONFIG.providers,
        ...userConfig.providers,
      },
      gateway: {
        ...DEFAULT_CONFIG.gateway,
        ...userConfig.gateway,
      },
      parliament: {
        ...DEFAULT_CONFIG.parliament,
        ...userConfig.parliament,
      },
      heartbeat: {
        ...DEFAULT_CONFIG.heartbeat,
        ...userConfig.heartbeat,
      },
      owlDna: {
        ...DEFAULT_CONFIG.owlDna,
        ...userConfig.owlDna,
      },
      smartRouting: {
        ...DEFAULT_CONFIG.smartRouting,
        ...(userConfig.smartRouting || {}),
      } as NonNullable<StackOwlConfig["smartRouting"]>,
      skills: {
        ...DEFAULT_CONFIG.skills!,
        ...(userConfig.skills || {}),
      } as NonNullable<StackOwlConfig["skills"]>,
      sandboxing: {
        ...DEFAULT_CONFIG.sandboxing!,
        ...(userConfig.sandboxing || {}),
      } as NonNullable<StackOwlConfig["sandboxing"]>,
      engine: {
        ...DEFAULT_CONFIG.engine,
        ...(userConfig.engine || {}),
      },
      synthesis: {
        ...DEFAULT_CONFIG.synthesis!,
        ...(userConfig.synthesis || {}),
      },
    };

    // Runtime validation
    const errors = validateConfig(config);
    if (errors.length > 0) {
      console.warn(`[Config] Validation warnings:\n  ${errors.join('\n  ')}`);
    }

    return config;
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    throw new Error(`[Config] Failed to load ${configPath}: ${msg}`);
  }
}

// ─── Runtime Validation ───────────────────────────────────────────

function validateConfig(config: StackOwlConfig): string[] {
  const errors: string[] = [];

  // Required fields
  if (!config.defaultProvider || typeof config.defaultProvider !== 'string') {
    errors.push('defaultProvider must be a non-empty string');
  }
  if (!config.defaultModel || typeof config.defaultModel !== 'string') {
    errors.push('defaultModel must be a non-empty string');
  }

  // Provider validation
  if (!config.providers || typeof config.providers !== 'object') {
    errors.push('providers must be an object');
  } else {
    if (config.defaultProvider && !config.providers[config.defaultProvider]) {
      errors.push(`defaultProvider "${config.defaultProvider}" not found in providers`);
    }
    for (const [name, entry] of Object.entries(config.providers)) {
      if (entry.baseUrl && typeof entry.baseUrl !== 'string') {
        errors.push(`providers.${name}.baseUrl must be a string`);
      }
      if (entry.baseUrl && !entry.baseUrl.startsWith('http')) {
        errors.push(`providers.${name}.baseUrl must start with http:// or https://`);
      }
    }
  }

  // Gateway
  if (config.gateway) {
    if (config.gateway.port && (config.gateway.port < 1 || config.gateway.port > 65535)) {
      errors.push(`gateway.port must be between 1 and 65535 (got ${config.gateway.port})`);
    }
    if (config.gateway.rateLimit) {
      if (config.gateway.rateLimit.maxPerMinute < 1) {
        errors.push('gateway.rateLimit.maxPerMinute must be >= 1');
      }
      if (config.gateway.rateLimit.maxPerHour < 1) {
        errors.push('gateway.rateLimit.maxPerHour must be >= 1');
      }
    }
  }

  // Parliament
  if (config.parliament) {
    if (config.parliament.maxRounds < 1 || config.parliament.maxRounds > 10) {
      errors.push(`parliament.maxRounds should be 1-10 (got ${config.parliament.maxRounds})`);
    }
    if (config.parliament.maxOwls < 1 || config.parliament.maxOwls > 20) {
      errors.push(`parliament.maxOwls should be 1-20 (got ${config.parliament.maxOwls})`);
    }
  }

  // Engine
  if (config.engine) {
    if (config.engine.maxToolIterations !== undefined && config.engine.maxToolIterations < 1) {
      errors.push('engine.maxToolIterations must be >= 1');
    }
    if (config.engine.maxToolIterations !== undefined && config.engine.maxToolIterations > 50) {
      errors.push(`engine.maxToolIterations=${config.engine.maxToolIterations} is very high — consider <= 30`);
    }
    if (config.engine.maxContextTokens !== undefined && config.engine.maxContextTokens < 1000) {
      errors.push('engine.maxContextTokens must be >= 1000');
    }
  }

  // Owl DNA
  if (config.owlDna) {
    if (config.owlDna.evolutionBatchSize < 1) {
      errors.push('owlDna.evolutionBatchSize must be >= 1');
    }
    if (config.owlDna.decayRatePerWeek < 0 || config.owlDna.decayRatePerWeek > 1) {
      errors.push('owlDna.decayRatePerWeek must be between 0 and 1');
    }
  }

  // Skills
  if (config.skills?.enabled && (!config.skills.directories || config.skills.directories.length === 0)) {
    errors.push('skills.enabled=true but no directories configured');
  }

  // Smart routing
  if (config.smartRouting?.enabled && (!config.smartRouting.availableModels || config.smartRouting.availableModels.length === 0)) {
    errors.push('smartRouting.enabled=true but no availableModels configured');
  }

  return errors;
}

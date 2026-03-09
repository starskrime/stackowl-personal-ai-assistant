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
    };

    return config;
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    throw new Error(`[Config] Failed to load ${configPath}: ${msg}`);
  }
}

/**
 * StackOwl — Configuration Loader
 *
 * Loads and validates stackowl.config.json.
 */

import { readFile, writeFile, rename } from "node:fs/promises";
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
    /**
     * Output verbosity mode.
     * - "normal" (default): users see only the final answer. No tool status, no thinking indicators.
     * - "debug": full visibility — tool start/finish, _Thinking..._ headers, iteration markers.
     */
    outputMode?: "normal" | "debug";
    /** @deprecated Use outputMode instead. Kept for backwards compatibility. */
    suppressThinkingMessages?: boolean;
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
    /** Enable per-turn intent routing for tool selection. Default: true */
    enableIntentRouting?: boolean;
    /** Max tools to pass to the model per turn when intent routing is enabled. Default: 8 */
    maxToolsRouting?: number;
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
  execution?: {
    hostMode: boolean;
    sandboxMode: boolean;
  };
  pellets?: {
    /**
     * Embedding model for semantic pellet search.
     * Requires Ollama running locally with the model pulled.
     * Default: "nomic-embed-text" (768-dim, fast, accurate)
     */
    embeddingModel?: string;
    dedup?: {
      enabled?: boolean;
      /** Cosine similarity threshold to trigger LLM check. Default: 0.65 */
      similarityThreshold?: number;
      /** Cosine similarity above which to auto-skip. Default: 0.85 */
      skipThreshold?: number;
      useLlm?: boolean;
      maxCandidates?: number;
    };
  };
  /** Storage backend configuration */
  storage?: {
    /** 'file' (default) or 'sqlite' */
    backend: "file" | "sqlite";
    /** Path to SQLite database (only for sqlite backend) */
    sqlitePath?: string;
  };
  /** Cost tracking and budget enforcement */
  costs?: {
    enabled: boolean;
    budget?: {
      maxDailyUsd?: number;
      maxMonthlyUsd?: number;
      maxPerRequestTokens?: number;
      warnAtPercent?: number;
    };
  };
  /** Task queue configuration */
  queue?: {
    /** Max parallel background tasks. Default: 3 */
    concurrency?: number;
    /** Max queued tasks before dropping. Default: 100 */
    maxQueueSize?: number;
  };
  /** Rate limiting configuration (extends gateway.rateLimit) */
  rateLimiting?: {
    /** Per-provider rate limits */
    perProvider?: Record<
      string,
      {
        maxPerMinute: number;
        maxPerHour?: number;
      }
    >;
  };
  /** Plugin system configuration */
  plugins?: {
    /** Directories to scan for plugins */
    directories: string[];
    /** Auto-discover new plugins on startup */
    autoDiscover: boolean;
  };
  /** Knowledge Council configuration */
  council?: {
    /** Days between automated council sessions. Default: 7 */
    intervalDays?: number;
    /** Enable automated weekly council sessions. Default: true */
    enabled?: boolean;
  };
  /** Cognitive Loop configuration — self-improvement engine */
  cognition?: {
    /** Interval between cognitive ticks in minutes. Default: 15 */
    tickIntervalMinutes?: number;
    /** Minimum idle minutes before background learning. Default: 5 */
    minIdleMinutes?: number;
    /** Maximum actions per day to prevent runaway costs. Default: 20 */
    maxActionsPerDay?: number;
    /** Enable/disable the loop. Default: true */
    enabled?: boolean;
  };
  /** Research behavior configuration */
  research?: {
    /** Auto-detect deep research from message content. Default: true */
    autoDeep?: boolean;
    /** Self-check interval (tool call count between self-assessments). Default: 5 */
    selfCheckInterval?: number;
    /** Max iterations for deep research tasks (soft cap). Default: 40 */
    maxIterations?: number;
    /** Enable diminishing returns detection. Default: true */
    enableDiminishingReturns?: boolean;
    /** String similarity threshold for diminishing returns (0-1). Default: 0.7 */
    similarityThreshold?: number;
    /** Switch to cloud provider after N consecutive failures. Default: 2 */
    cloudFallbackAfter?: number;
  };
  /** Persistent browser pool for anti-bot web fetching */
  browser?: {
    /** Number of warm browser instances. Default: 2. Each uses ~100-200MB RAM. */
    poolSize?: number;
    /** Visit benign sites on startup to build cookie/fingerprint baseline. Default: true */
    warmUp?: boolean;
    /** Apply stealth patches (webdriver, webgl, etc). Default: true */
    stealthMode?: boolean;
    /** Proxy URL (e.g. 'http://proxy:8080'). */
    proxy?: string;
    /** Run headless. Default: true */
    headless?: boolean;
    /** Enable the browser pool. Default: true */
    enabled?: boolean;
  };
  /** CamoFox anti-detection browser configuration */
  camofox?: {
    /** Enable the CamoFox tool and Tier 4 smart-fetch escalation. Default: false */
    enabled?: boolean;
    /** CamoFox server base URL. Default: "http://localhost:9377" */
    baseUrl?: string;
    /** Bearer token if CAMOFOX_API_KEY is set on the server. Default: null */
    apiKey?: string | null;
    /** Default userId for sessions. Default: "stackowl" */
    defaultUserId?: string;
    /** Request timeout in ms. Default: 30000 */
    defaultTimeout?: number;
  };
  /** Voice channel configuration (used by `stackowl voice` and /voice in Telegram) */
  voice?: {
    /** Whisper model for offline STT. Default: "base.en". */
    model?: string;
    /** macOS voice name passed to `say -v`. Default: "Samantha". */
    systemVoice?: string;
    /** TTS speaking rate in words per minute. Default: 200. */
    speakRate?: number;
    /** RMS energy level (0–32767) below which audio is silence. Default: 500. */
    silenceThreshold?: number;
    /** Milliseconds of continuous silence that trigger end-of-speech. Default: 1500. */
    silenceDurationMs?: number;
  };
  /** Telegram bot configuration */
  telegram?: {
    botToken: string;
    allowedUserIds?: number[];
  };
  /** Slack integration configuration */
  slack?: {
    /** Bot token (xoxb-...) — from Slack App → OAuth & Permissions */
    botToken: string;
    /** App-level token (xapp-...) — from Slack App → Basic Information → App-Level Tokens */
    appToken: string;
    /** Signing secret — from Slack App → Basic Information */
    signingSecret?: string;
    /** Restrict bot to specific channel IDs */
    allowedChannels?: string[];
    /** HTTP port for non-socket-mode. Default: 3078 */
    port?: number;
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
    outputMode: "normal",
    suppressThinkingMessages: true,
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
  tools: {
    enableIntentRouting: true,
    maxToolsRouting: 8,
  },
  sandboxing: {
    enabled: true,
    debugOutput: false,
  },
  execution: {
    hostMode: true,
    sandboxMode: true,
  },
  engine: {
    maxToolIterations: 15,
  },
  synthesis: {
    provider: "anthropic",
    model: "claude-sonnet-4-5-20241022",
  },
  research: {
    autoDeep: true,
    selfCheckInterval: 5,
    maxIterations: 40,
    enableDiminishingReturns: true,
    similarityThreshold: 0.7,
    cloudFallbackAfter: 2,
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
      tools: {
        ...DEFAULT_CONFIG.tools!,
        ...(userConfig.tools || {}),
      } as NonNullable<StackOwlConfig["tools"]>,
      sandboxing: {
        ...DEFAULT_CONFIG.sandboxing!,
        ...(userConfig.sandboxing || {}),
      } as NonNullable<StackOwlConfig["sandboxing"]>,
      execution: {
        ...DEFAULT_CONFIG.execution!,
        ...(userConfig.execution || {}),
      } as NonNullable<StackOwlConfig["execution"]>,
      engine: {
        ...DEFAULT_CONFIG.engine,
        ...(userConfig.engine || {}),
      },
      synthesis: {
        ...DEFAULT_CONFIG.synthesis!,
        ...(userConfig.synthesis || {}),
      },
      research: {
        ...DEFAULT_CONFIG.research!,
        ...(userConfig.research || {}),
      },
    };

    // Runtime validation
    const errors = validateConfig(config);
    if (errors.length > 0) {
      console.warn(`[Config] Validation warnings:\n  ${errors.join("\n  ")}`);
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
  if (!config.defaultProvider || typeof config.defaultProvider !== "string") {
    errors.push("defaultProvider must be a non-empty string");
  }
  if (!config.defaultModel || typeof config.defaultModel !== "string") {
    errors.push("defaultModel must be a non-empty string");
  }

  // Provider validation
  if (!config.providers || typeof config.providers !== "object") {
    errors.push("providers must be an object");
  } else {
    if (config.defaultProvider && !config.providers[config.defaultProvider]) {
      errors.push(
        `defaultProvider "${config.defaultProvider}" not found in providers`,
      );
    }
    for (const [name, entry] of Object.entries(config.providers)) {
      if (entry.baseUrl && typeof entry.baseUrl !== "string") {
        errors.push(`providers.${name}.baseUrl must be a string`);
      }
      if (entry.baseUrl && !entry.baseUrl.startsWith("http")) {
        errors.push(
          `providers.${name}.baseUrl must start with http:// or https://`,
        );
      }
    }
  }

  // Gateway
  if (config.gateway) {
    if (
      config.gateway.port &&
      (config.gateway.port < 1 || config.gateway.port > 65535)
    ) {
      errors.push(
        `gateway.port must be between 1 and 65535 (got ${config.gateway.port})`,
      );
    }
    if (config.gateway.rateLimit) {
      if (config.gateway.rateLimit.maxPerMinute < 1) {
        errors.push("gateway.rateLimit.maxPerMinute must be >= 1");
      }
      if (config.gateway.rateLimit.maxPerHour < 1) {
        errors.push("gateway.rateLimit.maxPerHour must be >= 1");
      }
    }
  }

  // Parliament
  if (config.parliament) {
    if (config.parliament.maxRounds < 1 || config.parliament.maxRounds > 10) {
      errors.push(
        `parliament.maxRounds should be 1-10 (got ${config.parliament.maxRounds})`,
      );
    }
    if (config.parliament.maxOwls < 1 || config.parliament.maxOwls > 20) {
      errors.push(
        `parliament.maxOwls should be 1-20 (got ${config.parliament.maxOwls})`,
      );
    }
  }

  // Engine
  if (config.engine) {
    if (
      config.engine.maxToolIterations !== undefined &&
      config.engine.maxToolIterations < 1
    ) {
      errors.push("engine.maxToolIterations must be >= 1");
    }
    if (
      config.engine.maxToolIterations !== undefined &&
      config.engine.maxToolIterations > 50
    ) {
      errors.push(
        `engine.maxToolIterations=${config.engine.maxToolIterations} is very high — consider <= 30`,
      );
    }
    if (
      config.engine.maxContextTokens !== undefined &&
      config.engine.maxContextTokens < 1000
    ) {
      errors.push("engine.maxContextTokens must be >= 1000");
    }
  }

  // Owl DNA
  if (config.owlDna) {
    if (config.owlDna.evolutionBatchSize < 1) {
      errors.push("owlDna.evolutionBatchSize must be >= 1");
    }
    if (
      config.owlDna.decayRatePerWeek < 0 ||
      config.owlDna.decayRatePerWeek > 1
    ) {
      errors.push("owlDna.decayRatePerWeek must be between 0 and 1");
    }
  }

  // Skills
  if (
    config.skills?.enabled &&
    (!config.skills.directories || config.skills.directories.length === 0)
  ) {
    errors.push("skills.enabled=true but no directories configured");
  }

  // Smart routing
  if (
    config.smartRouting?.enabled &&
    (!config.smartRouting.availableModels ||
      config.smartRouting.availableModels.length === 0)
  ) {
    errors.push("smartRouting.enabled=true but no availableModels configured");
  }

  return errors;
}

// ─── Saver ───────────────────────────────────────────────────────

/**
 * Compute the absolute path to stackowl.config.json for a given base path.
 * Exported so adapters can pass it to saveConfig() without re-deriving it.
 */
export function getConfigPath(basePath: string): string {
  return join(basePath, "stackowl.config.json");
}

/**
 * Atomically write the config to disk.
 *
 * Uses a write-to-temp → rename strategy so that a crash during the write
 * never corrupts the live config file. The rename is atomic on POSIX systems
 * (single directory), which covers the common macOS / Linux case.
 *
 * @param basePath  The directory containing stackowl.config.json (process.cwd()).
 * @param config    The full config object to persist.
 */
export async function saveConfig(
  basePath: string,
  config: StackOwlConfig,
): Promise<void> {
  const configPath = getConfigPath(basePath);
  const tmpPath = configPath + ".tmp";

  // Validate before writing — refuse to persist a broken config
  const errors = validateConfig(config);
  if (errors.length > 0) {
    throw new Error(
      `[saveConfig] Refusing to save invalid config:\n  ${errors.join("\n  ")}`,
    );
  }

  const json = JSON.stringify(config, null, 2);
  await writeFile(tmpPath, json, "utf-8");
  await rename(tmpPath, configPath);
}

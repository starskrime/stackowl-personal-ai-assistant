/**
 * StackOwl — Configuration Loader
 *
 * Loads and validates stackowl.config.json.
 */

import { readFile, writeFile, rename } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { IntelligenceConfig } from "../intelligence/router.js";
import type { SignalSource, ConsentMap } from "../ambient/types.js";

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
      /** When false, skip connecting at boot. Default: true */
      enabled?: boolean;
      /** Human-readable purpose for /mcp list output */
      description?: string;
      /** ISO timestamp set by addServer() */
      installedAt?: string;
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
  /** Tiered model routing for all platform components. */
  intelligence?: IntelligenceConfig;
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
  /** Ambient signal mesh (Perches) configuration */
  perches?: {
    /** Per-source consent overrides. Falls back to DEFAULT_CONSENT when absent. */
    consent?: ConsentMap;
    /** Maximum signals retained in pool. Default: 32 */
    maxSignals?: number;
    /** FileSystemCollector debounce window (ms). Default: 5000 */
    fileWatchDebounceMs?: number;
    /** If set, only these sources are registered as collectors. Default: all. */
    enabledSources?: SignalSource[];
    /** Override watched paths for FileSystemCollector. Default: workspace src/ or root. */
    watchPaths?: string[];
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
    /** Enable the CamoFox tool and Tier 4 smart-fetch escalation. Default: true */
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
  /** Web fetch / smart-fetch tier configuration */
  webFetch?: {
    obscura?: {
      /** Enable the Obscura tier (Tier 3 reserve). Default false — type-only safety valve until v1.0 + independent benchmarks. */
      enabled?: boolean;
    };
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
  /** Structured logging configuration */
  logging?: {
    /** Minimum log level to emit. Default: "info" */
    level?: "debug" | "info" | "warn" | "error";
    sinks?: {
      /** Write JSONL to logs/stackowl-YYYY-MM-DD.log. Default: true */
      file?: boolean;
      /** Keep an in-memory ring buffer (for TUI overlay + tests). Default: true */
      ringBuffer?: boolean;
      /** Color-coded stderr output when TUI is not mounted. Default: false */
      prettyConsole?: boolean;
    };
    /** PII fields to mask before writing. Default: ["tokens","emails"] */
    redact?: Array<"tokens" | "emails" | "paths">;
    /** Days to retain log files. Default: 7 */
    retentionDays?: number;
    /** Ring buffer capacity (records). Default: 5000 */
    ringBufferSize?: number;
  };
  /** Distributed tracing configuration */
  tracing?: {
    /** Enable W3C trace-context propagation. Default: true */
    enabled?: boolean;
    /** Fraction of debug-level records to emit (0-1). info+ always emitted. Default: 1 */
    sampleRate?: number;
    /** Cross-boundary propagation targets. Default: ["queue","mcp"] */
    propagatePast?: Array<"queue" | "http" | "mcp">;
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
  /** Role → provider name mappings. Auto-assigned from type when not set. */
  roles?: Partial<Record<import("../providers/registry.js").ProviderRole, string>>;
}

export interface ProviderConfigEntry {
  /** Model file to use for protocol lookup (defaults to the provider key) */
  profile?: string;
  baseUrl?: string;
  apiKey?: string;
  /** Active model — the model used for this provider. Replaces defaultModel. */
  activeModel?: string;
  /** @deprecated Use activeModel instead */
  defaultModel?: string;
  defaultEmbeddingModel?: string;
  /** Provider protocol type (e.g. "anthropic", "openai") — used by autoAssignRoles. */
  type?: string;
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
    decayRatePerWeek: 0.1,
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
  logging: {
    level: "info",
    sinks: { file: true, ringBuffer: true, prettyConsole: false },
    redact: ["tokens", "emails"],
    retentionDays: 7,
    ringBufferSize: 5000,
  },
  tracing: {
    enabled: true,
    sampleRate: 1,
    propagatePast: ["queue", "mcp"],
  },
};

// ─── Default Intelligence Config ─────────────────────────────────

/**
 * Build a pass-through IntelligenceConfig from bare provider/model defaults.
 * Used when the user config omits the `intelligence` block entirely.
 * Every task type resolves to the same provider and model — identical to
 * the pre-IntelligenceRouter default behavior.
 */
export function buildDefaultIntelligenceConfig(
  defaultProvider: string,
  defaultModel: string,
): IntelligenceConfig {
  const tier = { provider: defaultProvider, model: defaultModel };
  return {
    tiers: { high: tier, mid: tier, low: tier },
    defaults: {
      conversation:   "mid",
      parliament:     "high",
      evolution:      "mid",
      extraction:     "low",
      episodic:       "low",
      classification: "low",
      synthesis:      "high",
      summarization:  "low",
      clarification:  "mid",
    },
  };
}

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
      intelligence: userConfig.intelligence,
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
      webFetch: {
        ...(userConfig.webFetch || {}),
        obscura: {
          enabled: userConfig.webFetch?.obscura?.enabled === true,
        },
      },
      logging: {
        ...DEFAULT_CONFIG.logging!,
        ...(userConfig.logging || {}),
        sinks: {
          ...DEFAULT_CONFIG.logging!.sinks,
          ...(userConfig.logging?.sinks || {}),
        },
      },
      tracing: {
        ...DEFAULT_CONFIG.tracing!,
        ...(userConfig.tracing || {}),
      },
    };

    // Intelligence block validation
    if (config.intelligence) {
      const tiers = config.intelligence.tiers;
      if (!tiers?.mid?.provider || !tiers?.mid?.model) {
        throw new Error(
          "[Config] intelligence.tiers.mid is required (used as fallback for unspecified task types).",
        );
      }
      for (const [name, tier] of Object.entries(tiers ?? {})) {
        if (!tier.provider || !tier.model) {
          throw new Error(
            `[Config] intelligence.tiers.${name} is missing provider or model.`,
          );
        }
      }
    }

    // Derive defaultModel from active provider's activeModel (new config format)
    // This keeps all existing consumers of config.defaultModel working.
    const activeProviderEntry = config.providers[config.defaultProvider];
    if (activeProviderEntry) {
      config.defaultModel =
        activeProviderEntry.activeModel ??
        activeProviderEntry.defaultModel ??
        config.defaultModel;
    }

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
  // defaultModel is derived from active provider's activeModel; no hard validation needed

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

  // Logging
  if (config.logging) {
    const { level, retentionDays, ringBufferSize, sampleRate } = {
      ...config.logging,
      sampleRate: config.tracing?.sampleRate,
    };
    const validLevels = ["debug", "info", "warn", "error"];
    if (level && !validLevels.includes(level)) {
      errors.push(`logging.level must be one of ${validLevels.join(", ")} (got "${level}")`);
    }
    if (retentionDays !== undefined && (retentionDays < 1 || retentionDays > 90)) {
      errors.push(`logging.retentionDays must be 1-90 (got ${retentionDays})`);
    }
    if (ringBufferSize !== undefined && ringBufferSize < 100) {
      errors.push(`logging.ringBufferSize must be >= 100 (got ${ringBufferSize})`);
    }
    if (sampleRate !== undefined && (sampleRate < 0 || sampleRate > 1)) {
      errors.push(`tracing.sampleRate must be 0-1 (got ${sampleRate})`);
    }
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

let consentMutex: Promise<void> = Promise.resolve();

/**
 * Atomically grant or revoke consent for an ambient signal source.
 * Serialized per-process via a mutex chain so concurrent calls don't race.
 */
export async function mutateConsent(
  basePath: string,
  source: SignalSource,
  granted: boolean,
): Promise<void> {
  const next = consentMutex.then(async () => {
    const config = await loadConfig(basePath);
    const perches = (config.perches ??= {});
    const consent = (perches.consent ??= {});
    consent[source] = granted;
    await saveConfig(basePath, config);
  });
  consentMutex = next.catch(() => undefined);
  return next;
}

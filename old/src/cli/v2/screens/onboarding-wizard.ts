/**
 * onboarding-wizard.ts — @clack/prompts wizard for first-run setup.
 *
 * This is a plain TypeScript module (no Ink/JSX). It MUST run BEFORE Ink
 * mounts because @clack/prompts takes over stdin in raw mode — Ink does the
 * same, and they cannot coexist simultaneously.
 *
 * Exports:
 *   needsOnboarding()    — true if stackowl.config.json is missing or has no
 *                          real provider configured.
 *   runOnboardingWizard() — runs the @clack/prompts wizard, writes config, exits
 *                           on cancellation.
 */

import { existsSync, readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import {
  intro,
  outro,
  select,
  text,
  password,
  confirm,
  spinner,
  isCancel,
  cancel,
  note,
} from "@clack/prompts";
import type { StackOwlConfig } from "../../../config/loader.js";

// ─── Helpers ─────────────────────────────────────────────────────────────────

const CONFIG_FILENAME = "stackowl.config.json";

function configPath(baseDir?: string): string {
  return join(baseDir ?? homedir(), ".stackowl", CONFIG_FILENAME);
}

/**
 * Returns true when the wizard should run: config file doesn't exist, or the
 * default provider has no real model/key configured (i.e. it's only the
 * auto-generated Ollama stub that loadConfig() writes on first boot).
 *
 * @param baseDir  Directory that contains the `.stackowl/` folder.
 *                 Defaults to `homedir()` (i.e. `~/.stackowl/`).
 */
export function needsOnboarding(baseDir?: string): boolean {
  const p = configPath(baseDir);
  if (!existsSync(p)) return true;

  // Config exists — check if it looks like the auto-generated stub
  try {
    // Intentional sync read here; this is a cold-start check before any I/O loop.
    const raw = readFileSync(p, "utf-8");
    const cfg = JSON.parse(raw) as Partial<StackOwlConfig>;

    // If the only provider is ollama AND it has no apiKey, it might just be
    // the default stub. But we respect the user's choice — if they deliberately
    // set up Ollama, we don't force onboarding.  The only case we force it is
    // when there are NO providers at all.
    const providers = cfg.providers ?? {};
    return Object.keys(providers).length === 0;
  } catch {
    // Corrupt config — run onboarding to produce a clean one.
    return true;
  }
}

// ─── Provider definitions ─────────────────────────────────────────────────────

interface ProviderDef {
  value: string;
  label: string;
  hint: string;
  needsApiKey: boolean;
  needsBaseUrl: boolean;
  defaultBaseUrl?: string;
  defaultModel: string;
}

const PROVIDERS: ProviderDef[] = [
  {
    value: "anthropic",
    label: "Anthropic (Claude)",
    hint: "claude-opus-4-5, claude-sonnet-4-5, claude-haiku-3-5 — best reasoning",
    needsApiKey: true,
    needsBaseUrl: false,
    defaultModel: "claude-sonnet-4-5-20241022",
  },
  {
    value: "openai",
    label: "OpenAI",
    hint: "gpt-4o, gpt-4o-mini, o1, o3 — wide model selection",
    needsApiKey: true,
    needsBaseUrl: false,
    defaultModel: "gpt-4o",
  },
  {
    value: "ollama",
    label: "Ollama (local)",
    hint: "llama3.2, mistral, qwen2.5 — runs on your machine, no API key needed",
    needsApiKey: false,
    needsBaseUrl: true,
    defaultBaseUrl: "http://127.0.0.1:11434",
    defaultModel: "llama3.2",
  },
  {
    value: "openai-compatible",
    label: "Other (OpenAI-compatible)",
    hint: "LM Studio, Together AI, Groq, etc.",
    needsApiKey: true,
    needsBaseUrl: true,
    defaultBaseUrl: "http://127.0.0.1:1234",
    defaultModel: "local-model",
  },
];

// ─── Config builder ───────────────────────────────────────────────────────────

interface WizardResult {
  provider: string;
  providerDef: ProviderDef;
  apiKey: string;
  baseUrl: string;
  model: string;
  enableTelegram: boolean;
  telegramToken: string;
}

function buildConfig(answers: WizardResult): StackOwlConfig {
  const providerEntry: StackOwlConfig["providers"][string] = {
    activeModel: answers.model,
  };

  if (answers.apiKey) {
    providerEntry.apiKey = answers.apiKey;
  }

  if (answers.baseUrl) {
    providerEntry.baseUrl = answers.baseUrl;
  }

  // For ollama, add embedding model default
  if (answers.provider === "ollama") {
    providerEntry.defaultEmbeddingModel = "nomic-embed-text";
  }

  // For OpenAI-compatible, set profile to openai so the protocol adapter is used
  if (answers.provider === "openai-compatible") {
    providerEntry.profile = "openai";
  }

  const config: StackOwlConfig = {
    providers: {
      [answers.provider]: providerEntry,
    },
    defaultProvider: answers.provider,
    defaultModel: answers.model,
    workspace: "./workspace",
    gateway: {
      port: 3077,
      host: "127.0.0.1",
      outputMode: "normal",
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
      provider: answers.provider,
      model: answers.model,
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

  if (answers.enableTelegram && answers.telegramToken) {
    config.telegram = {
      botToken: answers.telegramToken,
    };
  }

  return config;
}

// ─── Wizard ───────────────────────────────────────────────────────────────────

/**
 * Run the full @clack/prompts onboarding wizard.
 *
 * Writes `~/.stackowl/stackowl.config.json` on success. Exits the process on
 * cancellation (Ctrl+C or explicit cancel) — the caller can rely on the config
 * existing after this returns without error.
 *
 * @param baseDir  Directory that contains the `.stackowl/` folder.
 *                 Defaults to `homedir()` (i.e. `~/.stackowl/`).
 */
export async function runOnboardingWizard(baseDir?: string): Promise<void> {
  const destPath = configPath(baseDir);

  intro(" StackOwl — First-run setup ");

  note(
    "This wizard will configure your AI provider and preferences.\n" +
    `Config is saved to ${destPath}\n` +
    "You can re-run this wizard any time with: stackowl config --wizard",
    "Welcome",
  );

  // ── Step 1: Provider ────────────────────────────────────────────────────────

  const providerValue = await select({
    message: "Which AI provider do you want to use?",
    options: PROVIDERS.map((p) => ({
      value: p.value,
      label: p.label,
      hint: p.hint,
    })),
  });

  if (isCancel(providerValue)) {
    cancel("Setup cancelled. Run `stackowl config --wizard` to set up later.");
    process.exit(0);
  }

  const providerDef = PROVIDERS.find((p) => p.value === providerValue)!;

  // ── Step 2: API key (if required) ──────────────────────────────────────────

  let apiKey = "";
  if (providerDef.needsApiKey) {
    const keyResult = await password({
      message: `Enter your ${providerDef.label} API key`,
      validate: (val) => {
        if (!val || val.trim().length === 0) return "API key is required";
        if (val.trim().length < 10) return "API key seems too short";
        return undefined;
      },
    });

    if (isCancel(keyResult)) {
      cancel("Setup cancelled.");
      process.exit(0);
    }

    apiKey = String(keyResult).trim();
  }

  // ── Step 2b: Base URL (if required) ────────────────────────────────────────

  let baseUrl = "";
  if (providerDef.needsBaseUrl) {
    const urlResult = await text({
      message: `Base URL for ${providerDef.label}`,
      placeholder: providerDef.defaultBaseUrl ?? "http://localhost:11434",
      defaultValue: providerDef.defaultBaseUrl,
      validate: (val) => {
        const trimmed = val?.trim() ?? "";
        if (!trimmed) return "Base URL is required";
        if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
          return "URL must start with http:// or https://";
        }
        return undefined;
      },
    });

    if (isCancel(urlResult)) {
      cancel("Setup cancelled.");
      process.exit(0);
    }

    baseUrl = String(urlResult).trim();
  }

  // ── Step 2c: Model ─────────────────────────────────────────────────────────

  let model = providerDef.defaultModel;

  if (providerDef.needsBaseUrl) {
    // For local providers, try to detect available models
    if (baseUrl) {
      const s = spinner();
      s.start("Checking for available models…");

      let detectedModels: string[] = [];
      try {
        // Ollama-style: /api/tags
        const endpoint = providerDef.value === "ollama"
          ? `${baseUrl}/api/tags`
          : `${baseUrl}/v1/models`;
        const resp = await fetch(endpoint, { signal: AbortSignal.timeout(2500) });
        if (resp.ok) {
          const data = await resp.json() as Record<string, unknown>;
          if (Array.isArray(data["models"])) {
            detectedModels = (data["models"] as Array<{ name: string }>)
              .map((m) => m.name)
              .filter(Boolean);
          } else if (Array.isArray(data["data"])) {
            detectedModels = (data["data"] as Array<{ id: string }>)
              .map((m) => m.id)
              .filter(Boolean);
          }
        }
      } catch {
        // Not available — user will type model name
      }

      if (detectedModels.length > 0) {
        s.stop(`Found ${detectedModels.length} model(s)`);

        const modelChoice = await select({
          message: "Select a model",
          options: detectedModels.map((m) => ({ value: m, label: m })),
        });

        if (isCancel(modelChoice)) {
          cancel("Setup cancelled.");
          process.exit(0);
        }

        model = String(modelChoice);
      } else {
        s.stop("Could not detect models — enter one manually");

        const modelText = await text({
          message: "Model name",
          placeholder: providerDef.defaultModel,
          defaultValue: providerDef.defaultModel,
        });

        if (isCancel(modelText)) {
          cancel("Setup cancelled.");
          process.exit(0);
        }

        model = String(modelText).trim() || providerDef.defaultModel;
      }
    }
  } else {
    // Cloud provider — let user type or accept default
    const modelText = await text({
      message: "Default model",
      placeholder: providerDef.defaultModel,
      defaultValue: providerDef.defaultModel,
    });

    if (isCancel(modelText)) {
      cancel("Setup cancelled.");
      process.exit(0);
    }

    model = String(modelText).trim() || providerDef.defaultModel;
  }

  // ── Step 3: Telegram (optional) ────────────────────────────────────────────

  const wantTelegram = await confirm({
    message: "Enable Telegram integration? (optional — you can set this up later)",
    initialValue: false,
  });

  if (isCancel(wantTelegram)) {
    cancel("Setup cancelled.");
    process.exit(0);
  }

  let telegramToken = "";
  if (wantTelegram) {
    const tokenResult = await text({
      message: "Telegram bot token",
      placeholder: "1234567890:ABC...",
      validate: (val) => {
        if (!val || val.trim().length === 0) return "Bot token is required";
        if (!val.includes(":")) return "Token format should be <id>:<secret>";
        return undefined;
      },
    });

    if (isCancel(tokenResult)) {
      cancel("Setup cancelled.");
      process.exit(0);
    }

    telegramToken = String(tokenResult).trim();
  }

  // ── Write config ───────────────────────────────────────────────────────────

  const answers: WizardResult = {
    provider: String(providerValue),
    providerDef,
    apiKey,
    baseUrl,
    model,
    enableTelegram: Boolean(wantTelegram),
    telegramToken,
  };

  const config = buildConfig(answers);
  const s = spinner();
  s.start("Writing configuration…");

  try {
    await mkdir(join(baseDir ?? homedir(), ".stackowl"), { recursive: true });
    await writeFile(
      destPath,
      JSON.stringify(config, null, 2),
      "utf-8",
    );
    s.stop("Configuration saved");
  } catch (err) {
    s.stop("Failed to write configuration");
    throw err;
  }

  outro(
    `Setup complete! Your config is at ${destPath}\n` +
    "Run `stackowl` to start chatting.",
  );
}

/**
 * StackOwl — Main Entry Point
 *
 * Initializes the StackOwl system and starts the CLI interface.
 */

import { resolve } from "node:path";
import { program } from "commander";
import chalk from "chalk";
// log imported by adapters/gateway internally
import { loadConfig } from "./config/loader.js";
import { ProviderRegistry } from "./providers/registry.js";
import { OwlRegistry } from "./owls/registry.js";
import { ToolRegistry } from "./tools/registry.js";
import { ShellTool } from "./tools/shell.js";
import { ReadFileTool, WriteFileTool, EditFileTool } from "./tools/files.js";
import { SendFileTool } from "./tools/send_file.js";
import { GoogleSearchTool } from "./tools/search.js";
import { WebCrawlTool } from "./tools/web.js";
import { MemoryConsolidator } from "./memory/consolidator.js";
import { SessionStore } from "./memory/store.js";
import { OrchestrateTasksTool } from "./tools/orchestrate.js";
import { SummonParliamentTool } from "./tools/parliament.js";
import { PatchTool } from "./tools/toolsmith.js";
import { ParliamentOrchestrator } from "./parliament/orchestrator.js";
import { PelletStore } from "./pellets/store.js";
import { OwlEvolutionEngine } from "./owls/evolution.js";
import { LearningEngine } from "./learning/self-study.js";
import { ToolSynthesizer } from "./evolution/synthesizer.js";
import { CapabilityLedger } from "./evolution/ledger.js";
import { DynamicToolLoader } from "./evolution/loader.js";
import { EvolutionHandler } from "./evolution/handler.js";
import { InstinctRegistry } from "./instincts/registry.js";
import { InstinctEngine } from "./instincts/engine.js";
import { PerchManager } from "./perch/manager.js";
import { FilePerch } from "./perch/file-perch.js";
import { StackOwlServer } from "./server/index.js";
import { OwlGateway } from "./gateway/core.js";
import { TelegramAdapter } from "./gateway/adapters/telegram.js";
import { CLIAdapter } from "./gateway/adapters/cli.js";
import { PreferenceStore } from "./preferences/store.js";
import { ReflexionEngine } from "./evolution/reflexion.js";
import { SkillsLoader } from "./skills/index.js";
import { MCPManager } from "./tools/mcp/manager.js";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";

// ─── Bootstrap StackOwl ──────────────────────────────────────────

async function bootstrap() {
  const basePath = process.cwd();
  const config = await loadConfig(basePath);
  const workspacePath = resolve(basePath, config.workspace);

  // Initialize provider registry
  const providerRegistry = new ProviderRegistry();
  for (const [name, providerConf] of Object.entries(config.providers)) {
    providerRegistry.register({
      name,
      ...providerConf,
    });
  }
  providerRegistry.setDefault(config.defaultProvider);

  // Initialize owl registry
  const owlRegistry = new OwlRegistry(workspacePath);
  await owlRegistry.loadAll();

  // Initialize tools
  const toolRegistry = new ToolRegistry();
  const { WebSearchTool } = await import("./compat/index.js");
  const { SessionsListTool, SessionsHistoryTool, SessionStatusTool } =
    await import("./compat/tools/sessions.js");
  const { MemorySearchTool, MemoryGetTool } =
    await import("./compat/tools/memory.js");
  const { CronTool } = await import("./compat/tools/cron.js");
  toolRegistry.registerAll([
    ShellTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    OrchestrateTasksTool,
    SendFileTool,
    GoogleSearchTool,
    WebCrawlTool,
    new SummonParliamentTool(),
    PatchTool,
    // OpenCLAW-compatible tools - BrowserTool disabled (requires Chrome)
    // Use google_search and web_crawl instead for web access
    // new BrowserTool(workspacePath),
    new WebSearchTool(
      "brave",
      process.env.BRAVE_API_KEY || process.env.WEB_SEARCH_API_KEY,
    ),
    // Session tools
    new SessionsListTool(workspacePath),
    new SessionsHistoryTool(workspacePath),
    new SessionStatusTool(),
    // Memory tools
    new MemorySearchTool(workspacePath),
    new MemoryGetTool(workspacePath),
    // Cron tool
    new CronTool(workspacePath),
  ]);

  // Initialize session store
  const sessionStore = new SessionStore(workspacePath);
  await sessionStore.init();

  // Initialize pellet store
  const pelletStore = new PelletStore(
    workspacePath,
    providerRegistry.getDefault(),
  );
  await pelletStore.init();

  // Learning Engine — instantiated here so bootstrap can share it across CLI + Telegram
  // (actual owl binding happens after owl selection, so we expose a factory)
  const learningEngineFactory = (
    owl: import("./owls/persona.js").OwlInstance,
  ) =>
    new LearningEngine(
      providerRegistry.getDefault(),
      owl,
      config,
      pelletStore,
      workspacePath,
    );

  // Evolution Engine (DNA)
  const evolutionEngine = new OwlEvolutionEngine(
    providerRegistry.getDefault(),
    config,
    sessionStore,
    owlRegistry,
  );

  // Apply DNA decay for all owls if overdue (runs at most once per week per owl)
  for (const o of owlRegistry.listOwls()) {
    await evolutionEngine.applyDecayIfNeeded(o.persona.name).catch(() => {});
  }

  // Self-improvement system
  const synthesizer = new ToolSynthesizer();
  const ledger = new CapabilityLedger();
  const loader = new DynamicToolLoader(ledger);
  const evolution = new EvolutionHandler(synthesizer, ledger, loader);

  // Load any previously synthesized tools into the registry
  const synthesizedCount = await loader.loadAll(toolRegistry);
  if (synthesizedCount > 0) {
    console.log(
      chalk.dim(
        `  [Loaded ${synthesizedCount} synthesized tool(s) from previous sessions]`,
      ),
    );
  }

  // Instincts
  const instinctRegistry = new InstinctRegistry(workspacePath);
  await instinctRegistry.loadAll();
  const instinctEngine = new InstinctEngine();

  // Skills (OpenCLAW-compatible)
  const skillsLoader = new SkillsLoader();
  if (config.skills?.enabled && config.skills.directories?.length > 0) {
    const skillsDirs = config.skills.directories.map((d) =>
      resolve(basePath, d),
    );
    const skillsCount = await skillsLoader.load({
      directories: skillsDirs,
      watch: config.skills.watch ?? false,
      watchDebounceMs: config.skills.watchDebounceMs ?? 250,
    });
    console.log(chalk.dim(`  [Loaded ${skillsCount} skills]`));
  }

  // Load tool permissions from config
  if (config.tools?.permissions) {
    toolRegistry.loadPermissions(config.tools.permissions as any);
  }

  // MCP server connections
  const mcpManager = new MCPManager();
  if (config.mcp?.servers?.length) {
    const mcpCount = await mcpManager.connectAll(config.mcp.servers, toolRegistry);
    if (mcpCount > 0) {
      console.log(chalk.dim(`  [MCP: ${mcpCount} tool(s) from ${config.mcp.servers.length} server(s)]`));
    }
  }

  // User Preference Store
  const preferenceStore = new PreferenceStore(workspacePath);
  await preferenceStore.load();

  // Reflexion Engine
  const reflexionEngine = new ReflexionEngine(
    providerRegistry.getDefault(),
    sessionStore,
    pelletStore,
  );

  // Perch Points
  const perchManager = new PerchManager(
    providerRegistry.getDefault(),
    config,
    owlRegistry,
  );
  perchManager.addPerch(new FilePerch(workspacePath));

  return {
    config,
    providerRegistry,
    owlRegistry,
    toolRegistry,
    sessionStore,
    pelletStore,
    evolutionEngine,
    instinctRegistry,
    instinctEngine,
    perchManager,
    workspacePath,
    evolution,
    synthesizer,
    ledger,
    loader,
    learningEngineFactory,
    preferenceStore,
    reflexionEngine,
    skillsLoader,
  };
}

// ─── Gateway Builder ─────────────────────────────────────────────

async function buildGateway(
  b: Awaited<ReturnType<typeof bootstrap>>,
  owl: NonNullable<
    ReturnType<Awaited<ReturnType<typeof bootstrap>>["owlRegistry"]["get"]>
  >,
): Promise<OwlGateway> {
  const provider = b.providerRegistry.getDefault();
  const memoryContext = await MemoryConsolidator.loadMemory(b.workspacePath);
  if (memoryContext) {
    console.log(chalk.dim("  [Memory loaded from previous sessions]"));
  }

  const gateway = new OwlGateway({
    provider,
    owl,
    owlRegistry: b.owlRegistry,
    config: b.config,
    toolRegistry: b.toolRegistry,
    sessionStore: b.sessionStore,
    pelletStore: b.pelletStore,
    capabilityLedger: b.ledger,
    evolution: b.evolution,
    evolutionEngine: b.evolutionEngine,
    learningEngine: b.learningEngineFactory(owl),
    instinctRegistry: b.instinctRegistry,
    instinctEngine: b.instinctEngine,
    preferenceStore: b.preferenceStore,
    reflexionEngine: b.reflexionEngine,
    skillsLoader: b.skillsLoader,
    memoryContext,
    cwd: b.workspacePath,
    providerRegistry: b.providerRegistry,
  });

  return gateway;
}

// ─── Chat Command ────────────────────────────────────────────────

async function chatCommand(owlName?: string) {
  const b = await bootstrap();

  const owl = owlName ? b.owlRegistry.get(owlName) : b.owlRegistry.getDefault();
  if (!owl) {
    console.error(chalk.red(`❌ Owl "${owlName}" not found.`));
    for (const o of b.owlRegistry.listOwls()) {
      console.log(chalk.dim(`  ${o.persona.emoji} ${o.persona.name}`));
    }
    process.exit(1);
  }

  const provider = b.providerRegistry.getDefault();
  if (!(await provider.healthCheck())) {
    console.error(
      chalk.red(`❌ Cannot reach ${provider.name}. Is it running?`),
    );
    process.exit(1);
  }

  console.log(
    chalk.green(`✓ Connected to ${provider.name}`) +
      chalk.dim(` (model: ${b.config.defaultModel})`),
  );

  const gateway = await buildGateway(b, owl);
  const adapter = new CLIAdapter(gateway);
  gateway.register(adapter);

  await b.perchManager.startAll();

  process.on("SIGINT", async () => {
    b.perchManager.stopAll();
    adapter.stop();
    process.exit(0);
  });

  await adapter.start();
}

// ─── Parliament Command ──────────────────────────────────────────

async function parliamentCommand(topic?: string) {
  if (!topic || topic.trim() === "") {
    console.error(
      chalk.red("❌ Please provide a topic for the Parliament to debate."),
    );
    console.log(
      chalk.dim(
        'Example: stackowl parliament "Should we migrate from PostgreSQL to DynamoDB?"',
      ),
    );
    process.exit(1);
  }

  const { providerRegistry, owlRegistry, config, pelletStore } =
    await bootstrap();
  const provider = providerRegistry.getDefault();

  // Pick 3-4 owls for the debate (default to Noctua, Archimedes, Scrooge, and Socrates if available)
  const participants = [
    owlRegistry.get("Noctua"),
    owlRegistry.get("Archimedes"),
    owlRegistry.get("Scrooge"),
    owlRegistry.get("Socrates"),
  ].filter(Boolean) as any[];

  if (participants.length < 2) {
    // Fallback to whatever owls we have
    const allOwls = owlRegistry.listOwls();
    if (allOwls.length < 2) {
      console.error(
        chalk.red(
          "❌ Parliament requires at least 2 owls. Create more OWL.md files.",
        ),
      );
      process.exit(1);
    }
    participants.length = 0;
    participants.push(...allOwls.slice(0, 4));
  }

  console.log(chalk.cyan(`\nSummoning Parliament...\n`));

  const orchestrator = new ParliamentOrchestrator(
    provider,
    config,
    pelletStore,
  );

  try {
    const session = await orchestrator.convene({
      topic,
      participants,
      contextMessages: [],
    });

    console.log("\n\n" + chalk.bold.green("=== FINAL REPORT ===\n"));
    console.log(orchestrator.formatSessionMarkdown(session));
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.error(chalk.red(`\nParliament session failed: ${msg}`));
  }
}

// ─── Owls Command ────────────────────────────────────────────────

async function owlsCommand() {
  const { owlRegistry } = await bootstrap();
  const owls = owlRegistry.listOwls();

  console.log(chalk.bold("\n🦉 StackOwl — Registered Owls\n"));

  if (owls.length === 0) {
    console.log(
      chalk.dim("  No owls found. Check your workspace/owls/ directory."),
    );
    return;
  }

  for (const owl of owls) {
    const p = owl.persona;
    const d = owl.dna;
    console.log(`  ${p.emoji} ${chalk.bold(p.name)} — ${p.type}`);
    console.log(
      chalk.dim(
        `     Challenge: ${d.evolvedTraits.challengeLevel} | Gen: ${d.generation} | Convos: ${d.interactionStats.totalConversations}`,
      ),
    );
    console.log(chalk.dim(`     Specialties: ${p.specialties.join(", ")}`));
    console.log("");
  }
}

// ─── Status Command ──────────────────────────────────────────────

async function statusCommand() {
  const { config, providerRegistry } = await bootstrap();

  console.log(chalk.bold("\n🦉 StackOwl — System Status\n"));

  const healthResults = await providerRegistry.healthCheckAll();
  for (const [name, healthy] of Object.entries(healthResults)) {
    const icon = healthy ? chalk.green("✓") : chalk.red("✗");
    const label = name === config.defaultProvider ? `${name} (default)` : name;
    console.log(`  ${icon} ${label}`);
  }

  console.log(`\n  Default model: ${config.defaultModel}`);
  console.log(`  Gateway: ws://${config.gateway.host}:${config.gateway.port}`);
  console.log(`  Workspace: ${config.workspace}`);
  console.log("");
}

// ─── Pellets Command ───────────────────────────────────────────────

async function pelletsCommand(opts: { search?: string; read?: string }) {
  const { pelletStore } = await bootstrap();

  if (opts.read) {
    // Read a specific pellet
    const pellet = await pelletStore.get(opts.read);
    if (!pellet) {
      console.error(chalk.red(`❌ Pellet "${opts.read}" not found.`));
      process.exit(1);
    }

    console.log(chalk.bold.cyan(`📦 PELLET: ${pellet.title}`));
    console.log(
      chalk.dim(`Generated: ${new Date(pellet.generatedAt).toLocaleString()}`),
    );
    console.log(chalk.dim(`Source: ${pellet.source}`));
    console.log(chalk.dim(`Tags: ${pellet.tags.join(", ")}`));
    console.log(chalk.dim(`Owls: ${pellet.owls.join(", ")}`));
    console.log("\n" + pellet.content);
    return;
  }

  // List or search pellets
  let pellets = await pelletStore.listAll();

  if (opts.search) {
    pellets = await pelletStore.search(opts.search);
    console.log(chalk.cyan(`🔍 Search results for "${opts.search}":\n`));
  } else {
    console.log(chalk.cyan(`📦 Knowledge Pellets:\n`));
  }

  if (pellets.length === 0) {
    console.log(
      chalk.dim(
        "No pellets found. Trigger a Parliament session to generate some.",
      ),
    );
    return;
  }

  for (const p of pellets) {
    console.log(`${chalk.bold(p.title)} ${chalk.dim(`(ID: ${p.id})`)}`);
    console.log(`  ${chalk.dim("Tags: ")} ${p.tags.join(", ")}`);
    console.log(`  ${chalk.dim("Owls: ")} ${p.owls.join(", ")}`);
    console.log("");
  }
}

// ─── Skills Command ────────────────────────────────────────────────

import { ClawHubClient } from "./skills/clawhub.js";

async function skillsCommand(opts: {
  list?: boolean;
  search?: string;
  read?: string;
  install?: string;
  clawhubSearch?: string;
}) {
  const { skillsLoader, config } = await bootstrap();

  if (!config.skills?.enabled) {
    console.log(chalk.yellow("⚠️  Skills are not enabled in config."));
    console.log(
      chalk.dim("Add 'skills' to your stackowl.config.json to enable them."),
    );
    process.exit(1);
  }

  const registry = skillsLoader.getRegistry();

  // Handle ClawHub search
  if (opts.clawhubSearch) {
    const clawHub = new ClawHubClient();
    console.log(
      chalk.cyan(`🔍 Searching ClawHub for "${opts.clawhubSearch}"...\n`),
    );

    try {
      const results = await clawHub.search(opts.clawhubSearch, 10);
      console.log(chalk.bold(`Found ${results.total} skills:\n`));

      for (const skill of results.skills) {
        const emoji = "📦";
        console.log(`${emoji} ${chalk.bold(skill.name)}`);
        console.log(`   ${chalk.dim(skill.description)}`);
        console.log(
          `   ${chalk.dim(`⭐ ${skill.stars} stars | 👇 ${skill.downloads} downloads | by ${skill.author}`)}`,
        );
        console.log(
          chalk.dim(`   Install: stackowl skills --install ${skill.slug}`),
        );
        console.log("");
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(chalk.red(`ClawHub search failed: ${msg}`));
    }
    return;
  }

  // Handle ClawHub install
  if (opts.install) {
    const clawHub = new ClawHubClient();
    const targetDir = config.skills?.directories?.[0] || "./workspace/skills";

    console.log(chalk.cyan(`Installing "${opts.install}" from ClawHub...\n`));

    try {
      await clawHub.install(opts.install, targetDir);
      console.log(chalk.green(`\n✓ Successfully installed!`));
      console.log(chalk.dim(`Reload skills: restart the assistant`));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(chalk.red(`Installation failed: ${msg}`));
    }
    return;
  }

  if (opts.read) {
    const skill = registry.get(opts.read);
    if (!skill) {
      console.error(chalk.red(`❌ Skill "${opts.read}" not found.`));
      process.exit(1);
    }

    console.log(chalk.bold.cyan(`🎯 SKILL: ${skill.name}`));
    console.log(chalk.dim(`Description: ${skill.description}`));
    console.log(chalk.dim(`Source: ${skill.sourcePath}`));
    console.log(chalk.dim(`Enabled: ${skill.enabled ? "Yes" : "No"}`));

    if (skill.requiredEnv && skill.requiredEnv.length > 0) {
      console.log(chalk.dim(`Required env: ${skill.requiredEnv.join(", ")}`));
    }
    if (skill.requiredBins && skill.requiredBins.length > 0) {
      console.log(chalk.dim(`Required bins: ${skill.requiredBins.join(", ")}`));
    }

    console.log("\n" + chalk.bold("Instructions:"));
    console.log(skill.instructions);
    return;
  }

  // List or search skills
  let skills = opts.search
    ? skillsLoader.search(opts.search)
    : registry.listAll();

  if (opts.search) {
    console.log(chalk.cyan(`🔍 Search results for "${opts.search}":\n`));
  } else if (opts.list || (!opts.search && !opts.read)) {
    console.log(chalk.cyan(`🎯 Loaded Skills:\n`));
  }

  if (skills.length === 0) {
    console.log(chalk.dim("No skills found."));
    if (!config.skills.directories?.length) {
      console.log(
        chalk.dim("Configure 'skills.directories' in stackowl.config.json"),
      );
    }
    return;
  }

  for (const s of skills) {
    const emoji = s.metadata.openclaw?.emoji || "🎯";
    console.log(
      `${emoji} ${chalk.bold(s.name)} ${chalk.dim(s.enabled ? "" : "(disabled)")}`,
    );
    console.log(`   ${chalk.dim(s.description)}`);
    if (s.requiredEnv?.length || s.requiredBins?.length) {
      const reqs: string[] = [];
      if (s.requiredEnv?.length) reqs.push(`env: ${s.requiredEnv.join(", ")}`);
      if (s.requiredBins?.length)
        reqs.push(`bins: ${s.requiredBins.join(", ")}`);
      console.log(`   ${chalk.yellow(reqs.join(" | "))}`);
    }
    console.log("");
  }
}

// ─── Evolve Command ────────────────────────────────────────────────

async function evolveCommand(owlName: string) {
  const { evolutionEngine } = await bootstrap();

  if (!owlName) {
    console.error(chalk.red("❌ Please provide an owl name to evolve."));
    process.exit(1);
  }

  try {
    const mutated = await evolutionEngine.evolve(owlName);
    if (!mutated) {
      console.log(
        chalk.yellow(
          `\n🦤 No evolution triggered for ${owlName}. They didn't learn anything new.`,
        ),
      );
    }
  } catch (error) {
    console.error(chalk.red("\nEvolution failed:"), error);
    process.exit(1);
  }
}

// ─── Telegram Command ────────────────────────────────────────────

async function telegramCommand(opts: { owl?: string; withCli?: boolean }) {
  const b = await bootstrap();

  // Read bot token
  let botToken = "";
  const telegramConfig = (b.config as any)["telegram"] as
    | { botToken?: string }
    | undefined;
  if (telegramConfig?.botToken) {
    botToken = telegramConfig.botToken;
  } else {
    const credPath = join(process.cwd(), ".stackowl.credentials.json");
    if (existsSync(credPath)) {
      try {
        const creds = JSON.parse(await readFile(credPath, "utf-8")) as Record<
          string,
          string
        >;
        botToken = creds["telegramBotToken"] ?? "";
      } catch {
        /* ignore */
      }
    }
  }

  if (!botToken) {
    console.error(chalk.red("❌ Telegram bot token not found."));
    console.log(
      chalk.dim(
        '  Run ./start.sh to configure, or set "telegram.botToken" in stackowl.config.json',
      ),
    );
    process.exit(1);
  }

  const owl = opts.owl
    ? b.owlRegistry.get(opts.owl)
    : b.owlRegistry.getDefault();
  if (!owl) {
    console.error(chalk.red(`❌ Owl "${opts.owl}" not found.`));
    process.exit(1);
  }

  const provider = b.providerRegistry.getDefault();
  if (!(await provider.healthCheck())) {
    console.error(
      chalk.red(`❌ Cannot reach ${provider.name}. Is it running?`),
    );
    process.exit(1);
  }

  console.log(
    chalk.green(`✓ Provider: ${provider.name}`) +
      chalk.dim(` (model: ${b.config.defaultModel})`),
  );
  console.log(chalk.green(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`));
  console.log(chalk.green(`✓ Channel: 📱 Telegram`));

  const gateway = await buildGateway(b, owl);
  const adapter = new TelegramAdapter(gateway, {
    botToken,
    chatIdsPath: join(b.workspacePath, "known_chat_ids.json"),
  });
  gateway.register(adapter);

  // Perch: broadcast through gateway so all channels receive it
  const perch = new PerchManager(
    provider,
    b.config,
    b.owlRegistry,
    (msg: string) => gateway.broadcastProactive(msg),
  );
  perch.addPerch(new FilePerch(b.workspacePath));
  await perch.startAll();

  const shutdown = () => {
    console.log(chalk.dim("\n🦉 Shutting down..."));
    perch.stopAll();
    adapter.stop();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  await adapter.start();

  if (opts.withCli) {
    console.log(chalk.dim("\n📱 Telegram running. CLI also active.\n"));
    await chatCommand(opts.owl);
  }
}

// ─── Web Command ─────────────────────────────────────────────────

async function webCommand(port?: string) {
  const resolvedPort = port ? parseInt(port, 10) : 3000;

  const {
    config,
    providerRegistry,
    owlRegistry,
    pelletStore,
    sessionStore,
    toolRegistry,
    workspacePath,
  } = await bootstrap();
  const provider = providerRegistry.getDefault();

  // Health check
  const healthy = await provider.healthCheck();
  if (!healthy) {
    console.error(
      chalk.red(`❌ Cannot reach ${provider.name} provider. Is it running?`),
    );
    process.exit(1);
  }

  const server = new StackOwlServer(
    config,
    provider,
    owlRegistry,
    pelletStore,
    sessionStore,
    toolRegistry,
    workspacePath,
    resolvedPort,
  );

  await server.start();
}

// ─── All Command ─────────────────────────────────────────────────

async function allCommand(opts: { owl?: string; port?: string }) {
  // 1. Start Web Server
  const resolvedPort = opts.port ? parseInt(opts.port, 10) : 3000;
  const {
    config,
    providerRegistry,
    owlRegistry,
    pelletStore,
    sessionStore,
    toolRegistry,
    workspacePath,
  } = await bootstrap();
  const provider = providerRegistry.getDefault();

  const healthy = await provider.healthCheck();
  if (!healthy) {
    console.error(
      chalk.red(`❌ Cannot reach ${provider.name} provider. Is it running?`),
    );
    process.exit(1);
  }

  const server = new StackOwlServer(
    config,
    provider,
    owlRegistry,
    pelletStore,
    sessionStore,
    toolRegistry,
    workspacePath,
    resolvedPort,
  );
  await server.start();

  // 2. Check for Telegram
  let botToken = "";
  const configAny = config as unknown as Record<string, unknown>;
  const telegramConfig = configAny["telegram"] as
    | { botToken?: string; enabled?: boolean }
    | undefined;

  if (telegramConfig?.botToken) {
    botToken = telegramConfig.botToken;
  } else {
    const credPath = join(process.cwd(), ".stackowl.credentials.json");
    if (existsSync(credPath)) {
      try {
        const creds = JSON.parse(await readFile(credPath, "utf-8")) as Record<
          string,
          string
        >;
        botToken = creds["telegramBotToken"] ?? "";
      } catch {}
    }
  }

  if (botToken) {
    // Start Telegram and then CLI
    await telegramCommand({ owl: opts.owl, withCli: true });
  } else {
    // No telegram, just start CLI directly
    await chatCommand(opts.owl);
  }
}

// ─── CLI Setup ───────────────────────────────────────────────────

program
  .name("stackowl")
  .description("🦉 StackOwl — Personal AI Assistant")
  .version("0.1.0");

program
  .command("chat")
  .description("Start an interactive chat session")
  .option("-o, --owl <name>", "Owl persona to use")
  .action(async (opts: { owl?: string }) => {
    await chatCommand(opts.owl);
  });

program
  .command("telegram")
  .description("Start Telegram bot channel")
  .option("-o, --owl <name>", "Owl persona to use")
  .option("--with-cli", "Also start CLI chat alongside Telegram")
  .action(async (opts: { owl?: string; withCli?: boolean }) => {
    await telegramCommand(opts);
  });

program
  .command("parliament [topic]")
  .description("Convene a Parliament of owls to debate a complex topic")
  .action((topic) => {
    parliamentCommand(topic).catch((err) => {
      console.error(chalk.red(`Fatal error: ${err.message}`));
      process.exit(1);
    });
  });

program
  .command("owls")
  .description("List available owl personas")
  .action(async () => {
    await owlsCommand();
  });

program
  .command("pellets")
  .description("Manage and search Knowledge Pellets")
  .option("-s, --search <query>", "Search pellets by keyword or tag")
  .option("-r, --read <id>", "Read the full content of a specific pellet")
  .action((opts) => {
    pelletsCommand(opts).catch((err) => {
      console.error(chalk.red(`Fatal error: ${err.message}`));
      process.exit(1);
    });
  });

program
  .command("evolve <owlName>")
  .description("Trigger a DNA evolution pass for a specific owl")
  .action((owlName) => {
    evolveCommand(owlName).catch((err) => {
      console.error(chalk.red(`Fatal error: ${err.message}`));
      process.exit(1);
    });
  });

program
  .command("web")
  .description("Start the StackOwl Web UI Server")
  .option("-p, --port <number>", "Port to listen on", "3000")
  .action((opts) => {
    webCommand(opts.port).catch((err) => {
      console.error(chalk.red(`Fatal error: ${err.message}`));
      process.exit(1);
    });
  });

program
  .command("status")
  .description("Show system status and provider health")
  .action(async () => {
    await statusCommand();
  });

program
  .command("skills")
  .description("Manage OpenCLAW-compatible skills")
  .option("-l, --list", "List all loaded skills")
  .option("-s, --search <query>", "Search skills by name or description")
  .option("-r, --read <name>", "Show detailed info for a specific skill")
  .option("-i, --install <slug>", "Install a skill from ClawHub")
  .option(
    "--clawhub-search <query>",
    "Search ClawHub for skills without installing",
  )
  .action(
    async (opts: {
      list?: boolean;
      search?: string;
      read?: string;
      install?: string;
      clawhubSearch?: string;
    }) => {
      await skillsCommand(opts).catch((err) => {
        console.error(chalk.red(`Fatal error: ${err.message}`));
        process.exit(1);
      });
    },
  );

program
  .command("all")
  .description(
    "Start all available channels (CLI, Web, and optionally Telegram)",
  )
  .option("-o, --owl <name>", "Owl persona to use")
  .option("-p, --port <number>", "Port for Web UI", "3000")
  .action((opts) => {
    allCommand(opts).catch((err) => {
      console.error(chalk.red(`Fatal error: ${err.message}`));
      process.exit(1);
    });
  });

// Default to chat if no command given
program.action(async () => {
  await chatCommand();
});

program.parse();

/**
 * StackOwl — Main Entry Point
 *
 * Initializes the StackOwl system and starts the CLI interface.
 */

// ── Global crash guards ───────────────────────────────────────────
// Without these, unhandled rejections silently kill the process on Node 22+.
process.on("uncaughtException", (err) => {
  console.error("\n[FATAL] Uncaught exception — process will exit:");
  console.error(err);
  process.exit(1);
});
process.on("unhandledRejection", (reason) => {
  console.error("\n[FATAL] Unhandled promise rejection — process will exit:");
  console.error(reason);
  process.exit(1);
});

import { resolve } from "node:path";
import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { program } from "commander";
import chalk from "chalk";
// log imported by adapters/gateway internally
import { initFileLog, log } from "./logger.js";
import { loadConfig } from "./config/loader.js";
import { ProviderRegistry } from "./providers/registry.js";
import { OwlRegistry } from "./owls/registry.js";
import { ToolRegistry } from "./tools/registry.js";
import { ShellTool } from "./tools/shell.js";
import { SandboxTool } from "./tools/sandbox.js";
import { ReadFileTool, WriteFileTool, EditFileTool } from "./tools/files.js";
import { SendFileTool } from "./tools/send_file.js";
import { DuckDuckGoSearchTool } from "./tools/search.js";
import { WebCrawlTool } from "./tools/web.js";
import { SessionStore } from "./memory/store.js";
import { StackOwlEventBus } from "./events/index.js";
import { TaskQueue } from "./queue/index.js";
import { CostTracker } from "./costs/index.js";
import { DefaultAgentRegistry } from "./agents/index.js";
import { RateLimiter } from "./ratelimit/index.js";
import type { RateLimitRule } from "./ratelimit/index.js";
import { OrchestrateTasksTool } from "./tools/orchestrate.js";
import { SummonParliamentTool } from "./tools/parliament.js";
import { PatchTool } from "./tools/toolsmith.js";
import { ScreenshotTool } from "./tools/screenshot.js";
// ── macOS native tools ──
import {
  AppleCalendarTool,
  AppleRemindersTool,
  AppleContactsTool,
  AppleNotesTool,
  AppleMailTool,
  SystemInfoTool,
  NotificationTool,
  ClipboardTool,
  FocusModeTool,
  SpotlightSearchTool,
  TextToSpeechTool,
  SystemControlsTool,
  MusicControlTool,
  IMessageTool,
  AirDropTool,
} from "./tools/macos/index.js";
// ── Utility tools ──
import {
  CalculatorTool,
  TimerTool,
  WeatherTool,
  CurrencyConverterTool,
  PasswordGeneratorTool,
  JSONTransformTool,
  ProcessManagerTool,
  DailyBriefingTool,
  HabitTrackerTool,
  ExpenseTrackerTool,
  QuickCaptureTool,
  TranslatorTool,
  UnitConverterTool,
  QRCodeTool,
} from "./tools/utils/index.js";
// ── Web utility tools ──
import {
  WebMonitorTool,
  LinkPreviewTool,
  RSSFeedTool,
  YouTubeSearchTool,
  BookmarkManagerTool,
  WebImageSearchTool,
} from "./tools/web-utils/index.js";
// ── Dev tools ──
import {
  DockerTool,
  GitTool,
  APITesterTool,
  NetworkScanTool,
  CronJobTool,
} from "./tools/dev/index.js";
// ── Creative tools ──
import {
  MermaidDiagramTool,
  MarkdownRenderTool,
  ImageGenerationTool,
  SpeechToTextTool,
} from "./tools/creative/index.js";
// ── Data tools ──
import {
  SpreadsheetTool,
  DataVisualizationTool,
  FileEncryptTool,
  FileOrganizeTool,
  ArchiveTool,
  PDFReaderTool,
  OCRTool,
} from "./tools/data/index.js";
// ── Computer Use ──
import { ComputerUseTool } from "./tools/computer-use/index.js";
// ── Anti-bot Web Scraping ──
import { ScraplingTool } from "./tools/web-scrapling.js";
import { CamoFoxTool } from "./tools/camofox.js";
import { ParliamentOrchestrator } from "./parliament/orchestrator.js";
import { PelletStore } from "./pellets/store.js";
import { OwlEvolutionEngine } from "./owls/evolution.js";
import { LearningEngine } from "./learning/self-study.js";
import { LearningOrchestrator } from "./learning/orchestrator.js";
import { MemoryReflexionEngine } from "./memory/reflexion.js";
import { OwlInnerLife } from "./owls/inner-life.js";
import { KnowledgeCouncil } from "./parliament/knowledge-council.js";
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
import { SlackAdapter } from "./gateway/adapters/slack.js";
import { CLIAdapter } from "./gateway/adapters/cli.js";
import { BootSplash } from "./cli/splash.js";
import type { BootStep } from "./cli/splash.js";
import { OnboardingWizard } from "./cli/onboarding.js";
import { VoiceChannelAdapter } from "./gateway/adapters/voice.js";
import { WhisperSTT } from "./voice/stt.js";
import { FaceEmitter } from "./events/face-emitter.js";
import { PreferenceStore } from "./preferences/store.js";
import { ReflexionEngine } from "./evolution/reflexion.js";
import { SkillsLoader } from "./skills/index.js";
import { MCPManager } from "./tools/mcp/manager.js";
import { MicroLearner } from "./learning/micro-learner.js";
import { MutationTracker } from "./owls/mutation-tracker.js";
import { SelfLearningCoordinator } from "./learning/coordinator.js";
import { MemorySearcher } from "./memory-threads/searcher.js";
import { RecallMemoryTool } from "./tools/recall.js";
import { RememberTool } from "./tools/remember.js";
import { PelletRecallTool } from "./tools/pellet-recall.js";
import { initEmbedder } from "./pellets/embedder.js";
import { selfSeedIfEmpty } from "./pellets/self-seed.js";
import { EchoChamberDetector } from "./echo-chamber/detector.js";
import { EchoCheckTool } from "./tools/echo-check.js";
import { JournalGenerator } from "./growth-journal/generator.js";
import { GrowthJournalTool } from "./tools/journal.js";
import { QuestManager } from "./quests/manager.js";
import { QuestTool } from "./tools/quest.js";
import { CapsuleManager } from "./capsules/manager.js";
import { TimeCapsuleTool } from "./tools/capsule.js";
import { ConstellationMiner } from "./constellations/miner.js";
import { SocraticEngine } from "./socratic/engine.js";
import { join } from "node:path";
// ── Persistent Browser Pool ──
import { BrowserPool, initSmartFetch, initCamoFox } from "./browser/index.js";
// ── New Feature Modules (Phase 1-3) ──
import { InfraProfileStore, InfraDetector } from "./infra/index.js";
import { ConnectorResolver } from "./connectors/index.js";
import {
  WorkflowChainStore,
  WorkflowExecutor,
  RunbookMiner,
} from "./workflows/index.js";
import { HealthChecker } from "./monitoring/index.js";
import { AutoConfigDetector } from "./infra/auto-config.js";
import { CrossAppPlanner } from "./orchestrator/cross-app.js";
import { createWorkflowTool } from "./tools/workflow.js";
import { createMonitorTool } from "./tools/monitor.js";
import { createConnectorTool } from "./tools/connector.js";
import { IntentStateMachine, CommitmentTrackerImpl } from "./intent/index.js";
import { UserPreferenceModel } from "./preferences/model.js";
import { WorkingContextManager } from "./memory/working-context.js";
import { EpisodicMemory } from "./memory/episodic.js";
import { FactStore } from "./memory/fact-store.js";
import { FactExtractor } from "./memory/fact-extractor.js";
import { MemoryDatabase } from "./memory/db.js";
import { KnowledgeGraph } from "./knowledge/index.js";
import { GoalGraph } from "./goals/graph.js";
import { ProactiveIntentionLoop } from "./intent/proactive-loop.js";
import { PlanLedger } from "./tasks/plan-ledger.js";

// ─── Bootstrap StackOwl ──────────────────────────────────────────

async function bootstrap() {
  const basePath = resolve(homedir(), ".stackowl");
  mkdirSync(basePath, { recursive: true });
  const config = await loadConfig(basePath);
  const workspacePath = resolve(basePath, config.workspace);

  // Initialize file-based session log (overwrites on each restart)
  initFileLog(workspacePath);

  // Create browser pool config — browsers are launched lazily on first request,
  // not on startup, to avoid blocking startup and wasting resources.
  let browserPool: BrowserPool | undefined;
  if (config.browser?.enabled !== false && !config.camofox?.enabled) {
    browserPool = new BrowserPool({
      poolSize: config.browser?.poolSize ?? 2,
      warmUp: config.browser?.warmUp ?? false,
      stealthMode: config.browser?.stealthMode ?? true,
      userDataDir: resolve(workspacePath, ".browser-data"),
      proxy: config.browser?.proxy,
      headless: config.browser?.headless ?? true,
    });
    initSmartFetch(browserPool);
  }

  // Initialize CamoFox (Tier 4 anti-detection browser) — enabled by default
  if (config.camofox?.enabled !== false) {
    initCamoFox({
      baseUrl: config.camofox?.baseUrl ?? "http://localhost:9377",
      apiKey: config.camofox?.apiKey,
      defaultUserId: config.camofox?.defaultUserId ?? "stackowl",
      defaultTimeout: config.camofox?.defaultTimeout ?? 30000,
    });
  }

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

  // Mutation Tracker — tracks DNA mutation outcomes for rollback decisions
  const mutationTracker = new MutationTracker(owlRegistry, workspacePath);
  await mutationTracker.init();

  // Initialize tools
  const toolRegistry = new ToolRegistry();
  const { WebSearchTool } = await import("./compat/index.js");
  const { SessionsListTool, SessionsHistoryTool, SessionStatusTool } =
    await import("./compat/tools/sessions.js");
  const { MemorySearchTool, MemoryGetTool } =
    await import("./compat/tools/memory.js");
  const { CronTool } = await import("./compat/tools/cron.js");
  const { BrowserTool } = await import("./compat/tools/browser.js");
  toolRegistry.registerAll([
    // ── Core tools ──
    ShellTool,
    SandboxTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    // ── Web & search ──
    DuckDuckGoSearchTool,
    WebCrawlTool,
    new WebSearchTool(
      "brave",
      process.env.BRAVE_API_KEY || process.env.WEB_SEARCH_API_KEY,
    ),
    new BrowserTool(workspacePath),
    // ── Media & files ──
    SendFileTool,
    ScreenshotTool,
    // ── Cognitive ──
    new SummonParliamentTool(),
    OrchestrateTasksTool,
    // ── Memory & sessions ──
    new MemorySearchTool(workspacePath),
    new MemoryGetTool(workspacePath),
    new SessionsListTool(workspacePath),
    new SessionsHistoryTool(workspacePath),
    new SessionStatusTool(),
    // ── System ──
    new CronTool(workspacePath),
    PatchTool,
    // ── macOS Native ──
    AppleCalendarTool,
    AppleRemindersTool,
    AppleContactsTool,
    AppleNotesTool,
    AppleMailTool,
    SystemInfoTool,
    NotificationTool,
    ClipboardTool,
    FocusModeTool,
    SpotlightSearchTool,
    TextToSpeechTool,
    SystemControlsTool,
    MusicControlTool,
    IMessageTool,
    AirDropTool,
    // ── Utilities ──
    CalculatorTool,
    TimerTool,
    WeatherTool,
    CurrencyConverterTool,
    PasswordGeneratorTool,
    JSONTransformTool,
    ProcessManagerTool,
    DailyBriefingTool,
    HabitTrackerTool,
    ExpenseTrackerTool,
    QuickCaptureTool,
    TranslatorTool,
    UnitConverterTool,
    QRCodeTool,
    // ── Web Utils ──
    WebMonitorTool,
    LinkPreviewTool,
    RSSFeedTool,
    YouTubeSearchTool,
    BookmarkManagerTool,
    WebImageSearchTool,
    // ── Dev ──
    DockerTool,
    GitTool,
    APITesterTool,
    NetworkScanTool,
    CronJobTool,
    // ── Creative ──
    MermaidDiagramTool,
    MarkdownRenderTool,
    ImageGenerationTool,
    SpeechToTextTool,
    // ── Data ──
    SpreadsheetTool,
    DataVisualizationTool,
    FileEncryptTool,
    FileOrganizeTool,
    ArchiveTool,
    PDFReaderTool,
    OCRTool,
    // ── Computer Use (Desktop Automation) ──
    ComputerUseTool,
    // ── Anti-bot Web Scraping ──
    ScraplingTool,
    // ── Anti-detection Browser (CamoFox / Firefox) ──
    CamoFoxTool,
    // ── Memory & Recall ──
    new RecallMemoryTool(),
    new RememberTool(),
    PelletRecallTool,
    // ── Growth & Wisdom ──
    new EchoCheckTool(),
    new GrowthJournalTool(),
    new QuestTool(),
    new TimeCapsuleTool(),
  ]);

  // Initialize session store
  const sessionStore = new SessionStore(workspacePath);
  await sessionStore.init();

  // Initialize pellet store (with AI-powered deduplication)
  // Initialize pellet embedder (Ollama nomic-embed-text) before PelletStore
  // so vector search is available from the first save/search call.
  initEmbedder().catch((e) => log.engine.warn("[Init] Embedder: " + (e instanceof Error ? e.message : String(e))));

  const pelletStore = new PelletStore(
    workspacePath,
    providerRegistry.getDefault(),
    config.pellets?.dedup,
  );
  await pelletStore.init();

  // Self-seed foundational pellets on first startup (empty store)
  // This gives the model self-knowledge (identity, tools, skills) immediately
  // after a reset — prevents "acts like generic LLM" regression.
  selfSeedIfEmpty(
    pelletStore,
    workspacePath,
    toolRegistry.getAllDefinitions().map((t) => t.name),
  ).catch((e) =>
    log.engine.warn(`[SelfSeed] Failed (non-fatal): ${e instanceof Error ? e.message : e}`)
  );

  // Build/refresh knowledge graph in background (non-blocking)
  pelletStore
    .buildGraph()
    .catch((err) =>
      console.warn(
        `[PelletGraph] Build failed (non-fatal): ${err instanceof Error ? err.message : err}`,
      ),
    );

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
      providerRegistry,
    );

  // Learning Orchestrator — new unified learning system (TopicFusion + Synthesis + Reflexion)
  const learningOrchestratorFactory = (
    owl: import("./owls/persona.js").OwlInstance,
  ) =>
    new LearningOrchestrator(
      providerRegistry.getDefault(),
      owl,
      config,
      pelletStore,
      workspacePath,
      providerRegistry,
    );

  // Micro-Learner — per-message lightweight signal extraction
  const microLearner = new MicroLearner(workspacePath);
  await microLearner.load().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));

  // SQLite Memory Database — single source of truth for all persistent memory.
  // Created early so FactStore, EpisodicMemory, and FeedbackStore can use it.
  // The gateway will reuse this instance (ctx.db) instead of creating its own.
  const memoryDb = new MemoryDatabase(workspacePath);
  // One-time JSON migration (fire-and-forget)
  memoryDb
    .importFromJson(workspacePath)
    .catch((err) =>
      console.warn(`[MemoryDatabase] JSON import failed: ${err}`),
    );

  // Episodic Memory — LLM-extracted session summaries for cross-session recall
  const episodicMemory = new EpisodicMemory(workspacePath, undefined, memoryDb);
  await episodicMemory.load();

  // Fact Store — Mem0-inspired structured fact memory with conflict resolution
  const factStore = new FactStore(workspacePath, {}, memoryDb);
  await factStore.load();

  // Knowledge Graph — entity relationships with semantic search
  const knowledgeGraph = new KnowledgeGraph(workspacePath);
  await knowledgeGraph.load();

  // Fact Extractor — LLM-powered extraction from conversations
  const factExtractor = new FactExtractor(providerRegistry.getDefault());

  // Memory Searcher — cross-system recall for conversational threads
  const memorySearcher = new MemorySearcher(
    pelletStore,
    sessionStore,
    workspacePath,
    providerRegistry.getDefault(),
  );
  await memorySearcher.loadIndex().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));

  // Echo Chamber Detector — bias analysis on conversation history
  const echoChamberDetector = new EchoChamberDetector(
    sessionStore,
    providerRegistry.getDefault(),
    workspacePath,
  );
  await echoChamberDetector.load().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));

  // Growth Journal Generator
  const journalGenerator = new JournalGenerator(
    pelletStore,
    sessionStore,
    providerRegistry.getDefault(),
    workspacePath,
  );

  // Quest Manager — gamified learning
  const questManager = new QuestManager(
    providerRegistry.getDefault(),
    pelletStore,
    workspacePath,
  );

  // Capsule Manager — time capsules
  const capsuleManager = new CapsuleManager(
    providerRegistry.getDefault(),
    workspacePath,
  );

  // Constellation Miner — cross-pellet pattern discovery
  const constellationMiner = new ConstellationMiner(
    providerRegistry.getDefault(),
    pelletStore,
    workspacePath,
  );

  // Socratic Engine — per-session question-only mode
  const socraticEngine = new SocraticEngine();

  // Evolution Engine (DNA) — with holistic user profile for smarter evolution
  const evolutionEngine = new OwlEvolutionEngine(
    providerRegistry.getDefault(),
    config,
    sessionStore,
    owlRegistry,
    () => microLearner.getProfile(),
    episodicMemory,
    memoryDb,
  );

  // Apply DNA decay for all owls if overdue (runs at most once per week per owl)
  for (const o of owlRegistry.listOwls()) {
    await evolutionEngine.applyDecayIfNeeded(o.persona.name).catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));
  }

  // Self-improvement system
  const synthesizer = new ToolSynthesizer();
  const ledger = new CapabilityLedger();
  const loader = new DynamicToolLoader(ledger);
  const evolution = new EvolutionHandler(
    synthesizer,
    ledger,
    loader,
    memoryDb,
    owlRegistry,
  );
  ledger.setDb(memoryDb);

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
  // Always include built-in defaults + any user-configured directories
  const skillsLoader = new SkillsLoader();
  {
    const builtInSkillsDir = resolve(
      new URL(".", import.meta.url).pathname,
      "skills/defaults",
    );
    const userDirs = (config.skills?.directories ?? []).map((d) =>
      resolve(basePath, d),
    );
    // Built-in defaults first, then user overrides (user skills take precedence)
    const allSkillsDirs = [builtInSkillsDir, ...userDirs];
    const skillsCount = await skillsLoader.load({
      directories: allSkillsDirs,
      watch: config.skills?.watch ?? false,
      watchDebounceMs: config.skills?.watchDebounceMs ?? 250,
    });
    console.log(chalk.dim(`  [Loaded ${skillsCount} skills]`));
  }

  // ── Infrastructure Profile ──
  const infraProfile = new InfraProfileStore(workspacePath);
  await infraProfile.load().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));
  const infraDetector = new InfraDetector(infraProfile);

  // ── App Connectors ──
  const connectorResolver = new ConnectorResolver(workspacePath);
  await connectorResolver.load().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));

  // ── Workflow Chains ──
  const workflowStore = new WorkflowChainStore(workspacePath);
  await workflowStore.load().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));

  // ── Health Monitoring ──
  const healthChecker = new HealthChecker(workspacePath);
  await healthChecker.load().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));

  // ── Intent State Machine ──
  const intentStateMachine = new IntentStateMachine(workspacePath);
  await intentStateMachine.load();

  // ── Commitment Tracker ──
  const commitmentTracker = new CommitmentTrackerImpl(workspacePath);
  await commitmentTracker.load();

  // ── User Preference Model (behavioral inference) ──
  const preferenceModel = new UserPreferenceModel(workspacePath);
  await preferenceModel.load();

  // ── Mem0-Inspired Memory Layer ──
  const { MemoryRetriever } = await import("./memory/memory-retriever.js");
  const { MemoryFeedback } = await import("./memory/memory-feedback.js");
  const memoryRetriever = new MemoryRetriever(
    episodicMemory,
    factStore,
    knowledgeGraph,
    preferenceModel,
    pelletStore,
    providerRegistry.getDefault(),
  );
  const memoryFeedback = new MemoryFeedback(factStore, knowledgeGraph);

  // ── Memory Layers ──
  const workingContextManager = new WorkingContextManager();

  // ── Ground State View (Phase 4) ──
  const { GroundStateView } = await import("./cognition/ground-state.js");
  const groundState = new GroundStateView(
    factStore,
    providerRegistry.getDefault(),
  );

  // ── Goal Graph ──
  const goalGraph = new GoalGraph(workspacePath);
  await goalGraph.load();

  // Plan Ledger — persistent plan state for cross-session resume
  const planLedger = new PlanLedger(workspacePath);
  await planLedger.init();

  // Register new tools
  const defaultProvider = providerRegistry.getDefault();
  const workflowExecutor = new WorkflowExecutor(
    toolRegistry,
    defaultProvider,
    workspacePath,
    owlRegistry,
    config,
  );
  toolRegistry.registerAll([
    createWorkflowTool(workflowStore, workflowExecutor),
    createMonitorTool(healthChecker),
    createConnectorTool(connectorResolver),
  ]);

  // Load tool permissions from config
  if (config.tools?.permissions) {
    toolRegistry.loadPermissions(config.tools.permissions as any);
  }

  // MCP server connections
  const mcpManager = new MCPManager();
  if (config.mcp?.servers?.length) {
    const mcpCount = await mcpManager.connectAll(
      config.mcp.servers,
      toolRegistry,
    );
    if (mcpCount > 0) {
      console.log(
        chalk.dim(
          `  [MCP: ${mcpCount} tool(s) from ${config.mcp.servers.length} server(s)]`,
        ),
      );
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
    learningOrchestratorFactory,
    preferenceStore,
    reflexionEngine,
    skillsLoader,
    microLearner,
    memorySearcher,
    echoChamberDetector,
    journalGenerator,
    questManager,
    capsuleManager,
    constellationMiner,
    socraticEngine,
    infraProfile,
    infraDetector,
    connectorResolver,
    workflowStore,
    workflowExecutor,
    healthChecker,
    browserPool,
    intentStateMachine,
    commitmentTracker,
    preferenceModel,
    mutationTracker,
    workingContextManager,
    memoryDb,
    episodicMemory,
    factStore,
    factExtractor,
    knowledgeGraph,
    memoryRetriever,
    memoryFeedback,
    goalGraph,
    groundState,
    mcpManager,
    planLedger,
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

  // Legacy memory.md retired (Phase 3) — replaced by structured memory:
  // FactStore (semantic facts) + ConversationDigest (L1 session memory) +
  // MemoryRetriever (episodic + reflexion search). ContextBuilder queries these
  // on every message. Do NOT load memory.md — it is unsearchable and obsolete.
  // MemoryConsolidator.loadMemory() is no longer called.

  // Load reflexion-based structured memory (Phase 3 replacement for memory.md)
  let reflexionContext = "";
  try {
    const reflexion = new MemoryReflexionEngine(b.workspacePath, provider, owl);
    reflexionContext = await reflexion.getForSystemPrompt();
    if (reflexionContext) {
      console.log(chalk.dim("  [Reflexion memory loaded]"));
    }
  } catch {
    /* non-blocking — first run will have no reflexion data */
  }

  // Owl Inner Life — persistent desires, mood, opinions, inner monologue
  const innerLife = new OwlInnerLife(provider, owl, b.workspacePath);
  await innerLife.load().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));
  console.log(chalk.dim(`  [Inner Life loaded for ${owl.persona.name}]`));

  // Knowledge Council — group learning & peer review sessions
  const knowledgeCouncil = new KnowledgeCouncil(
    provider,
    b.owlRegistry,
    b.config,
    b.pelletStore,
    b.workspacePath,
    b.providerRegistry,
  );

  // ─── New Infrastructure (Improvements #1-7) ──────────────────
  const eventBus = new StackOwlEventBus();
  // Wire face state machine — translates EventBus events to face:state
  new FaceEmitter(eventBus).start();
  // Give the pellet store an event bus so it fires pellet:created live
  b.pelletStore.eventBus = eventBus;
  const taskQueue = new TaskQueue(b.config.queue);

  // Self-Learning Coordinator — wires SignalBus, MutationTracker, and UserPreferenceModel
  const selfLearningCoordinator = new SelfLearningCoordinator(
    b.microLearner,
    b.mutationTracker,
    b.preferenceModel,
    owl.persona.name,
  );

  // Cost tracker
  let costTracker: CostTracker | undefined;
  if (b.config.costs?.enabled !== false) {
    const costPath = resolve(b.workspacePath, "cost_tracking.json");
    costTracker = new CostTracker(b.config.costs?.budget, costPath);
    await costTracker.load();
  }

  // Agent registry (empty — agents register themselves later)
  const agentRegistry = new DefaultAgentRegistry();

  // Rate limiter
  const rules: RateLimitRule[] = [];
  if (b.config.gateway?.rateLimit) {
    rules.push({
      name: "session-minute",
      maxRequests: b.config.gateway.rateLimit.maxPerMinute,
      windowMs: 60_000,
    });
    rules.push({
      name: "session-hour",
      maxRequests: b.config.gateway.rateLimit.maxPerHour,
      windowMs: 3_600_000,
    });
  }
  if (b.config.rateLimiting?.perProvider) {
    for (const [prov, limit] of Object.entries(
      b.config.rateLimiting.perProvider,
    )) {
      rules.push({
        name: `provider-${prov}-minute`,
        maxRequests: limit.maxPerMinute,
        windowMs: 60_000,
      });
      if (limit.maxPerHour) {
        rules.push({
          name: `provider-${prov}-hour`,
          maxRequests: limit.maxPerHour,
          windowMs: 3_600_000,
        });
      }
    }
  }
  const rateLimiter = rules.length > 0 ? new RateLimiter(rules) : undefined;

  // ─── Plugin System ──────────────────────────────────────────
  const { ServiceRegistry } = await import("./plugins/services.js");
  const { HookPipeline } = await import("./plugins/hook-pipeline.js");
  const { PluginRegistry } = await import("./plugins/registry.js");
  const { PluginLifecycleManager } = await import("./plugins/lifecycle.js");

  const serviceRegistry = new ServiceRegistry();
  const hookPipeline = new HookPipeline();
  const pluginRegistry = new PluginRegistry();
  const pluginLifecycle = new PluginLifecycleManager(
    pluginRegistry,
    serviceRegistry,
    hookPipeline,
    b.toolRegistry,
    eventBus,
  );

  // Load plugins from configured directories
  if (b.config.plugins?.directories) {
    for (const dir of b.config.plugins.directories) {
      const resolvedDir = resolve(b.workspacePath, "..", dir);
      await pluginLifecycle.loadAll(resolvedDir);
    }
    await pluginLifecycle.startAll();
  }

  // ─── Hot Reload Manager ────────────────────────────────────
  const { HotReloadManager } = await import("./reload/manager.js");
  const hotReloadManager = new HotReloadManager(
    eventBus,
    b.config.skills?.watchDebounceMs ?? 250,
  );

  // ─── ACP Router ────────────────────────────────────────────
  const { ACPRouter } = await import("./acp/router.js");
  const { SessionBridgeFactory } = await import("./acp/bridge.js");
  const bridgeFactory = new SessionBridgeFactory(b.sessionStore);
  const acpRouter = new ACPRouter(agentRegistry, eventBus, bridgeFactory);

  // ─── New Feature Modules ──────────────────────────────────────
  const autoConfigDetector = new AutoConfigDetector(
    b.infraProfile,
    b.connectorResolver,
  );
  const runbookMiner = new RunbookMiner(b.workflowStore, provider);
  const crossAppPlanner = new CrossAppPlanner(
    provider,
    b.toolRegistry,
    b.workspacePath,
  );

  // Connect connector presets to MCP manager
  const connectorMcpConfigs = b.connectorResolver.resolveToMcpConfigs();
  if (connectorMcpConfigs.length > 0) {
    const mcpManager = new MCPManager();
    const mcpCount = await mcpManager.connectAll(
      connectorMcpConfigs,
      b.toolRegistry,
    );
    if (mcpCount > 0) {
      console.log(
        chalk.dim(
          `  [Connectors: ${mcpCount} tool(s) from ${connectorMcpConfigs.length} app connector(s)]`,
        ),
      );
    }
  }

  // Start health monitoring
  b.healthChecker.startAll();

  // ─── Cognitive Loop (Self-Improvement Engine) ──────────────
  // Drives continuous learning from inner desires, capability gaps,
  // pattern mining, skill evolution, and reflexion.
  const { CognitiveLoop } = await import("./cognition/loop.js");
  const skillsDir = b.skillsLoader
    ? resolve(b.workspacePath, "skills")
    : undefined;
  const cognitiveLoop = new CognitiveLoop(
    {
      provider,
      owl,
      config: b.config,
      innerLife,
      learningOrchestrator: b.learningOrchestratorFactory(owl),
      learningEngine: b.learningEngineFactory(owl),
      reflexionEngine: b.reflexionEngine,
      skillsRegistry: b.skillsLoader?.getRegistry(),
      sessionStore: b.sessionStore,
      pelletStore: b.pelletStore,
      capabilityLedger: b.ledger,
      microLearner: b.microLearner,
      toolRegistry: b.toolRegistry,
      skillsDir,
      owlRegistry: b.owlRegistry,
      evolutionEngine: b.evolutionEngine,
      providerRegistry: b.providerRegistry,
      skillsLoader: b.skillsLoader,
    },
    b.config.cognition,
  );
  cognitiveLoop.start();

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
    learningOrchestrator: b.learningOrchestratorFactory(owl),
    innerLife,
    instinctRegistry: b.instinctRegistry,
    instinctEngine: b.instinctEngine,
    preferenceStore: b.preferenceStore,
    reflexionEngine: b.reflexionEngine,
    skillsLoader: b.skillsLoader,
    memoryContext: reflexionContext || undefined,
    cwd: b.workspacePath,
    providerRegistry: b.providerRegistry,
    microLearner: b.microLearner,
    memorySearcher: b.memorySearcher,
    echoChamberDetector: b.echoChamberDetector,
    journalGenerator: b.journalGenerator,
    questManager: b.questManager,
    capsuleManager: b.capsuleManager,
    constellationMiner: b.constellationMiner,
    socraticEngine: b.socraticEngine,
    // New infrastructure
    eventBus,
    taskQueue,
    costTracker,
    agentRegistry,
    rateLimiter,
    selfLearningCoordinator,
    // Plugin, Reload & ACP
    pluginRegistry,
    serviceRegistry,
    hookPipeline,
    hotReloadManager,
    acpRouter,
    // Feature modules
    infraProfile: b.infraProfile,
    infraDetector: b.infraDetector,
    connectorResolver: b.connectorResolver,
    workflowStore: b.workflowStore,
    healthChecker: b.healthChecker,
    autoConfigDetector,
    runbookMiner,
    crossAppPlanner,
    knowledgeCouncil,
    cognitiveLoop,
    intentStateMachine: b.intentStateMachine,
    commitmentTracker: b.commitmentTracker,
    preferenceModel: b.preferenceModel,
    workingContextManager: b.workingContextManager,
    db: b.memoryDb,
    episodicMemory: b.episodicMemory,
    factStore: b.factStore,
    factExtractor: b.factExtractor,
    knowledgeGraph: b.knowledgeGraph,
    memoryRetriever: b.memoryRetriever,
    memoryFeedback: b.memoryFeedback,
    goalGraph: b.goalGraph,
    groundState: b.groundState,
    mcpManager: b.mcpManager,
    planLedger: b.planLedger,
    proactiveLoop: new ProactiveIntentionLoop(
      b.commitmentTracker,
      b.intentStateMachine,
      b.goalGraph,
      undefined, // contextMesh initialized separately in gateway
    ),
  });

  return gateway;
}

// ─── Chat Command ────────────────────────────────────────────────

async function chatCommand(owlName?: string) {
  // ── Phase 0: onboarding (first launch) ────────────────────────
  const configPath = resolve(homedir(), ".stackowl", "stackowl.config.json");
  if (!existsSync(configPath)) {
    const wizard = new OnboardingWizard(configPath);
    const completed = await wizard.run();
    if (!completed) {
      console.log(chalk.yellow("\nSetup cancelled. Run again to configure StackOwl."));
      process.exit(0);
    }
  }

  // ── Phase 1: fast bootstrap (no UI yet) ───────────────────────
  const splash = new BootSplash();

  // Bootstrap steps — each measured and shown in the splash
  let b!: Awaited<ReturnType<typeof bootstrap>>;
  let owl!: NonNullable<ReturnType<Awaited<ReturnType<typeof bootstrap>>["owlRegistry"]["get"]>>;
  let gateway!: OwlGateway;

  const steps: BootStep[] = [
    {
      label: "Loading config & providers",
      fn: async () => {
        b = await bootstrap();
        const o = owlName ? b.owlRegistry.get(owlName) : b.owlRegistry.getDefault();
        if (!o) {
          console.error(chalk.red(`\n  Owl "${owlName}" not found.`));
          process.exit(1);
        }
        owl = o;
      },
    },
    {
      label: "Connecting to provider",
      fn: async () => {
        // Non-fatal: if provider is unreachable the window still opens and
        // the error is surfaced in the chat area on the first message.
        const provider = b.providerRegistry.getDefault();
        await provider.healthCheck().catch(() => {/* silently continue */});
      },
    },
    {
      label: "Initialising memory & tools",
      fn: async () => {
        gateway = await buildGateway(b, owl);
      },
    },
    {
      label: "Starting perch watchers",
      fn: async () => {
        await b.perchManager.startAll();
      },
    },
  ];

  await splash.run(steps, () => ({
    owlEmoji: owl.persona.emoji,
    owlName:  owl.persona.name,
    provider: b.providerRegistry.getDefault().name,
    model:    b.config.defaultModel,
  }));

  // ── Phase 2: interactive session ──────────────────────────────
  const adapter = new CLIAdapter(gateway);
  gateway.register(adapter);

  process.on("SIGINT", async () => {
    b.perchManager.stopAll();
    adapter.stop();
    await b.browserPool?.shutdown();
    process.exit(0);
  });

  await adapter.start();
}

// ─── Voice Command ───────────────────────────────────────────────

async function voiceCommand(opts: {
  owl?: string;
  model?: string;
  voice?: string;
  rate?: number;
}) {
  const b = await bootstrap();

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

  if (process.platform !== "darwin") {
    console.error(
      chalk.red("❌ Voice mode currently requires macOS (uses `say` for TTS)."),
    );
    process.exit(1);
  }

  console.log(
    chalk.green(`✓ Connected to ${provider.name}`) +
      chalk.dim(` (model: ${b.config.defaultModel})`),
  );

  // Merge: CLI flags > config.voice > defaults
  const vc = b.config.voice ?? {};
  const resolvedModel  = (opts.model  ?? vc.model       ?? "base.en") as import("./voice/stt.js").WhisperModel;
  const resolvedVoice  =  opts.voice  ?? vc.systemVoice ?? "Samantha";
  const resolvedRate   =  opts.rate   ?? vc.speakRate   ?? 200;
  const resolvedThresh = vc.silenceThreshold  ?? 500;
  const resolvedDur    = vc.silenceDurationMs ?? 1500;

  console.log(
    chalk.dim(`  Model: ${resolvedModel} | Voice: ${resolvedVoice} | Rate: ${resolvedRate} wpm`),
  );

  // Pre-warm: build whisper.cpp binary + download model before the interactive loop.
  // Shows real compiler/download output so the user knows what's happening.
  const stt = new WhisperSTT({ model: resolvedModel });
  try {
    await stt.ensureReady();
  } catch (err) {
    console.error(chalk.red(`\n❌ Voice setup failed: ${(err as Error).message}`));
    process.exit(1);
  }
  console.log(chalk.green("✓ Voice ready — mic and transcription available\n"));

  const gateway = await buildGateway(b, owl);
  const adapter = new VoiceChannelAdapter(gateway, {
    model:             resolvedModel,
    systemVoice:       resolvedVoice,
    speakRate:         resolvedRate,
    silenceThreshold:  resolvedThresh,
    silenceDurationMs: resolvedDur,
    sttInstance:       stt,           // reuse the already-warmed instance
  });
  gateway.register(adapter);

  await b.perchManager.startAll();

  process.on("SIGINT", async () => {
    b.perchManager.stopAll();
    adapter.stop();
    await b.browserPool?.shutdown();
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

  const {
    providerRegistry,
    owlRegistry,
    config,
    pelletStore,
    toolRegistry,
    memoryDb,
  } = await bootstrap();
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
    toolRegistry,
    memoryDb,
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

async function pelletsCommand(opts: {
  search?: string;
  read?: string;
  dedup?: boolean;
  dryRun?: boolean;
  graph?: boolean;
  related?: string;
}) {
  const { pelletStore } = await bootstrap();

  // ─── Bulk Dedup ────────────────────────────────────────────────
  if (opts.dedup) {
    const { bulkDedup } = await import("./pellets/bulk-dedup.js");
    console.log(
      chalk.cyan(
        opts.dryRun
          ? "🔍 Running bulk dedup DRY RUN (no changes will be made)...\n"
          : "🧹 Running bulk dedup (duplicates will be merged/removed)...\n",
      ),
    );
    const stats = await bulkDedup(pelletStore, pelletStore.getDeduplicator(), {
      dryRun: opts.dryRun,
    });
    console.log("\n" + chalk.bold("Results:"));
    console.log(`  Total pellets:  ${stats.total}`);
    console.log(`  Checked:        ${stats.checked}`);
    console.log(chalk.green(`  Kept:           ${stats.kept}`));
    console.log(chalk.yellow(`  Skipped:        ${stats.skipped}`));
    console.log(chalk.cyan(`  Merged:         ${stats.merged}`));
    console.log(chalk.magenta(`  Superseded:     ${stats.superseded}`));
    if (stats.errors > 0)
      console.log(chalk.red(`  Errors:         ${stats.errors}`));
    return;
  }

  // ─── Knowledge Graph ─────────────────────────────────────────
  if (opts.graph) {
    console.log(chalk.cyan("🕸️  Building knowledge graph...\n"));
    await pelletStore.buildGraph();
    const stats = await pelletStore.kuzuGraph.getStats();
    console.log(chalk.bold("Graph Stats:"));
    console.log(`  Nodes (pellets): ${stats.nodes}`);
    console.log(`  Edges (links):   ${stats.edges}`);
    return;
  }

  // ─── Find Related ────────────────────────────────────────────
  if (opts.related) {
    console.log(
      chalk.cyan(`🔗 Finding pellets related to "${opts.related}"...\n`),
    );
    const results = await pelletStore.searchWithGraph(opts.related as string, 10);
    if (results.length === 0) {
      console.log(chalk.dim("No related pellets found."));
      return;
    }
    for (const r of results) {
      console.log(
        `${chalk.bold(r.title)} ${chalk.dim(`(${r.id})`)}` +
          ` — tags: ${r.tags.join(", ")}`,
      );
    }
    return;
  }

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

  const botToken = b.config.telegram?.botToken ?? "";

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

  const shutdown = async () => {
    console.log(chalk.dim("\n🦉 Shutting down..."));
    perch.stopAll();
    adapter.stop();
    await b.browserPool?.shutdown();
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

// ─── Slack Command ───────────────────────────────────────────────

async function slackCommand(opts: { owl?: string; withCli?: boolean }) {
  const b = await bootstrap();

  const slackConfig = b.config.slack;
  if (!slackConfig?.botToken || !slackConfig?.appToken) {
    console.error(chalk.red("❌ Slack credentials not found."));
    console.log(
      chalk.dim(
        '  Set "slack.botToken" (xoxb-...) and "slack.appToken" (xapp-...) in stackowl.config.json',
      ),
    );
    console.log(chalk.dim("  See: https://api.slack.com/start/quickstart"));
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
  console.log(chalk.green(`✓ Channel: 💬 Slack`));

  const gateway = await buildGateway(b, owl);
  const adapter = new SlackAdapter(gateway, {
    botToken: slackConfig.botToken,
    appToken: slackConfig.appToken,
    signingSecret: slackConfig.signingSecret,
    allowedChannels: slackConfig.allowedChannels,
    port: slackConfig.port,
    channelIdsPath: join(b.workspacePath, "known_slack_channels.json"),
  });
  gateway.register(adapter);

  // Perch: broadcast through gateway
  const perch = new PerchManager(
    provider,
    b.config,
    b.owlRegistry,
    (msg: string) => gateway.broadcastProactive(msg),
  );
  perch.addPerch(new FilePerch(b.workspacePath));
  await perch.startAll();

  const shutdown = async () => {
    console.log(chalk.dim("\n🦉 Shutting down..."));
    perch.stopAll();
    adapter.stop();
    await b.browserPool?.shutdown();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  await adapter.start();

  if (opts.withCli) {
    console.log(chalk.dim("\n💬 Slack running. CLI also active.\n"));
    await chatCommand(opts.owl);
  }
}

// ─── Web Command ─────────────────────────────────────────────────

async function webCommand(port?: string, owlName?: string) {
  const resolvedPort = port ? parseInt(port, 10) : 3000;

  const b = await bootstrap();
  const provider = b.providerRegistry.getDefault();

  // Health check
  const healthy = await provider.healthCheck();
  if (!healthy) {
    console.error(
      chalk.red(`❌ Cannot reach ${provider.name} provider. Is it running?`),
    );
    process.exit(1);
  }

  const owl = owlName ? b.owlRegistry.get(owlName) : b.owlRegistry.getDefault();
  if (!owl) {
    console.error(chalk.red(`❌ Owl "${owlName}" not found.`));
    process.exit(1);
  }

  console.log(
    chalk.green(`✓ Provider: ${provider.name}`) +
      chalk.dim(` (model: ${b.config.defaultModel})`),
  );
  console.log(chalk.green(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`));
  console.log(chalk.green(`✓ Channel: 🌐 WebSocket Control Plane`));

  const gateway = await buildGateway(b, owl);

  const server = new StackOwlServer(
    b.config,
    gateway,
    b.owlRegistry,
    b.pelletStore,
    b.sessionStore,
    resolvedPort,
  );

  await server.start();
}

// ─── All Command ─────────────────────────────────────────────────

async function allCommand(opts: { owl?: string; port?: string }) {
  const resolvedPort = opts.port ? parseInt(opts.port, 10) : 3000;
  const b = await bootstrap();
  const provider = b.providerRegistry.getDefault();

  const healthy = await provider.healthCheck();
  if (!healthy) {
    console.error(
      chalk.red(`❌ Cannot reach ${provider.name} provider. Is it running?`),
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

  console.log(
    chalk.green(`✓ Provider: ${provider.name}`) +
      chalk.dim(` (model: ${b.config.defaultModel})`),
  );
  console.log(chalk.green(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`));

  // 1. Build Gateway (shared across all channels)
  const gateway = await buildGateway(b, owl);

  // 2. Start Web Server with WebSocket Control Plane
  const server = new StackOwlServer(
    b.config,
    gateway,
    b.owlRegistry,
    b.pelletStore,
    b.sessionStore,
    resolvedPort,
  );
  await server.start();
  console.log(
    chalk.green(`✓ Channel: 🌐 WebSocket Control Plane (port ${resolvedPort})`),
  );

  // 3. Check for Slack (start before Telegram — Telegram's bot.start() blocks)
  if (b.config.slack?.botToken && b.config.slack?.appToken) {
    try {
      const slackAdapter = new SlackAdapter(gateway, {
        botToken: b.config.slack.botToken,
        appToken: b.config.slack.appToken,
        signingSecret: b.config.slack.signingSecret,
        allowedChannels: b.config.slack.allowedChannels,
        port: b.config.slack.port,
        channelIdsPath: join(b.workspacePath, "known_slack_channels.json"),
      });
      gateway.register(slackAdapter);
      await slackAdapter.start();
      console.log(chalk.green(`✓ Channel: 💬 Slack`));
    } catch (err) {
      console.error(
        chalk.red(
          `✗ Slack failed to start: ${err instanceof Error ? err.message : err}`,
        ),
      );
    }
  }

  // 4. Check for Telegram
  // Note: grammY's bot.start() blocks forever (long-polling), so we start it
  // without await. The onStart callback confirms it's running.
  if (b.config.telegram?.botToken) {
    const telegramAdapter = new TelegramAdapter(gateway, {
      botToken: b.config.telegram.botToken,
      allowedUserIds: b.config.telegram.allowedUserIds,
      chatIdsPath: join(b.workspacePath, "known_chat_ids.json"),
    });
    gateway.register(telegramAdapter);
    telegramAdapter.start().catch((err) => {
      console.error(
        chalk.red(
          `✗ Telegram failed: ${err instanceof Error ? err.message : err}`,
        ),
      );
    });
    console.log(chalk.green(`✓ Channel: 📱 Telegram`));
  }

  // Agent Watch — supervises Claude Code / OpenCode sessions
  {
    const { AgentWatchManager } = await import("./agent-watch/index.js");
    const agentWatch = new AgentWatchManager({
      sendToUser: async (userId, channelId, html) => {
        await gateway.sendProactive(channelId, userId, html, true);
      },
    });
    agentWatch.start();
    gateway.agentWatch = agentWatch;
    console.log(chalk.green(`✓ Agent Watch: http://localhost:3111/agent-watch`));
  }

  // 5. Start CLI adapter
  const cliAdapter = new CLIAdapter(gateway);
  gateway.register(cliAdapter);

  // Perch: broadcast through gateway
  const perch = new PerchManager(
    provider,
    b.config,
    b.owlRegistry,
    (msg: string) => gateway.broadcastProactive(msg),
  );
  perch.addPerch(new FilePerch(b.workspacePath));
  await perch.startAll();

  const shutdown = async () => {
    console.log(chalk.dim("\n🦉 Shutting down all channels..."));
    perch.stopAll();
    cliAdapter.stop();
    await b.browserPool?.shutdown();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  await cliAdapter.start();
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
  .command("voice")
  .description("Start an offline voice session (mic → Whisper STT → owl → macOS say)")
  .option("-o, --owl <name>", "Owl persona to use")
  .option("-m, --model <name>", "Whisper model: tiny.en | base.en | small.en | medium", "base.en")
  .option("-v, --voice <name>", "macOS voice name (e.g. Samantha, Alex, Karen)", "Samantha")
  .option("-r, --rate <wpm>", "TTS words-per-minute", (v) => parseInt(v, 10), 200)
  .action(async (opts: { owl?: string; model?: string; voice?: string; rate?: number }) => {
    await voiceCommand(opts);
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
  .command("slack")
  .description("Start Slack bot channel")
  .option("-o, --owl <name>", "Owl persona to use")
  .option("--with-cli", "Also start CLI chat alongside Slack")
  .action(async (opts: { owl?: string; withCli?: boolean }) => {
    await slackCommand(opts);
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
  .option("--dedup", "Run bulk deduplication on all pellets")
  .option(
    "--dry-run",
    "Preview dedup without making changes (use with --dedup)",
  )
  .option("--graph", "Build and display knowledge graph stats")
  .option(
    "--related <query>",
    "Find pellets related to a topic via graph traversal",
  )
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
  .description("Start the StackOwl WebSocket Control Plane server")
  .option("-p, --port <number>", "Port to listen on", "3000")
  .option("-o, --owl <name>", "Owl persona to use")
  .action((opts) => {
    webCommand(opts.port, opts.owl).catch((err) => {
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

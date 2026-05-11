/**
 * StackOwl — Main Entry Point
 *
 * Initializes the StackOwl system and starts the CLI interface.
 */

// ── Global crash guards ───────────────────────────────────────────
// Without these, unhandled rejections silently kill the process on Node 22+.
process.on("uncaughtException", (err) => {
  process.stderr.write("\n[FATAL] Uncaught exception — process will exit:\n");
  process.stderr.write(String(err) + "\n");
  process.exit(1);
});
process.on("unhandledRejection", (reason) => {
  process.stderr.write("\n[FATAL] Unhandled promise rejection — process will exit:\n");
  process.stderr.write(String(reason) + "\n");
  process.exit(1);
});

import { resolve } from "node:path";
import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { program } from "commander";
// log imported by adapters/gateway internally
import { initFileLog, log } from "./logger.js";
import { loadConfig } from "./config/loader.js";
import { ProviderRegistry } from "./providers/registry.js";
import type { ProviderRole } from "./providers/registry.js";
import { OwlRegistry } from "./owls/registry.js";
import { ToolRegistry } from "./tools/registry.js";
import { ToolTracker } from "./tools/tracker.js";
import { ShellTool } from "./tools/shell.js";
import { CredentialsTool } from "./tools/credentials.js";
import { SandboxTool } from "./tools/sandbox.js";
import { ReadFileTool, WriteFileTool, EditFileTool } from "./tools/files.js";
import { SendFileTool } from "./tools/send_file.js";
import { CreateSkillTool } from "./tools/create-skill.js";
import { UpdateMemoryTool } from "./tools/update-memory.js";
import { WebSearchTool as InternalWebSearchTool } from "./tools/search.js";
import { WebFetchTool } from "./tools/web.js";
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
// ── Unified Tool Facades (Phase 7a) ──
import { createMemoryTool } from "./tools/memory-unified.js";
import { createMacosCommsTool } from "./tools/macos/comms-unified.js";
import { createMacosSystemTool } from "./tools/macos/system-unified.js";
// ── Tool Cortex 7d — new capability tools ──
import { VisionTool }      from "./tools/vision.js";
import { DocumentTool }    from "./tools/document.js";
import { CodeSandboxTool } from "./tools/code-sandbox.js";
import { DbQueryTool }     from "./tools/db-query.js";
import { ScheduleTool }    from "./tools/schedule.js";
// ── Tool Cortex T22 — frontmost-aware live browser control ──
import { createLiveBrowserTool } from "./tools/live-browser/index.js";
import { detectFrontmostBrowser } from "./tools/live-browser/frontmost.js";
import { SafariDriver } from "./tools/live-browser/safari-driver.js";
import { ChromeDriver } from "./tools/live-browser/chrome-driver.js";
import { PuppeteerChromeBackend } from "./tools/live-browser/chrome-backend.js";
import {
  ensureChromeBootstrap,
  defaultIsPortOpen,
  defaultRelaunchChrome,
  defaultWaitForPort,
} from "./tools/live-browser/bootstrap.js";
import { BrowserBridge } from "./tools/computer-use/browser/cdp.js";
import { ParliamentOrchestrator } from "./parliament/orchestrator.js";
import { PelletStore } from "./pellets/store.js";
import { OwlEvolutionEngine } from "./owls/evolution.js";
import { LearningOrchestrator } from "./learning/orchestrator.js";
import { MemoryReflexionEngine } from "./memory/reflexion.js";
import { OwlInnerLife } from "./owls/inner-life.js";
import { KnowledgeCouncil } from "./parliament/knowledge-council.js";
import { ToolSynthesizer } from "./evolution/synthesizer.js";
import { CapabilityLedger } from "./evolution/ledger.js";
import { DynamicToolLoader } from "./evolution/loader.js";
import { EvolutionHandler } from "./evolution/handler.js";
import { SkillInstaller, parseInstallSource } from "./skills/installer.js";
import { StackOwlServer } from "./server/index.js";
import { OwlGateway } from "./gateway/core.js";
import { TelegramAdapter } from "./gateway/adapters/telegram.js";
import { SlackAdapter } from "./gateway/adapters/slack.js";
import { DiscordAdapter } from "./gateway/adapters/discord.js";
import { WhatsAppAdapter } from "./gateway/adapters/whatsapp.js";
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
import { SkillInstallTool } from "./tools/skill-install.js";
import { ReadLogsTool } from "./tools/read-logs.js";
import { PelletRecallTool } from "./tools/pellet-recall.js";
import { initEmbedder, setEmbedderCacheDir } from "./pellets/embedder.js";
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
import { BrowserPool, initSmartFetch, initCamoFox, getCamoFoxClient } from "./browser/index.js";
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
import { MemoryRepository } from "./memory/repository.js";
import { UnifiedMemory } from "./memory/unified.js";
import { WorkspaceGit } from "./workspace/git.js";
import { MemoryWriter } from "./memory/writer.js";
import { MemoryBus } from "./memory/bus.js";
import { HitlCheckpointStore } from "./engine/hitl.js";
import { KnowledgeGraph } from "./knowledge/index.js";
import { GoalGraph } from "./goals/graph.js";
// ── Epic 7: Knowledge Building ─────────────────────────────────────
import { EventBasedPelletGenerator } from "./pellets/event-based-generator.js";
import { PelletRetriever } from "./pellets/pellet-retriever.js";
import { KnowledgeBase } from "./pellets/knowledge-base.js";
import { ProactiveKnowledgeGenerator } from "./pellets/proactive-generator.js";
import { makeProviderRouter } from "./pellets/generator.js";
import { ProactiveIntentionLoop } from "./intent/proactive-loop.js";
import { BackgroundOrchestrator } from "./background/orchestrator.js";
import { CronService } from "./cron/service.js";
import { IsolatedRunner } from "./cron/isolated-runner.js";
import { DEFAULT_CRON_JOBS } from "./cron/default-jobs.js";
import { PlanLedger } from "./tasks/plan-ledger.js";
import { SignalPool } from "./signals/pool.js";
import { SignalClassifier } from "./signals/classifier.js";
import {
  GitStatusCollector,
  TimeContextCollector,
  SystemCollector,
  ActiveFileCollector,
  ClipboardCollector,
  FileSystemCollector,
} from "./signals/collectors.js";
import { GoalVerifier } from "./tools/goal-verifier.js";

// ─── Boot helpers ────────────────────────────────────────────────

export async function probeCamoFoxAtBoot(
  availability: { update: (backend: "camofox", status: Record<string, unknown>) => Promise<void> },
  cfg: { baseUrl: string },
): Promise<void> {
  let ready = false;
  let lastError: string | undefined;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(`${cfg.baseUrl}/tabs`, { signal: ctrl.signal }).finally(() => clearTimeout(t));
    ready = r.ok;
  } catch (err) {
    lastError = err instanceof Error ? err.message : String(err);
  }
  await availability.update("camofox", {
    installed: ready,
    ready,
    lastProbe: new Date().toISOString(),
    lastError,
  });
}

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

  // Initialize CamoFox (anti-detection browser) — keep client wired for tools…
  let camofoxClient: import("./browser/camofox-client.js").CamoFoxClient | null = null;
  if (config.camofox?.enabled !== false) {
    initCamoFox({
      baseUrl: config.camofox?.baseUrl ?? "http://localhost:9377",
      apiKey: config.camofox?.apiKey,
      defaultUserId: config.camofox?.defaultUserId ?? "stackowl",
      defaultTimeout: config.camofox?.defaultTimeout ?? 30000,
    });
    // …and probe-only readiness check writes the availability map for the LLM.
    const { RuntimeAvailability } = await import("./runtime/availability.js");
    const runtimeAvailability = new RuntimeAvailability();
    await probeCamoFoxAtBoot(runtimeAvailability, {
      baseUrl: config.camofox?.baseUrl ?? "http://localhost:9377",
    });
    // Capture the CamoFox client for wiring into gateway context
    camofoxClient = getCamoFoxClient();
  }

  // Initialize Puppeteer fetcher (Tier 3 autonomous headless browser)
  let puppeteerFetcher: import("./browser/puppeteer-fetcher.js").PuppeteerFetcher | undefined;
  {
    const { PuppeteerFetcher } = await import("./browser/puppeteer-fetcher.js");
    const fetcher = new PuppeteerFetcher();
    const ready = await fetcher.probe();
    if (ready) {
      try {
        await fetcher.init();
        puppeteerFetcher = fetcher;
      } catch (err) {
        // Non-fatal: Chrome binary exists but failed to launch — puppeteer tier unavailable
        // Use process.stderr.write so this surfaces even when console is suppressed during boot
        process.stderr.write(`[puppeteer] Chrome launch failed (tier 3 unavailable): ${err instanceof Error ? err.message : String(err)}\n`);
      }
    }
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

  // Auto-assign roles based on provider type
  providerRegistry.autoAssignRoles(
    Object.entries(config.providers).map(([name, conf]) => ({ name, type: conf.type }))
  );

  // Apply explicit role overrides from config
  if (config.roles) {
    for (const [role, providerName] of Object.entries(config.roles)) {
      if (providerName) {
        providerRegistry.assignRole(role as ProviderRole, providerName);
      }
    }
  }

  // Initialize owl registry
  const owlRegistry = new OwlRegistry(workspacePath);
  await owlRegistry.loadAll();

  // Mutation Tracker — tracks DNA mutation outcomes for rollback decisions
  const mutationTracker = new MutationTracker(owlRegistry, workspacePath);
  await mutationTracker.init();

  // Initialize tools
  const toolRegistry = new ToolRegistry();
  const { SessionsListTool, SessionsHistoryTool, SessionStatusTool } =
    await import("./compat/tools/sessions.js");
  const { CronTool } = await import("./compat/tools/cron.js");
  const { BrowserTool } = await import("./compat/tools/browser.js");
  const updateMemoryTool = new UpdateMemoryTool();
  toolRegistry.registerAll([
    // ── Core tools ──
    ShellTool,
    SandboxTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    // ── Credentials ──
    CredentialsTool,
    // ── Web & search ──
    InternalWebSearchTool,
    WebFetchTool,
    new BrowserTool(workspacePath),
    // ── Media & files ──
    SendFileTool,
    ScreenshotTool,
    // ── Cognitive ──
    new SummonParliamentTool(),
    OrchestrateTasksTool,
    // ── Memory & sessions ──
    // (MemorySearchTool/MemoryGetTool removed — canonical `memory` tool registered post-gateway)
    new SessionsListTool(workspacePath),
    new SessionsHistoryTool(workspacePath),
    new SessionStatusTool(),
    // ── System ──
    new CronTool(workspacePath),
    new SkillInstallTool(workspacePath),
    new CreateSkillTool(),
    updateMemoryTool,
    new ReadLogsTool(workspacePath),
    PatchTool,
    // ── macOS Native ──
    ...(process.platform === "darwin" ? [
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
    ] : []),
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
  // Initialize pellet embedder before PelletStore so vector search is available
  // from the first save/search call. Model is cached inside workspace/memory/local_cache.
  setEmbedderCacheDir(join(workspacePath, "memory", "local_cache"));
  initEmbedder().catch((e) => log.engine.warn("[Init] Embedder: " + (e instanceof Error ? e.message : String(e))));

  const pelletStore = new PelletStore(
    workspacePath,
    providerRegistry.getDefault(),
    config.pellets?.dedup,
  );
  await pelletStore.init();

  // Build/refresh knowledge graph in background (non-blocking)
  pelletStore
    .buildGraph()
    .catch((err) =>
      log.engine.warn(
        `[PelletGraph] Build failed (non-fatal): ${err instanceof Error ? err.message : err}`,
      ),
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

  // Platform layer — probes OS capabilities once at startup, caches the matrix
  // for every consumer (paths, sandbox, notifier, process, shell). Must run
  // before any tool registry sees a sandbox check.
  const { platform } = await import("./platform/index.js");
  await platform.initialize();

  // SQLite Memory Database — single source of truth for all persistent memory.
  // Created early so FactStore, EpisodicMemory, and FeedbackStore can use it.
  // The gateway will reuse this instance (ctx.db) instead of creating its own.
  const memoryDb = new MemoryDatabase(workspacePath);
  // One-time JSON migration (fire-and-forget)
  memoryDb
    .importFromJson(workspacePath)
    .catch((err) =>
      log.engine.warn(`[MemoryDatabase] JSON import failed: ${err}`),
    );

  // Late-inject the MemoryDatabase into UpdateMemoryTool (created before memoryDb above).
  updateMemoryTool.setDb(memoryDb);

  // One-shot MEMORY.md → facts table migration (idempotent)
  const { migrateMemoryMd } = await import("./memory/memory-migration.js");
  const memoryMdPath = join(homedir(), ".stackowl", "workspace", "MEMORY.md");
  migrateMemoryMd(memoryDb, memoryMdPath).catch((err) =>
    log.engine.warn(`[MemoryMigration] Migration failed: ${err}`),
  );

  // Tool Tracker — SQLite-backed tool execution history (Element 7 / schema v23).
  // Wired here so registry.execute() records every tool call into tool_executions.
  const toolTracker = new ToolTracker(memoryDb);
  toolRegistry.setTracker(toolTracker);

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
    log.engine.info(`[Init] Loaded ${synthesizedCount} synthesized tool(s) from previous sessions`);
  }

  // Skills (OpenCLAW-compatible)
  // Always include built-in defaults + any user-configured directories
  const skillsLoader = new SkillsLoader();
  {
    const userSkillsDir = SkillsLoader.userSkillsDir();
    const builtInSkillsDir = resolve(
      new URL(".", import.meta.url).pathname,
      "skills/defaults",
    );
    const userDirs = (config.skills?.directories ?? []).map((d) =>
      resolve(basePath, d),
    );
    // User's ~/.stackowl/skills/ first (highest priority), then built-in defaults, then config dirs
    const allSkillsDirs = [userSkillsDir, builtInSkillsDir, ...userDirs];
    const skillsCount = await skillsLoader.load({
      directories: allSkillsDirs,
      watch: config.skills?.watch ?? false,
      watchDebounceMs: config.skills?.watchDebounceMs ?? 250,
    });
    log.engine.info(`[Init] Loaded ${skillsCount} skills`);
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

  // Element 15 — canonical `memory` tool is registered AFTER gateway construction
  // (it depends on gateway-owned GatewayEventBus and HitlCheckpointStore).

  if (process.platform === "darwin") {
    toolRegistry.register(createMacosCommsTool({
      mail:     (args, ctx) => toolRegistry.execute("apple_mail", args, ctx),
      contacts: (args, ctx) => toolRegistry.execute("apple_contacts", args, ctx),
      imessage: (args, ctx) => toolRegistry.execute("imessage", args, ctx),
    }));

    toolRegistry.register(createMacosSystemTool({
      spotlight:     (args, ctx) => toolRegistry.execute("spotlight_search", args, ctx),
      focus_mode:    (args, ctx) => toolRegistry.execute("focus_mode", args, ctx),
      notifications: (args, ctx) => toolRegistry.execute("send_notification", args, ctx),
      system_info:   (args, ctx) => toolRegistry.execute("system_info", args, ctx),
    }));
  }

  // ── Tool Cortex 7d — register new capability tools ──
  toolRegistry.register(VisionTool);
  toolRegistry.register(DocumentTool);
  toolRegistry.register(CodeSandboxTool);
  toolRegistry.register(DbQueryTool);
  toolRegistry.register(ScheduleTool);

  // ── Tool Cortex T22 — unified live_browser ──
  // Frontmost detection + Safari (JXA) / Chrome (CDP) drivers, with one-shot
  // bootstrap that relaunches Chrome with --remote-debugging-port=9222 when
  // needed. Side effects are wired here so the tool itself stays testable.
  toolRegistry.register(
    createLiveBrowserTool({
      detectFrontmost: detectFrontmostBrowser,
      safariDriverFactory: () => new SafariDriver(),
      chromeDriverFactory: () =>
        new ChromeDriver(new PuppeteerChromeBackend(BrowserBridge.getInstance())),
      ensureChromeBootstrap: () =>
        ensureChromeBootstrap({
          isPortOpen: () => defaultIsPortOpen(),
          // No interactive prompt yet — first invocation auto-approves the
          // relaunch. A future HITL hook will gate this through the channel.
          prompt: async () => true,
          relaunchChrome: defaultRelaunchChrome,
          waitForPort: () => defaultWaitForPort(),
          connect: () => BrowserBridge.getInstance().connect(),
        }),
    }),
  );

  // Self-seed foundational pellets on first startup (empty store)
  // This gives the model self-knowledge (identity, tools, skills) immediately
  // after a reset — prevents "acts like generic LLM" regression.
  // NOTE: must run AFTER all unified tools are registered so the seed pellet
  // reflects the consolidated catalog (web, memory, macos_comms, macos_system)
  // rather than the deprecated individual tool names.
  selfSeedIfEmpty(
    pelletStore,
    workspacePath,
    toolRegistry.getAllDefinitions().map((t) => t.name),
  ).catch((e) =>
    log.engine.warn(`[SelfSeed] Failed (non-fatal): ${e instanceof Error ? e.message : e}`)
  );

  // Load tool permissions from config
  if (config.tools?.permissions) {
    toolRegistry.loadPermissions(config.tools.permissions as any);
  }

  // MCP server connections
  const mcpManager = new MCPManager();
  if (config.mcp?.servers?.length) {
    const mcpCount = await mcpManager.connectAll(
      config.mcp.servers.filter((s) => s.enabled !== false),
      toolRegistry,
    );
    if (mcpCount > 0) {
      log.engine.info(`[Init] MCP: ${mcpCount} tool(s) from ${config.mcp.servers.length} server(s)`);
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

  return {
    config,
    providerRegistry,
    owlRegistry,
    toolRegistry,
    sessionStore,
    pelletStore,
    evolutionEngine,
    workspacePath,
    evolution,
    synthesizer,
    ledger,
    loader,
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
    puppeteerFetcher,
    camofoxClient,
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
  let owlReflexion: MemoryReflexionEngine | undefined;
  try {
    owlReflexion = new MemoryReflexionEngine(b.workspacePath, provider, owl);
    reflexionContext = await owlReflexion.getForSystemPrompt();
    if (reflexionContext) {
      log.engine.info("[Init] Reflexion memory loaded");
    }
  } catch {
    /* non-blocking — first run will have no reflexion data */
  }

  // Owl Inner Life — persistent desires, mood, opinions, inner monologue
  const innerLife = new OwlInnerLife(provider, owl, b.workspacePath);
  await innerLife.load().catch((e) => log.engine.warn("[Init] " + (e instanceof Error ? e.message : String(e))));
  log.engine.info(`[Init] Inner Life loaded for ${owl.persona.name}`);

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

  // ─── Epic 7: Knowledge Building Modules ─────────────────────────
  // EventBasedPelletGenerator — subscribes to event bus for pellet generation on events
  const eventBasedGenerator = new EventBasedPelletGenerator(
    eventBus,
    b.pelletStore,
    makeProviderRouter(provider),
  );
  eventBasedGenerator.subscribe();
  log.engine.info("[Init] EventBasedPelletGenerator subscribed to event bus");

  // PelletRetriever — for relevant pellet retrieval pre-engine
  const pelletRetriever = new PelletRetriever(b.pelletStore);

  // KnowledgeBase — for knowledge base growth tracking
  const knowledgeBase = new KnowledgeBase(b.pelletStore);

  // ProactiveGenerator — for scheduled proactive knowledge generation
  const proactiveGenerator = new ProactiveKnowledgeGenerator(
    b.pelletStore,
    makeProviderRouter(provider),
  );
  // Schedule periodic knowledge council runs (every 12 hours by default)
  const councilIntervalMs = 12 * 60 * 60 * 1000;
  setInterval(() => {
    proactiveGenerator.runKnowledgeCouncil().catch((err) =>
      log.engine.warn(`[ProactiveGenerator] Council run failed: ${err instanceof Error ? err.message : String(err)}`),
    );
  }, councilIntervalMs);
  // Also run on startup after a short delay
  setTimeout(() => {
    proactiveGenerator.runKnowledgeCouncil().catch((err) =>
      log.engine.warn(`[ProactiveGenerator] Initial council run failed: ${err instanceof Error ? err.message : String(err)}`),
    );
  }, 30_000);
  log.engine.info("[Init] ProactiveKnowledgeGenerator scheduled");

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

  // Warn if model pricing table is stale (> 90 days old)
  const { PRICING_UPDATED_AT } = await import("./costs/pricing.js");
  const pricingAge = Date.now() - new Date(PRICING_UPDATED_AT).getTime();
  if (pricingAge > 90 * 86_400_000) {
    log.engine.warn(
      `[CostTracker] MODEL_PRICING may be stale (last updated ${PRICING_UPDATED_AT}). Cost routing estimates may be inaccurate. Update src/costs/pricing.ts.`,
    );
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
      log.engine.info(`[Init] Connectors: ${mcpCount} tool(s) from ${connectorMcpConfigs.length} app connector(s)`);
    }
  }

  // Start health monitoring
  b.healthChecker.startAll();

  // ─── Cognitive Loop (Self-Improvement Engine) ──────────────
  // Drives continuous learning from inner desires, capability gaps,
  // pattern mining, skill evolution, and reflexion.
  const { CognitiveLoop } = await import("./cognition/loop.js");
  const { readLogsArray } = await import("./infra/observability/reader.js");
  const { summarize: summarizeLogs } = await import("./infra/observability/analyzer.js");
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
      reflexionEngine: b.reflexionEngine,
      skillsRegistry: b.skillsLoader?.getRegistry(),
      sessionStore: b.sessionStore,
      pelletStore: b.pelletStore,
      capabilityLedger: b.ledger,
      microLearner: b.microLearner,
      toolRegistry: b.toolRegistry,
      skillsDir,
      workspacePath: b.workspacePath,
      owlRegistry: b.owlRegistry,
      evolutionEngine: b.evolutionEngine,
      providerRegistry: b.providerRegistry,
      skillsLoader: b.skillsLoader,
      logReader: readLogsArray,
      logAnalyzer: summarizeLogs,
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
    learningOrchestrator: b.learningOrchestratorFactory(owl),
    innerLife,
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
    memoryBus: new MemoryBus(owlReflexion, b.pelletStore, b.microLearner, b.workspacePath),
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
      undefined,
    ),
    // ─── Epic 7: Knowledge Building Modules ─────────────────
    pelletRetriever,
    knowledgeBase,
    proactiveGenerator,
    eventBasedGenerator,
  });

  // ─── Background Orchestrator ────────────────────────────────────
  // Drives background jobs: desire execution, memory consolidation,
  // proactive pings, and session debriefs. Needs provider + episodic memory.
  const backgroundOrchestrator = new BackgroundOrchestrator(
    provider,
    owl,
    innerLife,
    undefined,        // DesireExecutor — not yet instantiated at top level
    undefined,        // FulfillmentTracker — not yet instantiated at top level
    undefined,        // onProactiveMessage — wired after Telegram/CLI adapters attach
    undefined,        // config — use BackgroundOrchestrator defaults
    b.episodicMemory, // EpisodicMemory for runDecay() in memory-consolidation job
  );
  gateway.ctx.backgroundOrchestrator = backgroundOrchestrator;
  backgroundOrchestrator.start();

  // ─── Cron Service ────────────────────────────────────────────────
  // Runs background jobs on schedules (memory consolidation, DNA evolution,
  // pellet deduplication, desire execution, daily briefings).
  const isolatedRunner = new IsolatedRunner({ provider });

  const cronService = new CronService({
    persist: true,
    maxConcurrentRuns: 3,
    onJobFire: async (job, traceId) => {
      const result = await isolatedRunner.run(job, traceId);
      if (job.deliver) {
        if (job.deliveryTarget) {
          try {
            await gateway.sendProactive(job.deliveryTarget.channel, job.deliveryTarget.userId, result);
          } catch (err) {
            log.engine.warn("[CronService] Failed to deliver job result", { jobId: job.id });
          }
        } else {
          log.engine.info("[CronService] [DELIVER_PENDING] Job result ready but no deliveryTarget configured", {
            jobId: job.id,
            resultPreview: result.slice(0, 200),
          });
        }
      }
      return result;
    },
  });

  // Register default jobs (skip duplicates from persisted crons.json)
  let registeredCount = 0;
  for (const job of DEFAULT_CRON_JOBS) {
    try {
      cronService.addJob(job);
      registeredCount++;
    } catch {
      log.engine.debug("[startup] Cron job already registered (from persistence), skipping", { id: job.id });
    }
  }

  // Nightly fact-promotion job (deterministic SQL — no LLM call)
  // Bumps confidence on frequently-accessed Tier-0-category facts so they
  // graduate into always-injected context over time. StackOwl's equivalent
  // of OpenClaw's "dreaming" consolidation, but free (no token cost).
  try {
    cronService.addJob({
      id: "tier0-fact-promotion",
      schedule: "30 3 * * *",
      prompt: "(direct handler)",
      safetyProfile: "low",
      deliver: false,
      description: "Nightly Tier-0 fact promotion at 3:30am — bumps confidence on frequently-accessed facts",
      handler: async (traceId) => {
        const promoted = b.memoryDb.facts.promoteFrequentlyAccessed({
          minAccess: 3,
          minConfidence: 0.7,
          boost: 0.1,
        });
        log.engine.info("[cron:tier0-fact-promotion] promoted facts", { promoted, traceId });
        return JSON.stringify({ promoted });
      },
    });
    registeredCount++;
  } catch {
    log.engine.debug("[startup] tier0-fact-promotion already registered (from persistence), skipping");
  }

  log.engine.info("[startup] Cron service initialized", { registeredCount, totalDefault: DEFAULT_CRON_JOBS.length });

  // Ensure cron service stops on shutdown
  process.on("SIGINT", () => {
    cronService.stop();
  });
  process.on("SIGTERM", () => {
    cronService.stop();
  });

  // ─── Element 15 — canonical memory surface ─────────────────────
  // Repository owns all reads/writes against `memories`/`memory_invalidations`/
  // `memory_contradictions`/`memory_access_log`. Writer ingests goal-conditioned
  // extractions and listens for engine:turn_complete to expire working memories.
  const hitlCheckpointStore = new HitlCheckpointStore(b.memoryDb);
  const memoryRepo = new MemoryRepository(b.memoryDb.rawDb, gateway.gatewayEventBus);
  gateway.ctx.memoryRepo = memoryRepo;
  gateway.ctx.unifiedMemory = new UnifiedMemory(memoryRepo, b.memoryDb.rawDb, b.providerRegistry.getDefault());

  // Workspace git — local-only history for rollback
  const workspaceGit = new WorkspaceGit(b.workspacePath);
  workspaceGit.init().then(() => workspaceGit.subscribe(gateway.gatewayEventBus)).catch(() => {});

  if (gateway.ctx.intelligence) {
    const memoryWriter = new MemoryWriter({
      repo: memoryRepo,
      bus: gateway.gatewayEventBus,
      router: gateway.ctx.intelligence,
      providerRegistry: b.providerRegistry,
    });
    memoryWriter.attachBusListeners();
    gateway.ctx.memoryWriter = memoryWriter;

    // ─── Element 16b — SignalPool (ambient signal mesh) ──────────
    const providerMap = new Map<string, import("./providers/base.js").ModelProvider>();
    providerMap.set(b.config.defaultProvider ?? "default", provider);
    const signalPool = new SignalPool({
      bus: gateway.gatewayEventBus,
      classifier: SignalClassifier.create(gateway.ctx.intelligence, providerMap),
      verifier: GoalVerifier.create(gateway.ctx.intelligence, providerMap),
      goalGraph: b.goalGraph,
      config: {
        maxSignals: b.config.perches?.maxSignals ?? 32,
        consent: b.config.perches?.consent ?? {},
        enabledSources: b.config.perches?.enabledSources,
      },
      memoryRepo,
      workspacePath: b.workspacePath,
    });
    signalPool.addCollector(new GitStatusCollector(b.workspacePath));
    signalPool.addCollector(new TimeContextCollector());
    signalPool.addCollector(new SystemCollector());
    signalPool.addCollector(new ActiveFileCollector(b.workspacePath));
    signalPool.addCollector(new ClipboardCollector());
    signalPool.addCollector(
      new FileSystemCollector(b.workspacePath, b.config.perches?.watchPaths, b.config.perches?.fileWatchDebounceMs),
    );
    gateway.ctx.signalPool = signalPool;
    gateway.ctx.proactiveLoop?.setSignalPool(signalPool);
    gateway.ctx.contextPipeline?.wireSignalPool(signalPool);
    signalPool.start();

    // BlockingClassifier — LLM-based anti-bot detection for web tools
    const { BlockingClassifier } = await import("./browser/blocking-classifier.js");
    gateway.ctx.blockingClassifier = new BlockingClassifier(
      gateway.ctx.intelligence,
      providerMap,
      gateway.gatewayEventBus,
    );

    // PuppeteerFetcher — wire into context if initialized during bootstrap
    if (b.puppeteerFetcher) {
      gateway.ctx.puppeteer = b.puppeteerFetcher;
    }

    // CamoFox client and Tavily API key — wire into context for search escalation
    if (b.camofoxClient) {
      gateway.ctx.camofox = b.camofoxClient;
    }
    const tavilyApiKey = process.env["TAVILY_API_KEY"];
    if (tavilyApiKey) {
      gateway.ctx.tavilyApiKey = tavilyApiKey;
    }
  }

  // Register canonical `memory` tool (search/get/invalidate; importance ≥ 0.8
  // invalidations route through HitlCheckpointStore for human approval).
  b.toolRegistry.register(createMemoryTool({
    repo: memoryRepo,
    bus: gateway.gatewayEventBus,
    hitl: hitlCheckpointStore,
  }));

  return gateway;
}

// ─── Chat Command ────────────────────────────────────────────────

async function chatCommand(owlName?: string) {
  // ── Phase 0: onboarding (first launch) ────────────────────────
  if (process.env.STACKOWL_TUI === "v1") {
    // v1: legacy terminal wizard (explicit opt-out via STACKOWL_TUI=v1).
    const configPath = resolve(homedir(), ".stackowl", "stackowl.config.json");
    if (!existsSync(configPath)) {
      const wizard = new OnboardingWizard(configPath);
      const completed = await wizard.run();
      if (!completed) {
        log.cli.info("Setup cancelled. Run again to configure StackOwl.");
        process.exit(0);
      }
    }
  } else {
    // v2 (default): use the @clack/prompts wizard (must run before Ink mounts).
    const { needsOnboarding, runOnboardingWizard } = await import("./cli/v2/screens/onboarding-wizard.js");
    if (needsOnboarding(homedir())) {
      await runOnboardingWizard(homedir());
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
          log.cli.error(`Owl "${owlName}" not found.`);
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
  ];

  await splash.run(steps, () => ({
    owlEmoji: owl.persona.emoji,
    owlName:  owl.persona.name,
    provider: b.providerRegistry.getDefault().name,
    model:    b.config.defaultModel,
  }));

  // ── Auto-start Telegram regardless of TUI version ────────────────
  let telegramPinger: ReturnType<TelegramAdapter["getPinger"]> | undefined;
  if (b.config.telegram?.botToken) {
    const telegramAdapter = new TelegramAdapter(gateway, {
      botToken: b.config.telegram.botToken,
      allowedUserIds: (b.config.telegram as any).allowedUserIds,
      chatIdsPath: join(b.workspacePath, "known_chat_ids.json"),
    });
    gateway.register(telegramAdapter);
    telegramAdapter
      .start()
      .then(() => {
        telegramPinger = telegramAdapter.getPinger() ?? undefined;
      })
      .catch((err) => {
        process.stderr.write(`✗ Telegram failed: ${err instanceof Error ? err.message : err}\n`);
      });
  }

  // ── TUI v2 (default) — gateway is ready, hand off to v2 stack ─────
  if (process.env.STACKOWL_TUI !== "v1") {
    const { startV2 } = await import("./cli/v2/index.js");
    await startV2(gateway);
    return;
  }

  // ── Phase 2: v1 interactive session ───────────────────────────
  const adapter = new CLIAdapter(gateway, { workspacePath: b.workspacePath });
  gateway.register(adapter);

  // Share the proactive pinger with CLI so user replies get
  // recorded as engagement signals (Element 12 — Task 6.5).
  if (telegramPinger) adapter.setPinger(telegramPinger);

  process.on("SIGINT", async () => {
    adapter.stop();
    await b.browserPool?.shutdown();
    await b.puppeteerFetcher?.close();
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
    log.cli.error(`❌ Owl "${opts.owl}" not found.`);
    process.exit(1);
  }

  const provider = b.providerRegistry.getDefault();
  if (!(await provider.healthCheck())) {
    log.cli.error(`❌ Cannot reach ${provider.name}. Is it running?`);
    process.exit(1);
  }

  if (process.platform !== "darwin") {
    log.cli.error("❌ Voice mode currently requires macOS (uses `say` for TTS).");
    process.exit(1);
  }

  log.cli.info(`✓ Connected to ${provider.name} (model: ${b.config.defaultModel})`);

  // Merge: CLI flags > config.voice > defaults
  const vc = b.config.voice ?? {};
  const resolvedModel  = (opts.model  ?? vc.model       ?? "base.en") as import("./voice/stt.js").WhisperModel;
  const resolvedVoice  =  opts.voice  ?? vc.systemVoice ?? "Samantha";
  const resolvedRate   =  opts.rate   ?? vc.speakRate   ?? 200;
  const resolvedThresh = vc.silenceThreshold  ?? 500;
  const resolvedDur    = vc.silenceDurationMs ?? 1500;

  log.cli.info(`  Model: ${resolvedModel} | Voice: ${resolvedVoice} | Rate: ${resolvedRate} wpm`);

  // Pre-warm: build whisper.cpp binary + download model before the interactive loop.
  // Shows real compiler/download output so the user knows what's happening.
  const stt = new WhisperSTT({ model: resolvedModel });
  try {
    await stt.ensureReady();
  } catch (err) {
    log.cli.error(`❌ Voice setup failed: ${(err as Error).message}`);
    process.exit(1);
  }
  log.cli.info("✓ Voice ready — mic and transcription available");

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

  process.on("SIGINT", async () => {
    adapter.stop();
    await b.browserPool?.shutdown();
    await b.puppeteerFetcher?.close();
    process.exit(0);
  });

  await adapter.start();
}

// ─── Parliament Command ──────────────────────────────────────────

async function parliamentCommand(topic?: string) {
  if (!topic || topic.trim() === "") {
    log.cli.error("❌ Please provide a topic for the Parliament to debate.");
    log.cli.info('Example: stackowl parliament "Should we migrate from PostgreSQL to DynamoDB?"');
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
      log.cli.error("❌ Parliament requires at least 2 owls. Create more OWL.md files.");
      process.exit(1);
    }
    participants.length = 0;
    participants.push(...allOwls.slice(0, 4));
  }

  log.cli.info("Summoning Parliament...");

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

    log.cli.info("=== FINAL REPORT ===\n" + orchestrator.formatSessionMarkdown(session));
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    log.cli.error(`Parliament session failed: ${msg}`, error);
  }
}

// ─── Owls Command ────────────────────────────────────────────────

async function owlsCommand() {
  const { owlRegistry } = await bootstrap();
  const owls = owlRegistry.listOwls();

  log.cli.info("🦉 StackOwl — Registered Owls");

  if (owls.length === 0) {
    log.cli.info("  No owls found. Check your workspace/owls/ directory.");
    return;
  }

  for (const owl of owls) {
    const p = owl.persona;
    const d = owl.dna;
    log.cli.info(`  ${p.emoji} ${p.name} — ${p.type}`);
    log.cli.info(
      `     Challenge: ${d.evolvedTraits.challengeLevel} | Gen: ${d.generation} | Convos: ${d.interactionStats.totalConversations}`,
    );
    log.cli.info(`     Specialties: ${p.specialties.join(", ")}`);
    log.cli.info("");
  }
}

// ─── Status Command ──────────────────────────────────────────────

async function statusCommand() {
  const { config, providerRegistry } = await bootstrap();

  log.cli.info("🦉 StackOwl — System Status");

  const healthResults = await providerRegistry.healthCheckAll();
  for (const [name, healthy] of Object.entries(healthResults)) {
    const icon = healthy ? "✓" : "✗";
    const label = name === config.defaultProvider ? `${name} (default)` : name;
    log.cli.info(`  ${icon} ${label}`);
  }

  log.cli.info(`\n  Default model: ${config.defaultModel}`);
  log.cli.info(`  Gateway: ws://${config.gateway.host}:${config.gateway.port}`);
  log.cli.info(`  Workspace: ${config.workspace}`);
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
    log.cli.info(
      opts.dryRun
        ? "🔍 Running bulk dedup DRY RUN (no changes will be made)..."
        : "🧹 Running bulk dedup (duplicates will be merged/removed)...",
    );
    const stats = await bulkDedup(pelletStore, pelletStore.getDeduplicator(), {
      dryRun: opts.dryRun,
    });
    log.cli.info("Results:");
    log.cli.info(`  Total pellets:  ${stats.total}`);
    log.cli.info(`  Checked:        ${stats.checked}`);
    log.cli.info(`  Kept:           ${stats.kept}`);
    log.cli.info(`  Skipped:        ${stats.skipped}`);
    log.cli.info(`  Merged:         ${stats.merged}`);
    log.cli.info(`  Superseded:     ${stats.superseded}`);
    if (stats.errors > 0)
      log.cli.info(`  Errors:         ${stats.errors}`);
    return;
  }

  // ─── Knowledge Graph ─────────────────────────────────────────
  if (opts.graph) {
    log.cli.info("🕸️  Building knowledge graph...");
    await pelletStore.buildGraph();
    const stats = await pelletStore.kuzuGraph.getStats();
    log.cli.info("Graph Stats:");
    log.cli.info(`  Nodes (pellets): ${stats.nodes}`);
    log.cli.info(`  Edges (links):   ${stats.edges}`);
    return;
  }

  // ─── Find Related ────────────────────────────────────────────
  if (opts.related) {
    log.cli.info(`🔗 Finding pellets related to "${opts.related}"...`);
    const results = await pelletStore.searchWithGraph(opts.related as string, 10);
    if (results.length === 0) {
      log.cli.info("No related pellets found.");
      return;
    }
    for (const r of results) {
      log.cli.info(`${r.title} (${r.id}) — tags: ${r.tags.join(", ")}`);
    }
    return;
  }

  if (opts.read) {
    // Read a specific pellet
    const pellet = await pelletStore.get(opts.read);
    if (!pellet) {
      log.cli.error(`❌ Pellet "${opts.read}" not found.`);
      process.exit(1);
    }

    log.cli.info(`📦 PELLET: ${pellet.title}`);
    log.cli.info(`Generated: ${new Date(pellet.generatedAt).toLocaleString()}`);
    log.cli.info(`Source: ${pellet.source}`);
    log.cli.info(`Tags: ${pellet.tags.join(", ")}`);
    log.cli.info(`Owls: ${pellet.owls.join(", ")}`);
    log.cli.info("\n" + pellet.content);
    return;
  }

  // List or search pellets
  let pellets = await pelletStore.listAll();

  if (opts.search) {
    pellets = await pelletStore.search(opts.search);
    log.cli.info(`🔍 Search results for "${opts.search}":`);
  } else {
    log.cli.info("📦 Knowledge Pellets:");
  }

  if (pellets.length === 0) {
    log.cli.info("No pellets found. Trigger a Parliament session to generate some.");
    return;
  }

  for (const p of pellets) {
    log.cli.info(`${p.title} (ID: ${p.id})`);
    log.cli.info(`  Tags: ${p.tags.join(", ")}`);
    log.cli.info(`  Owls: ${p.owls.join(", ")}`);
    log.cli.info("");
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
    log.cli.warn("⚠️  Skills are not enabled in config.");
    log.cli.info("Add 'skills' to your stackowl.config.json to enable them.");
    process.exit(1);
  }

  const registry = skillsLoader.getRegistry();

  // Handle ClawHub search
  if (opts.clawhubSearch) {
    const clawHub = new ClawHubClient();
    log.cli.info(`🔍 Searching ClawHub for "${opts.clawhubSearch}"...`);

    try {
      const results = await clawHub.search(opts.clawhubSearch, 10);
      log.cli.info(`Found ${results.total} skills:`);

      for (const skill of results.skills) {
        log.cli.info(`📦 ${skill.name}`);
        log.cli.info(`   ${skill.description}`);
        log.cli.info(`   ⭐ ${skill.stars} stars | 👇 ${skill.downloads} downloads | by ${skill.author}`);
        log.cli.info(`   Install: stackowl skills --install ${skill.slug}`);
        log.cli.info("");
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      log.cli.error(`ClawHub search failed: ${msg}`, error);
    }
    return;
  }

  // Handle ClawHub install
  if (opts.install) {
    const source = parseInstallSource(opts.install);
    const targetDir = config.skills?.directories?.[0] ?? "./workspace/skills";
    const workspaceRoot = resolve(targetDir, "../..");

    if (source.type === "github") {
      const installer = new SkillInstaller(workspaceRoot);
      log.cli.info(`Installing "${source.skillName}" from GitHub...`);
      try {
        await installer.fromGitHub(source.rawUrl, source.skillName);
        log.cli.info(`✓ Installed ${source.skillName}`);
        log.cli.info("Reload skills: restart the assistant");
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.cli.error(`GitHub install failed: ${msg}`, error);
      }
    } else if (source.type === "local") {
      const installer = new SkillInstaller(workspaceRoot);
      log.cli.info(`Installing "${source.skillName}" from local path...`);
      try {
        await installer.fromLocal(source.localPath);
        log.cli.info(`✓ Installed ${source.skillName}`);
        log.cli.info("Reload skills: restart the assistant");
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.cli.error(`Local install failed: ${msg}`, error);
      }
    } else {
      const clawHub = new ClawHubClient();
      log.cli.info(`Installing "${source.slug}" from ClawHub...`);
      try {
        await clawHub.install(source.slug, targetDir);
        log.cli.info("✓ Successfully installed!");
        log.cli.info("Reload skills: restart the assistant");
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.cli.error(`Installation failed: ${msg}`, error);
      }
    }
    return;
  }

  if (opts.read) {
    const skill = registry.get(opts.read);
    if (!skill) {
      log.cli.error(`❌ Skill "${opts.read}" not found.`);
      process.exit(1);
    }

    log.cli.info(`🎯 SKILL: ${skill.name}`);
    log.cli.info(`Description: ${skill.description}`);
    log.cli.info(`Source: ${skill.sourcePath}`);
    log.cli.info(`Enabled: ${skill.enabled ? "Yes" : "No"}`);

    if (skill.requiredEnv && skill.requiredEnv.length > 0) {
      log.cli.info(`Required env: ${skill.requiredEnv.join(", ")}`);
    }
    if (skill.requiredBins && skill.requiredBins.length > 0) {
      log.cli.info(`Required bins: ${skill.requiredBins.join(", ")}`);
    }

    log.cli.info("Instructions:");
    log.cli.info(skill.instructions);
    return;
  }

  // List or search skills
  let skills = opts.search
    ? skillsLoader.search(opts.search)
    : registry.listAll();

  if (opts.search) {
    log.cli.info(`🔍 Search results for "${opts.search}":`);
  } else if (opts.list || (!opts.search && !opts.read)) {
    log.cli.info("🎯 Loaded Skills:");
  }

  if (skills.length === 0) {
    log.cli.info("No skills found.");
    if (!config.skills.directories?.length) {
      log.cli.info("Configure 'skills.directories' in stackowl.config.json");
    }
    return;
  }

  for (const s of skills) {
    const emoji = s.metadata.openclaw?.emoji || "🎯";
    log.cli.info(`${emoji} ${s.name} ${s.enabled ? "" : "(disabled)"}`);
    log.cli.info(`   ${s.description}`);
    if (s.requiredEnv?.length || s.requiredBins?.length) {
      const reqs: string[] = [];
      if (s.requiredEnv?.length) reqs.push(`env: ${s.requiredEnv.join(", ")}`);
      if (s.requiredBins?.length)
        reqs.push(`bins: ${s.requiredBins.join(", ")}`);
      log.cli.info(`   ${reqs.join(" | ")}`);
    }
    log.cli.info("");
  }
}

// ─── Evolve Command ────────────────────────────────────────────────

async function evolveCommand(owlName: string) {
  const { evolutionEngine } = await bootstrap();

  if (!owlName) {
    log.cli.error("❌ Please provide an owl name to evolve.");
    process.exit(1);
  }

  try {
    const mutated = await evolutionEngine.evolve(owlName);
    if (!mutated) {
      log.cli.info(`🦤 No evolution triggered for ${owlName}. They didn't learn anything new.`);
    }
  } catch (error) {
    log.cli.error("Evolution failed:", error);
    process.exit(1);
  }
}

// ─── Pairing Command ─────────────────────────────────────────────

async function pairingCommand(channel: string, userId: string, code: string) {
  const { memoryDb } = await bootstrap();

  if (!channel || !userId || !code) {
    log.cli.error("❌ Usage: stackowl pairing approve <channel> <userId> <code>");
    log.cli.error("  Example: stackowl pairing approve discord user123 ABC123");
    process.exit(1);
  }

  const { PairingService } = await import("./gateway/security/pairing.js");
  const pairing = new PairingService(memoryDb.rawDb);

  const ok = pairing.approve(channel, userId, code);
  if (ok) {
    log.cli.info(`✓ Approved: ${userId} on ${channel}`);
  } else {
    log.cli.error(`✗ Failed: wrong code or unknown sender`);
    process.exit(1);
  }
}

// ─── Cron Commands ──────────────────────────────────────────────

async function cronListCommand() {
  const { CronService } = await import("./cron/service.js");
  const service = new CronService({ persist: true });

  const jobs = service.listJobs();
  if (jobs.length === 0) {
    log.cli.info("No scheduled jobs.");
    service.stop();
    return;
  }

  log.cli.info("");
  for (const job of jobs) {
    const state = service.getJobState(job.id);
    const nextRunStr = state?.nextRunAt
      ? new Date(state.nextRunAt).toISOString()
      : "unknown";
    const desc = job.description ? ` — ${job.description}` : "";
    log.cli.info(`${job.id} [${job.schedule}] — next: ${nextRunStr}${desc}`);
  }
  log.cli.info("");
  service.stop();
}

async function cronAddCommand(opts: {
  prompt?: string;
  schedule?: string;
  id?: string;
  safety?: string;
  deliver?: boolean;
}) {
  const { CronService } = await import("./cron/service.js");
  const { randomUUID } = await import("node:crypto");

  if (!opts.prompt || !opts.schedule) {
    log.cli.error(
      "❌ Usage: stackowl cron add --prompt <text> --schedule <cron-expr> [--id <id>] [--safety low|medium|full] [--deliver]",
    );
    process.exit(1);
  }

  const safety = (opts.safety || "low") as "low" | "medium" | "full";
  if (!["low", "medium", "full"].includes(safety)) {
    log.cli.error("❌ --safety must be one of: low, medium, full");
    process.exit(1);
  }

  const jobId = opts.id || randomUUID().substring(0, 8);
  const service = new CronService({ persist: true });

  try {
    const job = {
      id: jobId,
      prompt: opts.prompt,
      schedule: opts.schedule,
      safetyProfile: safety,
      deliver: opts.deliver ?? false,
    };
    service.addJob(job);
    const state = service.getJobState(jobId);
    const nextRunStr = state?.nextRunAt
      ? new Date(state.nextRunAt).toISOString()
      : "unknown";
    log.cli.info(`✓ Job "${jobId}" scheduled for ${opts.schedule}.`);
    log.cli.info(`  Next run: ${nextRunStr}`);
  } catch (err) {
    log.cli.error(`❌ Failed to add job: ${(err as Error).message}`);
    process.exit(1);
  } finally {
    service.stop();
  }
}

async function cronRemoveCommand(id: string) {
  const { CronService } = await import("./cron/service.js");

  if (!id) {
    log.cli.error("❌ Usage: stackowl cron remove <id>");
    process.exit(1);
  }

  const service = new CronService({ persist: true });

  try {
    const jobs = service.listJobs();
    const found = jobs.some((j) => j.id === id);
    if (!found) {
      log.cli.error(`❌ Job "${id}" not found.`);
      process.exit(1);
    }
    service.removeJob(id);
    log.cli.info(`✓ Job "${id}" removed.`);
  } catch (err) {
    log.cli.error(`❌ Failed to remove job: ${(err as Error).message}`);
    process.exit(1);
  } finally {
    service.stop();
  }
}

// ─── Telegram Command ────────────────────────────────────────────

async function telegramCommand(opts: { owl?: string; withCli?: boolean }) {
  const b = await bootstrap();

  const botToken = b.config.telegram?.botToken ?? "";

  if (!botToken) {
    log.cli.error("❌ Telegram bot token not found.");
    log.cli.info('  Run ./start.sh to configure, or set "telegram.botToken" in stackowl.config.json');
    process.exit(1);
  }

  const owl = opts.owl
    ? b.owlRegistry.get(opts.owl)
    : b.owlRegistry.getDefault();
  if (!owl) {
    log.cli.error(`❌ Owl "${opts.owl}" not found.`);
    process.exit(1);
  }

  const provider = b.providerRegistry.getDefault();
  if (!(await provider.healthCheck())) {
    log.cli.error(`❌ Cannot reach ${provider.name}. Is it running?`);
    process.exit(1);
  }

  log.cli.info(`✓ Provider: ${provider.name} (model: ${b.config.defaultModel})`);
  log.cli.info(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`);
  log.cli.info("✓ Channel: 📱 Telegram");

  const gateway = await buildGateway(b, owl);
  const adapter = new TelegramAdapter(gateway, {
    botToken,
    chatIdsPath: join(b.workspacePath, "known_chat_ids.json"),
  });
  gateway.register(adapter);

  const shutdown = async () => {
    log.cli.info("🦉 Shutting down...");
    adapter.stop();
    await b.browserPool?.shutdown();
    await b.puppeteerFetcher?.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  await adapter.start();

  if (opts.withCli) {
    log.cli.info("📱 Telegram running. CLI also active.");
    await chatCommand(opts.owl);
  }
}

// ─── Slack Command ───────────────────────────────────────────────

async function slackCommand(opts: { owl?: string; withCli?: boolean }) {
  const b = await bootstrap();

  const slackConfig = b.config.slack;
  if (!slackConfig?.botToken || !slackConfig?.appToken) {
    log.cli.error("❌ Slack credentials not found.");
    log.cli.info('  Set "slack.botToken" (xoxb-...) and "slack.appToken" (xapp-...) in stackowl.config.json');
    log.cli.info("  See: https://api.slack.com/start/quickstart");
    process.exit(1);
  }

  const owl = opts.owl
    ? b.owlRegistry.get(opts.owl)
    : b.owlRegistry.getDefault();
  if (!owl) {
    log.cli.error(`❌ Owl "${opts.owl}" not found.`);
    process.exit(1);
  }

  const provider = b.providerRegistry.getDefault();
  if (!(await provider.healthCheck())) {
    log.cli.error(`❌ Cannot reach ${provider.name}. Is it running?`);
    process.exit(1);
  }

  log.cli.info(`✓ Provider: ${provider.name} (model: ${b.config.defaultModel})`);
  log.cli.info(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`);
  log.cli.info("✓ Channel: 💬 Slack");

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

  const shutdown = async () => {
    log.cli.info("🦉 Shutting down...");
    adapter.stop();
    await b.browserPool?.shutdown();
    await b.puppeteerFetcher?.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  await adapter.start();

  if (opts.withCli) {
    log.cli.info("💬 Slack running. CLI also active.");
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
    log.cli.error(`❌ Cannot reach ${provider.name} provider. Is it running?`);
    process.exit(1);
  }

  const owl = owlName ? b.owlRegistry.get(owlName) : b.owlRegistry.getDefault();
  if (!owl) {
    log.cli.error(`❌ Owl "${owlName}" not found.`);
    process.exit(1);
  }

  log.cli.info(`✓ Provider: ${provider.name} (model: ${b.config.defaultModel})`);
  log.cli.info(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`);
  log.cli.info("✓ Channel: 🌐 WebSocket Control Plane");

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
    log.cli.error(`❌ Cannot reach ${provider.name} provider. Is it running?`);
    process.exit(1);
  }

  const owl = opts.owl
    ? b.owlRegistry.get(opts.owl)
    : b.owlRegistry.getDefault();
  if (!owl) {
    log.cli.error(`❌ Owl "${opts.owl}" not found.`);
    process.exit(1);
  }

  log.cli.info(`✓ Provider: ${provider.name} (model: ${b.config.defaultModel})`);
  log.cli.info(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`);

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
  log.cli.info(`✓ Channel: 🌐 WebSocket Control Plane (port ${resolvedPort})`);

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
      log.cli.info("✓ Channel: 💬 Slack");
    } catch (err) {
      log.cli.error(
        `✗ Slack failed to start: ${err instanceof Error ? err.message : err}`,
        err,
      );
    }
  }

  // 3b. Check for Discord
  if (b.config.discord?.botToken) {
    try {
      const discordAdapter = new DiscordAdapter({
        botToken: b.config.discord.botToken,
        guildIds: b.config.discord.guildIds,
        dmPolicy: b.config.discord.dmPolicy ?? "pairing",
      });
      gateway.register(discordAdapter);
      await discordAdapter.start(gateway);
      log.cli.info("✓ Channel: Discord");
    } catch (err) {
      log.cli.error(
        `✗ Discord failed to start: ${err instanceof Error ? err.message : err}`,
        err,
      );
    }
  }

  // 3c. Check for WhatsApp
  if (b.config.whatsapp?.enabled) {
    try {
      const whatsappAdapter = new WhatsAppAdapter({
        sessionDataPath: b.config.whatsapp.sessionDataPath,
        dmPolicy: b.config.whatsapp.dmPolicy ?? "pairing",
      });
      gateway.register(whatsappAdapter);
      await whatsappAdapter.start(gateway);
      log.cli.info("✓ Channel: WhatsApp");
    } catch (err) {
      log.cli.error(
        `✗ WhatsApp failed to start: ${err instanceof Error ? err.message : err}`,
        err,
      );
    }
  }

  // 4. Check for Telegram
  // Note: grammY's bot.start() blocks forever (long-polling), so we start it
  // without await. The onStart callback confirms it's running.
  let pendingTelegramAdapter: TelegramAdapter | null = null;
  if (b.config.telegram?.botToken) {
    const telegramAdapter = new TelegramAdapter(gateway, {
      botToken: b.config.telegram.botToken,
      allowedUserIds: b.config.telegram.allowedUserIds,
      chatIdsPath: join(b.workspacePath, "known_chat_ids.json"),
    });
    gateway.register(telegramAdapter);
    pendingTelegramAdapter = telegramAdapter;
    telegramAdapter.start().catch((err) => {
      log.cli.error(
        `✗ Telegram failed: ${err instanceof Error ? err.message : err}`,
        err,
      );
    });
    log.cli.info("✓ Channel: 📱 Telegram");
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
    log.cli.info("✓ Agent Watch: http://localhost:3111/agent-watch");
  }

  // 5. Start CLI adapter
  const cliAdapter = new CLIAdapter(gateway, { workspacePath: b.workspacePath });
  gateway.register(cliAdapter);

  // Element 12 — Task 6.5: share Telegram's ProactivePinger with CLI so user
  // replies on either channel record engagement signals. Pinger is constructed
  // inside Telegram's async `start()`, so we poll briefly to catch it once
  // initialization completes.
  if (pendingTelegramAdapter) {
    const tgRef = pendingTelegramAdapter;
    const tryAttachPinger = (attempt: number): void => {
      const pinger = tgRef.getPinger();
      if (pinger) {
        cliAdapter.setPinger(pinger);
        return;
      }
      if (attempt < 50) setTimeout(() => tryAttachPinger(attempt + 1), 200);
    };
    tryAttachPinger(0);
  }

  const shutdown = async () => {
    log.cli.info("🦉 Shutting down all channels...");
    cliAdapter.stop();
    await b.browserPool?.shutdown();
    await b.puppeteerFetcher?.close();
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
  .version("0.1.1");

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
      log.cli.error(`Fatal error: ${err.message}`, err);
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
      log.cli.error(`Fatal error: ${err.message}`, err);
      process.exit(1);
    });
  });

program
  .command("evolve <owlName>")
  .description("Trigger a DNA evolution pass for a specific owl")
  .action((owlName) => {
    evolveCommand(owlName).catch((err) => {
      log.cli.error(`Fatal error: ${err.message}`, err);
      process.exit(1);
    });
  });

program
  .command("pairing <subcommand> [args...]")
  .description("Manage DM pairing approvals for Discord/WhatsApp")
  .action((subcommand: string, args: string[]) => {
    if (subcommand === "approve" && args.length >= 3) {
      const [channel, userId, code] = args;
      pairingCommand(channel, userId, code).catch((err) => {
        log.cli.error(`Fatal error: ${err.message}`, err);
        process.exit(1);
      });
    } else {
      log.cli.error("❌ Usage: stackowl pairing approve <channel> <userId> <code>");
      log.cli.error("  Example: stackowl pairing approve discord user123 ABC123");
      process.exit(1);
    }
  });

program
  .command("cron <subcommand> [args...]")
  .description("Manage scheduled background jobs")
  .option("--prompt <text>", "Job prompt (required for add)")
  .option("--schedule <cron>", "Cron expression (required for add)")
  .option("--id <id>", "Job ID (optional, auto-generated for add)")
  .option("--safety <level>", "Safety profile: low, medium, full (default: low)")
  .option("--deliver", "Enable delivery to channel (flag)")
  .action(
    (
      subcommand: string,
      args: string[],
      opts: {
        prompt?: string;
        schedule?: string;
        id?: string;
        safety?: string;
        deliver?: boolean;
      },
    ) => {
      if (subcommand === "list") {
        cronListCommand().catch((err) => {
          log.cli.error(`Fatal error: ${err.message}`, err);
          process.exit(1);
        });
      } else if (subcommand === "add") {
        cronAddCommand(opts).catch((err) => {
          log.cli.error(`Fatal error: ${err.message}`, err);
          process.exit(1);
        });
      } else if (subcommand === "remove" && args.length >= 1) {
        cronRemoveCommand(args[0]).catch((err) => {
          log.cli.error(`Fatal error: ${err.message}`, err);
          process.exit(1);
        });
      } else {
        log.cli.error("❌ Usage:");
        log.cli.error("  stackowl cron list");
        log.cli.error(
          "  stackowl cron add --prompt <text> --schedule <cron> [--id <id>] [--safety low|medium|full] [--deliver]",
        );
        log.cli.error("  stackowl cron remove <id>");
        process.exit(1);
      }
    },
  );

program
  .command("web")
  .description("Start the StackOwl WebSocket Control Plane server")
  .option("-p, --port <number>", "Port to listen on", "3000")
  .option("-o, --owl <name>", "Owl persona to use")
  .action((opts) => {
    webCommand(opts.port, opts.owl).catch((err) => {
      log.cli.error(`Fatal error: ${err.message}`, err);
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
        log.cli.error(`Fatal error: ${err.message}`, err);
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
      log.cli.error(`Fatal error: ${err.message}`, err);
      process.exit(1);
    });
  });

// Default to chat if no command given
program.action(async () => {
  await chatCommand();
});

program.parse();

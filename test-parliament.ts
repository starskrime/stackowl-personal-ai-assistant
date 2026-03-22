/**
 * Quick test: Send "Should I switch careers from engineering to product management?"
 * through the gateway and observe whether parliament triggers.
 */

import { resolve } from "node:path";
import chalk from "chalk";
import { loadConfig } from "./src/config/loader.js";
import { ProviderRegistry } from "./src/providers/registry.js";
import { OwlRegistry } from "./src/owls/registry.js";
import { ToolRegistry } from "./src/tools/registry.js";
import { ShellTool } from "./src/tools/shell.js";
import { ReadFileTool, WriteFileTool, EditFileTool } from "./src/tools/files.js";
import { GoogleSearchTool } from "./src/tools/search.js";
import { WebCrawlTool } from "./src/tools/web.js";
import { SessionStore } from "./src/memory/store.js";
import { PelletStore } from "./src/pellets/store.js";
import { OwlEvolutionEngine } from "./src/owls/evolution.js";
import { ToolSynthesizer } from "./src/evolution/synthesizer.js";
import { CapabilityLedger } from "./src/evolution/ledger.js";
import { DynamicToolLoader } from "./src/evolution/loader.js";
import { EvolutionHandler } from "./src/evolution/handler.js";
import { OwlGateway, makeSessionId, makeMessageId } from "./src/gateway/core.js";
import { MemoryConsolidator } from "./src/memory/consolidator.js";
import type { GatewayCallbacks } from "./src/gateway/types.js";
import type { StreamEvent } from "./src/providers/base.js";

async function main() {
  console.log(chalk.bold.cyan("\n🏛️ Parliament Detection Test\n"));

  const basePath = process.cwd();
  const config = await loadConfig(basePath);
  const workspacePath = resolve(basePath, config.workspace);

  // Provider
  const providerRegistry = new ProviderRegistry();
  for (const [name, providerConf] of Object.entries(config.providers)) {
    providerRegistry.register({ name, ...providerConf });
  }
  providerRegistry.setDefault(config.defaultProvider);
  const provider = providerRegistry.getDefault();

  const healthy = await provider.healthCheck();
  if (!healthy) {
    console.error(chalk.red("❌ Provider not reachable."));
    process.exit(1);
  }
  console.log(chalk.green(`✓ Provider: ${provider.name}`));

  // Owls
  const owlRegistry = new OwlRegistry(workspacePath);
  await owlRegistry.loadAll();
  const owl = owlRegistry.getDefault()!;
  console.log(chalk.green(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`));
  console.log(chalk.green(`✓ Owls available: ${owlRegistry.listOwls().map(o => o.persona.name).join(', ')}`));

  // Tools
  const toolRegistry = new ToolRegistry();
  const { WebSearchTool } = await import("./src/compat/index.js");
  const { BrowserTool } = await import("./src/compat/tools/browser.js");
  toolRegistry.registerAll([
    ShellTool, ReadFileTool, WriteFileTool, EditFileTool,
    GoogleSearchTool, WebCrawlTool,
    new WebSearchTool("brave", process.env.BRAVE_API_KEY || process.env.WEB_SEARCH_API_KEY),
    new BrowserTool(workspacePath),
  ]);
  console.log(chalk.green(`✓ Tools: ${toolRegistry.listAll().length} registered`));

  // Sessions + Pellets
  const sessionStore = new SessionStore(workspacePath);
  await sessionStore.init();
  const pelletStore = new PelletStore(workspacePath, provider, config.pellets?.dedup);
  await pelletStore.init();

  // Evolution
  const synthesizer = new ToolSynthesizer();
  const ledger = new CapabilityLedger();
  const loader = new DynamicToolLoader(ledger);
  const evolution = new EvolutionHandler(synthesizer, ledger, loader);
  const evolutionEngine = new OwlEvolutionEngine(provider, config, sessionStore, owlRegistry);

  // Memory
  const memoryContext = await MemoryConsolidator.loadMemory(workspacePath);

  // Gateway
  const gateway = new OwlGateway({
    provider,
    owl,
    owlRegistry,
    config,
    toolRegistry,
    sessionStore,
    pelletStore,
    capabilityLedger: ledger,
    evolution,
    evolutionEngine,
    memoryContext,
    cwd: workspacePath,
    providerRegistry,
  });

  console.log(chalk.green(`✓ Gateway initialized\n`));

  // ─── Send the test message ──────────────────────────────────
  const testMessage = "Should I switch careers from engineering to product management?";
  console.log(chalk.bold(`📩 Sending: "${testMessage}"\n`));
  console.log(chalk.dim("─".repeat(70)));

  const progressMessages: string[] = [];
  const callbacks: GatewayCallbacks = {
    onProgress: async (msg: string) => {
      progressMessages.push(msg);
      console.log(chalk.yellow(`  [progress] ${msg}`));
    },
    onStreamEvent: async (_event: StreamEvent) => {},
    onSendFile: async (path: string, caption?: string) => {
      console.log(chalk.blue(`  [file] ${path} ${caption || ''}`));
    },
  };

  const sessionId = makeSessionId("test", "parliament-test");
  const messageId = makeMessageId();

  const start = Date.now();
  try {
    const response = await gateway.handle(
      {
        text: testMessage,
        channelId: "test",
        userId: "parliament-tester",
        sessionId,
        messageId,
      },
      callbacks,
    );

    const elapsed = Date.now() - start;
    console.log(chalk.dim("─".repeat(70)));
    console.log(chalk.bold(`\n📤 Response (${elapsed}ms):`));
    console.log(chalk.cyan(`  Owl: ${response.owlEmoji} ${response.owlName}`));
    console.log(chalk.cyan(`  Tools used: ${response.toolsUsed?.join(', ') || 'none'}`));

    // Check if parliament was triggered
    const isParliament =
      response.toolsUsed?.includes('summon_parliament') ||
      response.content.includes('Parliament') ||
      response.content.includes('🏛️') ||
      progressMessages.some(m => m.includes('Parliament') || m.includes('🏛️'));

    console.log(chalk.cyan(`  Parliament triggered: ${isParliament ? '✅ YES' : '❌ NO'}`));
    console.log(chalk.cyan(`  Progress events: ${progressMessages.length}`));

    // Print response content (truncated)
    console.log(chalk.bold(`\n📝 Content (first 1000 chars):`));
    console.log(response.content.slice(0, 1000));
    if (response.content.length > 1000) {
      console.log(chalk.dim(`... (${response.content.length} chars total)`));
    }
  } catch (err) {
    console.error(chalk.red(`\n❌ Error: ${err instanceof Error ? err.message : String(err)}`));
    if (err instanceof Error && err.stack) {
      console.error(chalk.dim(err.stack));
    }
  }

  process.exit(0);
}

main().catch((err) => {
  console.error(chalk.red(`Fatal: ${err.message}`));
  process.exit(1);
});

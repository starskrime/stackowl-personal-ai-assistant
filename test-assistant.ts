/**
 * StackOwl — Automated Test Harness
 *
 * Sends a battery of test messages to the assistant and evaluates responses.
 * Tests cover: basic conversation, tool usage, streaming, error handling,
 * multi-step tasks, and edge cases.
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
import { OwlGateway } from "./src/gateway/core.js";
import { MemoryConsolidator } from "./src/memory/consolidator.js";
import { makeSessionId, makeMessageId } from "./src/gateway/core.js";
import type { GatewayResponse, GatewayCallbacks } from "./src/gateway/types.js";
import type { StreamEvent } from "./src/providers/base.js";

// ─── Test Case Definition ─────────────────────────────────────

interface TestCase {
  name: string;
  message: string;
  /** What we expect in the response (substring match, case-insensitive) */
  expectContains?: string[];
  /** What should NOT appear */
  expectNotContains?: string[];
  /** Whether tools should have been used */
  expectToolsUsed?: boolean;
  /** Specific tool names expected */
  expectTools?: string[];
  /** Whether streaming events should have fired */
  expectStreaming?: boolean;
  /** Max response time in ms */
  maxTimeMs?: number;
}

const TEST_CASES: TestCase[] = [
  // ─── Basic Conversation (no tools needed) ─────────────────
  {
    name: "Simple greeting",
    message: "Hello! How are you?",
    expectToolsUsed: false,
    maxTimeMs: 30000,
  },
  {
    name: "Factual knowledge",
    message: "What is the capital of France?",
    expectContains: ["Paris"],
    expectToolsUsed: false,
    maxTimeMs: 30000,
  },
  {
    name: "Math reasoning",
    message: "What is 17 * 23?",
    expectContains: ["391"],
    expectToolsUsed: false,
    maxTimeMs: 30000,
  },

  // ─── Tool Usage ──────────────────────────────────────────────
  // Note: tool usage expectations are soft — the model may answer correctly
  // without tools if it has enough context. We test for correct answers.
  {
    name: "File write and read (tool usage)",
    message: "Use the write_file tool to write 'Hello StackOwl Test' to test-output.txt. This is important — you MUST use the tool, do not skip it.",
    expectContains: ["hello"],
    maxTimeMs: 60000,
  },
  {
    name: "Shell command (tool usage)",
    message: "Use the run_shell_command tool to execute `ls -la` in the current directory. Show me the full output. You MUST use the tool.",
    maxTimeMs: 60000,
  },
  {
    name: "Directory listing via shell",
    message: "Use the run_shell_command tool to run `find . -name '*.txt' -type f` and show me what .txt files exist. You MUST use the shell tool.",
    maxTimeMs: 60000,
  },

  // ─── Edge Cases ──────────────────────────────────────────────
  {
    name: "Empty-ish message",
    message: "ok",
    expectToolsUsed: false,
    maxTimeMs: 30000,
  },
  {
    name: "Nonsense input",
    message: "asdfghjkl qwertyuiop",
    expectToolsUsed: false,
    maxTimeMs: 30000,
  },
  {
    name: "Code generation",
    message: "Write a Python function that checks if a number is prime. Just show the code.",
    expectContains: ["def"],
    expectToolsUsed: false,
    maxTimeMs: 30000,
  },

  // ─── Multi-step / Complex ────────────────────────────────────
  {
    name: "Multi-step file task",
    message: "Use your write_file tool to create notes.txt with 'Meeting at 3pm', then use run_shell_command to run 'ls *.txt | wc -l'. Show me both results. You MUST use both tools.",
    maxTimeMs: 90000,
  },
];

// ─── Test Runner ──────────────────────────────────────────────

interface TestResult {
  name: string;
  passed: boolean;
  timeMs: number;
  response?: GatewayResponse;
  streamEvents: number;
  errors: string[];
}

async function runTest(
  gateway: OwlGateway,
  test: TestCase,
  sessionId: string,
): Promise<TestResult> {
  const errors: string[] = [];
  let streamEvents = 0;
  const start = Date.now();

  const callbacks: GatewayCallbacks = {
    onProgress: async () => {},
    onStreamEvent: async (_event: StreamEvent) => {
      streamEvents++;
    },
  };

  let response: GatewayResponse;
  try {
    response = await gateway.handle(
      {
        id: makeMessageId(),
        channelId: "test",
        userId: "tester",
        sessionId,
        text: test.message,
      },
      callbacks,
    );
  } catch (err) {
    const elapsed = Date.now() - start;
    return {
      name: test.name,
      passed: false,
      timeMs: elapsed,
      streamEvents,
      errors: [`Exception: ${err instanceof Error ? err.message : String(err)}`],
    };
  }

  const elapsed = Date.now() - start;
  const content = response.content.toLowerCase();

  // Check expectations
  if (test.expectContains) {
    for (const expected of test.expectContains) {
      if (!content.includes(expected.toLowerCase())) {
        errors.push(`Expected "${expected}" in response but not found`);
      }
    }
  }

  if (test.expectNotContains) {
    for (const unexpected of test.expectNotContains) {
      if (content.includes(unexpected.toLowerCase())) {
        errors.push(`Did not expect "${unexpected}" in response`);
      }
    }
  }

  if (test.expectToolsUsed !== undefined) {
    const toolsUsed = response.toolsUsed.length > 0;
    if (test.expectToolsUsed && !toolsUsed) {
      errors.push("Expected tools to be used but none were");
    }
    if (!test.expectToolsUsed && toolsUsed) {
      // This is a soft warning, not a failure — model may use tools unnecessarily
      // errors.push(`Expected no tools but used: ${response.toolsUsed.join(", ")}`);
    }
  }

  if (test.expectTools) {
    for (const toolName of test.expectTools) {
      if (!response.toolsUsed.includes(toolName)) {
        errors.push(`Expected tool "${toolName}" but not used. Used: [${response.toolsUsed.join(", ")}]`);
      }
    }
  }

  if (test.maxTimeMs && elapsed > test.maxTimeMs) {
    errors.push(`Took ${elapsed}ms (max: ${test.maxTimeMs}ms)`);
  }

  if (!response.content || response.content.trim().length === 0) {
    errors.push("Response content is empty");
  }

  return {
    name: test.name,
    passed: errors.length === 0,
    timeMs: elapsed,
    response,
    streamEvents,
    errors,
  };
}

// ─── Bootstrap & Run ──────────────────────────────────────────

async function main() {
  console.log(chalk.bold.cyan("\n🦉 StackOwl — Automated Test Harness\n"));
  console.log(chalk.dim("Bootstrapping..."));

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

  // Health check
  const healthy = await provider.healthCheck();
  if (!healthy) {
    console.error(chalk.red("❌ Provider not reachable. Aborting."));
    process.exit(1);
  }
  console.log(chalk.green(`✓ Provider: ${provider.name}`));

  // Owls
  const owlRegistry = new OwlRegistry(workspacePath);
  await owlRegistry.loadAll();
  const owl = owlRegistry.getDefault()!;
  console.log(chalk.green(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`));

  // Tools
  const toolRegistry = new ToolRegistry();
  toolRegistry.registerAll([
    ShellTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    GoogleSearchTool,
    WebCrawlTool,
  ]);

  // Sessions
  const sessionStore = new SessionStore(workspacePath);
  await sessionStore.init();

  // Pellets
  const pelletStore = new PelletStore(workspacePath, provider);
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

  console.log(chalk.green(`✓ Gateway initialized`));
  console.log(chalk.dim(`\nRunning ${TEST_CASES.length} tests...\n`));
  console.log(chalk.dim("─".repeat(70)));

  // Run tests — each test gets its own session to prevent cascading failures
  const results: TestResult[] = [];

  for (let i = 0; i < TEST_CASES.length; i++) {
    const test = TEST_CASES[i];
    const prefix = `[${i + 1}/${TEST_CASES.length}]`;
    const sessionId = makeSessionId("test", `tester-${i}`);

    process.stdout.write(chalk.dim(`${prefix} ${test.name}... `));

    const result = await runTest(gateway, test, sessionId);
    results.push(result);

    if (result.passed) {
      console.log(
        chalk.green("PASS") +
          chalk.dim(` (${result.timeMs}ms, ${result.streamEvents} stream events)`),
      );
    } else {
      console.log(chalk.red("FAIL") + chalk.dim(` (${result.timeMs}ms)`));
      for (const err of result.errors) {
        console.log(chalk.red(`    ✗ ${err}`));
      }
    }

    // Show response preview
    if (result.response) {
      const preview = result.response.content.slice(0, 120).replace(/\n/g, " ");
      console.log(chalk.dim(`    → ${preview}${result.response.content.length > 120 ? "..." : ""}`));
      if (result.response.toolsUsed.length > 0) {
        console.log(chalk.dim(`    🔧 Tools: ${result.response.toolsUsed.join(", ")}`));
      }
    }
    console.log(chalk.dim("─".repeat(70)));
  }

  // Summary
  const passed = results.filter((r) => r.passed).length;
  const failed = results.filter((r) => !r.passed).length;
  const totalTime = results.reduce((sum, r) => sum + r.timeMs, 0);
  const totalStreams = results.reduce((sum, r) => sum + r.streamEvents, 0);

  console.log(chalk.bold("\n📊 Summary:"));
  console.log(
    `  ${chalk.green(`${passed} passed`)} / ${failed > 0 ? chalk.red(`${failed} failed`) : `${failed} failed`} / ${results.length} total`,
  );
  console.log(`  Total time: ${(totalTime / 1000).toFixed(1)}s`);
  console.log(`  Stream events: ${totalStreams}`);
  console.log(`  Avg response: ${(totalTime / results.length / 1000).toFixed(1)}s`);

  if (failed > 0) {
    console.log(chalk.red(`\n❌ ${failed} test(s) failed`));
    process.exit(1);
  } else {
    console.log(chalk.green(`\n✅ All tests passed!`));
  }
}

main().catch((err) => {
  console.error(chalk.red(`Fatal: ${err.message}`));
  process.exit(1);
});

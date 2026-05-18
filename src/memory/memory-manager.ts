import { Worker } from "node:worker_threads";
import { join } from "node:path";
import { existsSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { log } from "../logger.js";
import type { ProviderRegistry } from "../providers/registry.js";
import type { StackOwlConfig } from "../config/loader.js";
import { FactExtractor } from "./fact-extractor.js";
import type {
  Fact,
  ChatMessage,
  MainToMemory,
  MemoryToMain,
  DreamToMemory,
  MemoryToDream,
} from "./fact-schema.js";

// ─── Extended Dream IPC (relayed through manager) ─────────────────

type DreamToManagerMsg =
  | DreamToMemory
  | { type: "dream:llm-call"; prompt: string; requestId: string }
  | { type: "dream:done"; reflectedCount: number };

type ManagerToDreamMsg =
  | MemoryToDream
  | { type: "dream:llm-result"; requestId: string; content: string }
  | { type: "dream:llm-error"; requestId: string; message: string };

// ─── MemoryManager ────────────────────────────────────────────────

export class MemoryManager {
  private memoryWorker: Worker | null = null;
  private dreamWorker: Worker | null = null;
  private idleTimer: NodeJS.Timeout | null = null;
  private extractor: FactExtractor;

  private readonly workspacePath: string;
  private readonly ggufPath: string;
  private readonly compactionThreshold: number;
  private readonly topK: number;
  private readonly dreamCron: string;

  // In-flight search callbacks keyed by requestId
  private pendingSearches = new Map<string, (facts: Fact[]) => void>();

  constructor(
    config: StackOwlConfig,
    private providerRegistry: ProviderRegistry,
    workspacePath: string,
  ) {
    this.workspacePath = workspacePath;
    this.ggufPath = join(workspacePath, config.memory?.ggufPath ?? "models/bge-small-en-v1.5-q8_0.gguf");
    this.compactionThreshold = config.memory?.compactionThreshold ?? 50_000;
    this.topK = config.memory?.topK ?? 5;
    this.dreamCron = config.memory?.dreamCron ?? "0 3 * * *";
    this.extractor = new FactExtractor(providerRegistry);
  }

  // ─── Lifecycle ─────────────────────────────────────────────────

  async start(): Promise<void> {
    log.engine.info("[MemoryManager] start: entry", {
      ggufPath: this.ggufPath,
      compactionThreshold: this.compactionThreshold,
    });

    if (!existsSync(this.ggufPath)) {
      throw new Error(
        `[MemoryManager] GGUF model not found at "${this.ggufPath}". ` +
        `Commit the model file to the repo via git LFS before starting.`,
      );
    }

    this._spawnMemoryWorker();
    this._spawnDreamWorker();
    log.engine.info("[MemoryManager] start: exit — workers spawned");
  }

  async stop(): Promise<void> {
    log.engine.info("[MemoryManager] stop: entry");
    this._clearIdleTimer();

    this.memoryWorker?.postMessage({ type: "shutdown" } satisfies MainToMemory);
    await this.memoryWorker?.terminate().catch(() => {});
    await this.dreamWorker?.terminate().catch(() => {});
    this.memoryWorker = null;
    this.dreamWorker = null;
    log.engine.info("[MemoryManager] stop: exit");
  }

  // ─── Public API ────────────────────────────────────────────────

  /** Called after every LLM response. Emits context:compact if threshold crossed. */
  onResponseUsage(
    promptTokens: number,
    sessionId: string,
    messages: ChatMessage[],
    owlName: string,
    userId: string,
  ): void {
    log.engine.debug("[MemoryManager] onResponseUsage", { promptTokens, sessionId });

    if (promptTokens > this.compactionThreshold) {
      log.engine.info("[MemoryManager] compaction threshold crossed — triggering extraction", {
        promptTokens,
        threshold: this.compactionThreshold,
        sessionId,
      });
      void this._runExtraction(sessionId, messages, owlName, userId);
    }
  }

  /** Called on every user message. Resets the 30-min idle timer. */
  onUserMessage(sessionId: string, messages: ChatMessage[], owlName: string, userId: string): void {
    this._resetIdleTimer(sessionId, messages, owlName, userId);
  }

  /** Semantic search — returns top-K facts for injection into system prompt. */
  async search(query: string): Promise<Fact[]> {
    if (!this.memoryWorker) return [];

    const requestId = randomUUID();
    return new Promise((resolve) => {
      this.pendingSearches.set(requestId, resolve);
      const msg: MainToMemory = { type: "search", query, topK: this.topK, requestId };
      this.memoryWorker!.postMessage(msg);

      // Timeout — return empty rather than hanging the user response
      setTimeout(() => {
        if (this.pendingSearches.has(requestId)) {
          this.pendingSearches.delete(requestId);
          log.engine.warn("[MemoryManager] search timeout", { requestId });
          resolve([]);
        }
      }, 3_000);
    });
  }

  /** Write a single fact through the MemoryWorker pipeline (embed → LanceDB → Kuzu → SQLite). */
  writeFact(fact: Fact): void {
    if (!this.memoryWorker) {
      log.engine.warn("[MemoryManager] writeFact: worker not running — fact dropped", {
        factType: (fact as any).type,
      });
      return;
    }
    const msg: DreamToMemory = { type: "write-fact", fact, requestId: randomUUID() };
    this.memoryWorker.postMessage(msg);
    log.engine.debug("[MemoryManager] writeFact: enqueued", { factId: (fact as any).fact_id });
  }

  // ─── Extraction ────────────────────────────────────────────────

  private async _runExtraction(
    sessionId: string,
    messages: ChatMessage[],
    owlName: string,
    userId: string,
  ): Promise<void> {
    log.engine.info("[MemoryManager] _runExtraction: entry", { sessionId });

    try {
      // FactExtractor runs in main thread (has provider registry access)
      const existing = await this._getExistingFacts(owlName, userId);
      const diff = await this.extractor.extract(messages, existing, sessionId, owlName, userId);

      log.engine.info("[MemoryManager] _runExtraction: extraction done", {
        sessionId,
        new: diff.new.length,
        updated: diff.updated.length,
      });

      // Write new facts via MemoryWorker (Actor Model)
      for (const fact of diff.new) {
        const msg: DreamToMemory = { type: "write-fact", fact, requestId: randomUUID() };
        this.memoryWorker?.postMessage(msg);
      }
    } catch (err) {
      log.engine.error("[MemoryManager] _runExtraction: failed", err as Error, { sessionId });
    }
  }

  private async _getExistingFacts(_owlName: string, _userId: string): Promise<Fact[]> {
    // Search for existing facts via MemoryWorker search (broad query)
    return this.search(`user preferences approaches project context owl behavior`);
  }

  // ─── Idle timer ────────────────────────────────────────────────

  private _resetIdleTimer(
    sessionId: string,
    messages: ChatMessage[],
    owlName: string,
    userId: string,
  ): void {
    this._clearIdleTimer();
    this.idleTimer = setTimeout(
      () => {
        log.engine.info("[MemoryManager] idle timer fired — triggering extraction", { sessionId });
        void this._runExtraction(sessionId, messages, owlName, userId);
      },
      30 * 60 * 1_000,
    );
  }

  private _clearIdleTimer(): void {
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }

  // ─── Worker spawn + crash recovery ─────────────────────────────

  private _spawnMemoryWorker(): void {
    const workerPath = new URL("../../dist/memory/memory.worker.js", import.meta.url);
    log.engine.info("[MemoryManager] spawning MemoryWorker", { path: workerPath.pathname });

    const worker = new Worker(workerPath, {
      workerData: {
        workspacePath: this.workspacePath,
        ggufPath: this.ggufPath,
      },
    });

    this.memoryWorker = worker;

    worker.on("message", (raw: unknown) => {
      const msg = raw as MemoryToMain | { _dest?: string; type: string; requestId?: string; success?: boolean };

      // ack from DreamWorker write relay
      if ("_dest" in msg && msg._dest === "dream") {
        const ackMsg = msg as unknown as MemoryToDream & { _dest: string };
        this.dreamWorker?.postMessage({ type: ackMsg.type, requestId: ackMsg.requestId, success: (ackMsg as any).success });
        return;
      }

      const m = msg as MemoryToMain;
      if (m.type === "search-result") {
        const cb = this.pendingSearches.get(m.requestId);
        if (cb) {
          this.pendingSearches.delete(m.requestId);
          cb(m.facts);
        }
      } else if (m.type === "error") {
        log.engine.error("[MemoryManager] MemoryWorker reported error", undefined, { message: m.message });
      }
    });

    worker.on("error", (err) => {
      log.engine.error("[MemoryManager] MemoryWorker crashed", err);
      this._spawnMemoryWorker();
    });

    worker.on("exit", (code) => {
      if (code !== 0) {
        log.engine.warn("[MemoryManager] MemoryWorker exited unexpectedly", { code });
        this._spawnMemoryWorker();
      }
    });
  }

  private _spawnDreamWorker(): void {
    const workerPath = new URL("../../dist/memory/dream.worker.js", import.meta.url);
    log.engine.info("[MemoryManager] spawning DreamWorker", { path: workerPath.pathname });

    const worker = new Worker(workerPath, {
      workerData: {
        workspacePath: this.workspacePath,
        ggufPath: this.ggufPath,
        dreamCron: this.dreamCron,
      },
    });

    this.dreamWorker = worker;

    worker.on("message", async (raw: unknown) => {
      const msg = raw as DreamToManagerMsg;

      // Write relay: DreamWorker → MemoryWorker
      if (msg.type === "write-fact" || msg.type === "delete-fact") {
        this.memoryWorker?.postMessage(msg);
        return;
      }

      // LLM relay: DreamWorker → synthesizer provider → DreamWorker
      if (msg.type === "dream:llm-call") {
        await this._relayLlmCall(msg.requestId, msg.prompt);
        return;
      }

      if (msg.type === "dream:done") {
        log.engine.info("[MemoryManager] DreamWorker cycle complete", { reflectedCount: msg.reflectedCount });
      }
    });

    worker.on("error", (err) => {
      log.engine.error("[MemoryManager] DreamWorker crashed", err);
      this._spawnDreamWorker();
    });

    worker.on("exit", (code) => {
      if (code !== 0) {
        log.engine.warn("[MemoryManager] DreamWorker exited unexpectedly", { code });
        this._spawnDreamWorker();
      }
    });
  }

  // ─── LLM relay for DreamWorker ─────────────────────────────────

  private async _relayLlmCall(requestId: string, prompt: string): Promise<void> {
    log.engine.debug("[MemoryManager] relayLlmCall: entry", { requestId });

    try {
      const provider = this.providerRegistry.byRole("synthesizer");
      const response = await provider.chat(
        [
          { role: "system", content: "You are a reflective AI assistant performing self-improvement analysis." },
          { role: "user", content: prompt },
        ],
        undefined,
        { temperature: 0.3 },
      );

      const reply: ManagerToDreamMsg = {
        type: "dream:llm-result",
        requestId,
        content: response.content,
      };
      this.dreamWorker?.postMessage(reply);
      log.engine.debug("[MemoryManager] relayLlmCall: exit", { requestId });
    } catch (err) {
      log.engine.error("[MemoryManager] relayLlmCall: failed", err as Error, { requestId });
      const reply: ManagerToDreamMsg = {
        type: "dream:llm-error",
        requestId,
        message: err instanceof Error ? err.message : String(err),
      };
      this.dreamWorker?.postMessage(reply);
    }
  }
}

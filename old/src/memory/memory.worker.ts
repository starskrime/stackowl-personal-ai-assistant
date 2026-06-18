/**
 * MemoryWorker — off-main-thread fact extraction + semantic search
 *
 * Triggered by context:compact and context:idle events.
 * Sole writer to LanceDB and Kuzu (Actor Model).
 * DreamWorker sends write requests here via MessageChannel.
 */

import { parentPort, workerData, isMainThread } from "node:worker_threads";
import { existsSync } from "node:fs";
import { log } from "../logger.js";
import { MemoryStore } from "./memory-store.js";
import type { MainToMemory, MemoryToMain, DreamToMemory, MemoryToDream } from "./fact-schema.js";

if (isMainThread) {
  throw new Error("[MemoryWorker] Must be spawned as a worker_thread, not run directly");
}

// ─── Worker config passed via workerData ─────────────────────────

interface WorkerData {
  workspacePath: string;
  ggufPath: string;
  synthesizerId: string; // provider registry path — not used in worker; extractor uses registry
}

const { workspacePath, ggufPath } = workerData as WorkerData;

// ─── State ───────────────────────────────────────────────────────

let store: MemoryStore;
let llama: Awaited<ReturnType<typeof import("node-llama-cpp").getLlama>>;
let model: Awaited<ReturnType<typeof llama.loadModel>>;
let embeddingCtx: Awaited<ReturnType<typeof model.createEmbeddingContext>>;
let ready = false;

// ─── Boot ────────────────────────────────────────────────────────

async function boot(): Promise<void> {
  log.engine.info("[MemoryWorker] boot: entry", { workspacePath, ggufPath });

  if (!existsSync(ggufPath)) {
    throw new Error(
      `[MemoryWorker] GGUF model not found at "${ggufPath}". ` +
      `Commit the model file to the repo via git LFS before starting.`,
    );
  }

  // Load GGUF inside worker — never shared across threads
  const { getLlama } = await import("node-llama-cpp");
  llama = await getLlama();
  model = await llama.loadModel({ modelPath: ggufPath });
  embeddingCtx = await model.createEmbeddingContext();
  log.engine.info("[MemoryWorker] boot: embedding model loaded");

  store = new MemoryStore(workspacePath);
  await store.init();

  ready = true;
  log.engine.info("[MemoryWorker] boot: exit — ready");
  send({ type: "extract-done", factCount: 0 }); // signal readiness to main
}

// ─── Embed ───────────────────────────────────────────────────────

async function embed(text: string): Promise<number[]> {
  const result = await embeddingCtx.getEmbeddingFor(text);
  return Array.from(result.vector);
}

// ─── Message handlers ─────────────────────────────────────────────

async function handleExtract(msg: Extract<MainToMemory, { type: "extract" }>): Promise<void> {
  log.engine.info("[MemoryWorker] extract: entry", {
    sessionId: msg.sessionId,
    messageCount: msg.messages.length,
  });

  try {
    const existing = await store.getExisting(msg.owlName, msg.userId);

    // FactExtractor needs provider registry — but workers don't have it.
    // We receive the pre-serialized extractor result from main; this path
    // is only reached if main sends the raw messages. For now, extraction
    // is handled in main thread's FactExtractor, which posts results here
    // as write-fact messages. This handler is reserved for future direct extraction.
    log.engine.debug("[MemoryWorker] extract: existing facts", { count: existing.length });

    send({ type: "extract-done", factCount: 0 });
  } catch (err) {
    log.engine.error("[MemoryWorker] extract: failed", err as Error, { sessionId: msg.sessionId });
    send({ type: "error", message: err instanceof Error ? err.message : String(err) });
  }
}

async function handleSearch(msg: Extract<MainToMemory, { type: "search" }>): Promise<void> {
  log.engine.debug("[MemoryWorker] search: entry", { requestId: msg.requestId, topK: msg.topK });

  try {
    const queryVector = await embed(msg.query);
    const facts = await store.search(queryVector, msg.topK);
    send({ type: "search-result", requestId: msg.requestId, facts });
    log.engine.debug("[MemoryWorker] search: exit", { requestId: msg.requestId, found: facts.length });
  } catch (err) {
    log.engine.error("[MemoryWorker] search: failed", err as Error, { requestId: msg.requestId });
    send({ type: "error", message: err instanceof Error ? err.message : String(err) });
  }
}

async function handleWriteFact(msg: Extract<DreamToMemory, { type: "write-fact" }>): Promise<void> {
  log.engine.debug("[MemoryWorker] write-fact: entry", { requestId: msg.requestId, factId: msg.fact.factId });

  try {
    const vector = await embed(msg.fact.content);
    await store.upsert(msg.fact, vector);
    sendToDream({ type: "write-ack", requestId: msg.requestId, success: true });
    log.engine.debug("[MemoryWorker] write-fact: exit", { requestId: msg.requestId });
  } catch (err) {
    log.engine.error("[MemoryWorker] write-fact: failed", err as Error, { requestId: msg.requestId });
    sendToDream({ type: "write-ack", requestId: msg.requestId, success: false });
  }
}

async function handleDeleteFact(msg: Extract<DreamToMemory, { type: "delete-fact" }>): Promise<void> {
  log.engine.debug("[MemoryWorker] delete-fact: entry", { requestId: msg.requestId, factId: msg.factId });

  try {
    await store.delete(msg.factId);
    sendToDream({ type: "write-ack", requestId: msg.requestId, success: true });
    log.engine.debug("[MemoryWorker] delete-fact: exit", { requestId: msg.requestId });
  } catch (err) {
    log.engine.error("[MemoryWorker] delete-fact: failed", err as Error, { requestId: msg.requestId });
    sendToDream({ type: "write-ack", requestId: msg.requestId, success: false });
  }
}

// ─── Write a fact directly (called when FactExtractor runs in main) ─

export async function writeFact(
  content: string,
  fact: import("./fact-schema.js").Fact,
): Promise<void> {
  const vector = await embed(content);
  await store.upsert(fact, vector);
}

// ─── IPC ─────────────────────────────────────────────────────────

function send(msg: MemoryToMain): void {
  parentPort?.postMessage(msg);
}

function sendToDream(msg: MemoryToDream): void {
  // DreamWorker receives via the MessageChannel port passed at spawn time
  // parentPort carries both Main→Memory and Dream→Memory messages
  parentPort?.postMessage({ ...msg, _dest: "dream" });
}

parentPort?.on("message", async (raw: unknown) => {
  if (!ready) {
    log.engine.warn("[MemoryWorker] message received before ready — queuing not implemented, dropping");
    return;
  }

  const msg = raw as MainToMemory | DreamToMemory;

  if (msg.type === "extract") await handleExtract(msg);
  else if (msg.type === "search") await handleSearch(msg);
  else if (msg.type === "write-fact") await handleWriteFact(msg);
  else if (msg.type === "delete-fact") await handleDeleteFact(msg);
  else if (msg.type === "shutdown") {
    log.engine.info("[MemoryWorker] shutdown received");
    process.exit(0);
  }
});

// ─── Start ───────────────────────────────────────────────────────

boot().catch((err) => {
  log.engine.error("[MemoryWorker] boot failed", err as Error);
  process.exit(1);
});

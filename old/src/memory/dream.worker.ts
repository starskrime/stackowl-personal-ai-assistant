/**
 * DreamWorker — nightly self-reflection on mistakes
 *
 * Runs at 3 AM via croner + catches up on startup if last run > 20h.
 * Reads mistake facts from MemoryStore + raw messages from SQLite.
 * Sends write-fact requests to MemoryManager (Actor Model — never writes directly).
 * Relays LLM calls to MemoryManager (provider stays in main thread).
 */

import { parentPort, workerData, isMainThread } from "node:worker_threads";
import { existsSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { Cron } from "croner";
import { log } from "../logger.js";
import { MemoryStore } from "./memory-store.js";
import type { Fact, DreamToMemory, MemoryToDream } from "./fact-schema.js";

if (isMainThread) {
  throw new Error("[DreamWorker] Must be spawned as a worker_thread, not run directly");
}

// ─── Types ────────────────────────────────────────────────────────

interface DreamWorkerData {
  workspacePath: string;
  ggufPath: string;
  dreamCron: string;
}

// Extended IPC — DreamWorker ↔ MemoryManager
type DreamToManager =
  | DreamToMemory
  | { type: "dream:llm-call"; prompt: string; requestId: string }
  | { type: "dream:done"; reflectedCount: number };

type ManagerToDream =
  | MemoryToDream
  | { type: "dream:llm-result"; requestId: string; content: string }
  | { type: "dream:llm-error"; requestId: string; message: string };

// ─── State ───────────────────────────────────────────────────────

const { workspacePath, ggufPath, dreamCron } = workerData as DreamWorkerData;
const DREAM_STATE_PATH = `${workspacePath}/.memory_dream_state.json`;

interface DreamState { lastRunAt: string }

let store: MemoryStore;

// In-flight LLM request callbacks keyed by requestId
const pendingLlm = new Map<string, (result: { content?: string; error?: string }) => void>();

// In-flight write-ack callbacks
const pendingAcks = new Map<string, (success: boolean) => void>();

// ─── Persistence ─────────────────────────────────────────────────

function loadState(): DreamState {
  if (existsSync(DREAM_STATE_PATH)) {
    try {
      return JSON.parse(require("node:fs").readFileSync(DREAM_STATE_PATH, "utf8")) as DreamState;
    } catch { /* corrupt — use default */ }
  }
  return { lastRunAt: new Date(0).toISOString() };
}

function saveState(state: DreamState): void {
  require("node:fs").writeFileSync(DREAM_STATE_PATH, JSON.stringify(state));
}

// ─── LLM relay ───────────────────────────────────────────────────

async function callLlm(prompt: string): Promise<string> {
  const requestId = randomUUID();
  return new Promise((resolve, reject) => {
    pendingLlm.set(requestId, ({ content, error }) => {
      if (error) reject(new Error(error));
      else resolve(content ?? "");
    });
    send({ type: "dream:llm-call", prompt, requestId });
  });
}

// ─── Write relay (Actor Model) ────────────────────────────────────

async function writeFact(fact: Fact): Promise<boolean> {
  const requestId = randomUUID();
  return new Promise((resolve) => {
    pendingAcks.set(requestId, resolve);
    send({ type: "write-fact", fact, requestId });
  });
}

async function deleteFact(factId: string): Promise<boolean> {
  const requestId = randomUUID();
  return new Promise((resolve) => {
    pendingAcks.set(requestId, resolve);
    send({ type: "delete-fact", factId, requestId });
  });
}

// ─── Core dream logic ─────────────────────────────────────────────

async function runDreamCycle(): Promise<void> {
  log.engine.info("[DreamWorker] runDreamCycle: entry");

  const candidates = await store.getDreamCandidates(10);
  log.engine.info("[DreamWorker] runDreamCycle: candidates fetched", { count: candidates.length });

  if (candidates.length === 0) {
    log.engine.info("[DreamWorker] runDreamCycle: no candidates — exit");
    send({ type: "dream:done", reflectedCount: 0 });
    saveState({ lastRunAt: new Date().toISOString() });
    return;
  }

  let reflected = 0;

  for (const fact of candidates) {
    try {
      await reflectOnFact(fact);
      reflected++;
    } catch (err) {
      log.engine.error("[DreamWorker] runDreamCycle: reflection failed for fact", err as Error, {
        factId: fact.factId,
      });
    }
  }

  saveState({ lastRunAt: new Date().toISOString() });
  log.engine.info("[DreamWorker] runDreamCycle: exit", { reflected });
  send({ type: "dream:done", reflectedCount: reflected });
}

async function reflectOnFact(fact: Fact): Promise<void> {
  log.engine.debug("[DreamWorker] reflectOnFact: entry", { factId: fact.factId, type: fact.type });

  // Get raw conversation from SQLite via MemoryManager relay
  // (db is in main thread; worker reads via llm-call relay for now)
  const prompt = buildReflectionPrompt(fact);

  let reflection: string;
  try {
    reflection = await callLlm(prompt);
  } catch (err) {
    log.engine.error("[DreamWorker] reflectOnFact: LLM call failed", err as Error, { factId: fact.factId });
    return;
  }

  if (!reflection.trim()) return;

  const dreamFact: Fact = {
    factId: randomUUID(),
    type: "dream_reflection",
    content: `[Reflection on ${fact.type}] ${reflection.trim()}`,
    confidence: 0.8,
    source: fact.source,
    confirmationCount: 0,
    contradictions: [],
    owlName: fact.owlName,
    userId: fact.userId,
    createdAt: new Date().toISOString(),
  };

  const ok = await writeFact(dreamFact);
  if (!ok) {
    log.engine.warn("[DreamWorker] reflectOnFact: write failed", { factId: dreamFact.factId });
    return;
  }

  // Soft-prune low-confidence approach_failed facts with many contradictions
  if (fact.type === "approach_failed" && fact.contradictions.length >= 2 && fact.confidence < 0.4) {
    await deleteFact(fact.factId);
    log.engine.info("[DreamWorker] reflectOnFact: pruned low-confidence fact", { factId: fact.factId });
  }

  log.engine.debug("[DreamWorker] reflectOnFact: exit", { dreamFactId: dreamFact.factId });
}

function buildReflectionPrompt(fact: Fact): string {
  return [
    `You are ${fact.owlName}, an AI assistant, reflecting on a past mistake or failure pattern.`,
    "",
    `FACT TYPE: ${fact.type}`,
    `FACT CONTENT: ${fact.content}`,
    `CONFIDENCE: ${fact.confidence}`,
    `CONTRADICTIONS COUNT: ${fact.contradictions.length}`,
    "",
    "Reflect on this failure or sub-optimal pattern:",
    "1. Why did this happen? What was the root cause?",
    "2. What specific behavior should change to avoid this?",
    "3. State the corrected approach in one concise sentence.",
    "",
    "Reply in 2-3 sentences maximum. Be specific and actionable. No preamble.",
  ].join("\n");
}

// ─── Boot ─────────────────────────────────────────────────────────

async function boot(): Promise<void> {
  log.engine.info("[DreamWorker] boot: entry", { workspacePath, dreamCron });

  if (!existsSync(ggufPath)) {
    throw new Error(
      `[DreamWorker] GGUF model not found at "${ggufPath}". ` +
      `Commit the model file to the repo via git LFS.`,
    );
  }

  store = new MemoryStore(workspacePath);
  await store.init();
  log.engine.info("[DreamWorker] boot: store ready");

  // Startup catch-up: if last run > 20h ago, run immediately
  const state = loadState();
  const hoursSinceLastRun =
    (Date.now() - new Date(state.lastRunAt).getTime()) / (1000 * 60 * 60);

  if (hoursSinceLastRun > 20) {
    log.engine.info("[DreamWorker] boot: catch-up run triggered", {
      hoursSinceLastRun: Math.round(hoursSinceLastRun),
    });
    await runDreamCycle();
  }

  // Schedule nightly run
  new Cron(dreamCron, async () => {
    log.engine.info("[DreamWorker] cron: dream cycle triggered", { schedule: dreamCron });
    await runDreamCycle().catch((err) => {
      log.engine.error("[DreamWorker] cron: dream cycle failed", err as Error);
    });
  });

  log.engine.info("[DreamWorker] boot: exit — cron scheduled", { schedule: dreamCron });
}

// ─── IPC ─────────────────────────────────────────────────────────

function send(msg: DreamToManager): void {
  parentPort?.postMessage(msg);
}

parentPort?.on("message", (raw: unknown) => {
  const msg = raw as ManagerToDream;

  if (msg.type === "dream:llm-result" || msg.type === "dream:llm-error") {
    const cb = pendingLlm.get(msg.requestId);
    if (cb) {
      pendingLlm.delete(msg.requestId);
      if (msg.type === "dream:llm-result") cb({ content: msg.content });
      else cb({ error: msg.message });
    }
    return;
  }

  if (msg.type === "write-ack") {
    const cb = pendingAcks.get(msg.requestId);
    if (cb) {
      pendingAcks.delete(msg.requestId);
      cb(msg.success);
    }
    return;
  }
});

// ─── Start ───────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-require-imports
const require = (await import("node:module")).createRequire(import.meta.url);

boot().catch((err) => {
  log.engine.error("[DreamWorker] boot failed", err as Error);
  process.exit(1);
});

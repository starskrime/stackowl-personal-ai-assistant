/**
 * Memory SDK — REST API Server
 *
 * Endpoints:
 *   POST   /memory/store       — store a user↔assistant exchange
 *   POST   /memory/recall      — recall relevant memories
 *   POST   /memory/context     — get enriched context string
 *   POST   /memory/flush       — flush turn buffer → episodic memory
 *   DELETE /memory/clear       — clear all memory for a user
 *   GET    /memory/stats/:uid  — memory statistics
 *   GET    /health             — health check
 *
 * Usage:
 *   PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... npx tsx products/memory-sdk/server.ts
 */

import express, { type Request, type Response } from "express";
import { MemorySDK } from "./index.js";
import type { HealthResponse } from "../shared/types.js";

// ─── Provider bootstrap (same as deliberation server) ───────────────────────

async function createSdkProvider() {
  const providerName = process.env.PROVIDER ?? "anthropic";

  if (providerName === "anthropic") {
    const { AnthropicMemoryAdapter } = await import("./adapters/anthropic.js");
    const Anthropic = (await import("@anthropic-ai/sdk")).default;
    const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY ?? "" });
    return new AnthropicMemoryAdapter(client, {
      model: process.env.MODEL ?? "claude-haiku-4-5-20251001",
    });
  }

  if (providerName === "openai") {
    const { OpenAIMemoryAdapter } = await import("./adapters/openai.js");
    const OpenAI = (await import("openai" as string)).default;
    const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY ?? "" });
    return new OpenAIMemoryAdapter(client);
  }

  if (providerName === "ollama" || providerName === "generic") {
    const { GenericMemoryAdapter } = await import("./adapters/generic.js");
    return new GenericMemoryAdapter({
      baseUrl: process.env.PROVIDER_URL ?? "http://localhost:11434/v1",
      apiKey: process.env.PROVIDER_API_KEY ?? "ollama",
      model: process.env.MODEL ?? "llama3.2",
      embedModel: process.env.EMBED_MODEL,
    });
  }

  throw new Error(`Unknown provider: ${providerName}`);
}

// ─── Server ───────────────────────────────────────────────────────────────────

const app = express();
const startTime = Date.now();
const WORKSPACE = process.env.MEMORY_PATH ?? "./memory-store";

let sdk: MemorySDK | null = null;

async function initSdk() {
  const provider = await createSdkProvider().catch((err) => {
    console.warn(`[MemorySDK] Provider init failed, running without LLM: ${err.message}`);
    return undefined;
  });

  sdk = new MemorySDK({
    workspacePath: WORKSPACE,
    provider,
    workingContextWindow: parseInt(process.env.CONTEXT_WINDOW ?? "10", 10),
  });

  console.log(`[MemorySDK] Initialized — workspace: ${WORKSPACE}`);
}

app.use(express.json());
app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  if (req.method === "OPTIONS") { res.sendStatus(204); return; }
  next();
});

app.get("/health", (_req: Request, res: Response) => {
  const body: HealthResponse = {
    status: sdk ? "ok" : "degraded",
    version: "1.0.0",
    uptime: Math.floor((Date.now() - startTime) / 1000),
  };
  res.json(body);
});

// POST /memory/store
app.post("/memory/store", async (req: Request, res: Response) => {
  if (!sdk) { res.status(503).json({ error: "SDK not initialized" }); return; }
  const { userId, message, response: assistantResponse } = req.body as {
    userId?: string;
    message?: string;
    response?: string;
  };
  if (!userId || !message || !assistantResponse) {
    res.status(400).json({ error: "userId, message, and response are required" });
    return;
  }
  try {
    const result = await sdk.store(userId, message, assistantResponse);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// POST /memory/recall
app.post("/memory/recall", async (req: Request, res: Response) => {
  if (!sdk) { res.status(503).json({ error: "SDK not initialized" }); return; }
  const { userId, query } = req.body as { userId?: string; query?: string };
  if (!userId || !query) {
    res.status(400).json({ error: "userId and query are required" });
    return;
  }
  try {
    const result = await sdk.recall(userId, query);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// POST /memory/context
app.post("/memory/context", async (req: Request, res: Response) => {
  if (!sdk) { res.status(503).json({ error: "SDK not initialized" }); return; }
  const { userId, query } = req.body as { userId?: string; query?: string };
  if (!userId) {
    res.status(400).json({ error: "userId is required" });
    return;
  }
  try {
    const result = await sdk.context(userId, query);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// POST /memory/flush
app.post("/memory/flush", async (req: Request, res: Response) => {
  if (!sdk) { res.status(503).json({ error: "SDK not initialized" }); return; }
  const { userId } = req.body as { userId?: string };
  if (!userId) {
    res.status(400).json({ error: "userId is required" });
    return;
  }
  try {
    const flushed = await sdk.flush(userId);
    res.json({ flushed });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// DELETE /memory/clear
app.delete("/memory/clear", async (req: Request, res: Response) => {
  if (!sdk) { res.status(503).json({ error: "SDK not initialized" }); return; }
  const { userId } = req.body as { userId?: string };
  if (!userId) {
    res.status(400).json({ error: "userId is required" });
    return;
  }
  try {
    await sdk.clear(userId);
    res.json({ cleared: true });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// GET /memory/stats/:userId
app.get("/memory/stats/:userId", async (req: Request, res: Response) => {
  if (!sdk) { res.status(503).json({ error: "SDK not initialized" }); return; }
  try {
    const result = await sdk.stats(req.params.userId);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// ─── Start ────────────────────────────────────────────────────────────────────

const PORT = parseInt(process.env.PORT ?? "3002", 10);

initSdk().then(() => {
  app.listen(PORT, () => {
    console.log(`🧠 Memory SDK server running at http://localhost:${PORT}`);
    console.log(`   Provider:  ${process.env.PROVIDER ?? "anthropic"}`);
    console.log(`   Workspace: ${WORKSPACE}`);
  });
}).catch((err) => {
  console.error("Failed to start Memory SDK server:", err);
  process.exit(1);
});

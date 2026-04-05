/**
 * Persona Engine — REST API Server
 *
 * Endpoints:
 *   POST   /personas                        — register a persona definition
 *   GET    /personas                        — list all registered personas
 *   GET    /personas/:id                    — get definition
 *   GET    /personas/:id/render/:userId     — render system prompt for user
 *   POST   /personas/:id/evolve/:userId     — evolve persona from transcript
 *   POST   /personas/:id/snapshot/:userId   — take a snapshot
 *   POST   /personas/:id/rollback/:userId   — rollback to snapshot
 *   POST   /personas/:id/reset/:userId      — reset to base traits
 *   GET    /personas/:id/analytics/:userId  — drift analytics
 *   GET    /personas/:id/export/:userId     — export definition + state
 *   GET    /health
 *
 * Usage:
 *   WORKSPACE=./persona-store PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... \
 *   npx tsx products/persona/server.ts
 */

import express, { type Request, type Response } from "express";
import { PersonaEngine } from "./engine.js";
import type { PersonaDefinition } from "./types.js";
import type { MemoryProvider } from "../memory-sdk/types.js";
import type { HealthResponse } from "../shared/types.js";

// ─── Provider bootstrap ───────────────────────────────────────────────────

async function createProvider(): Promise<MemoryProvider | undefined> {
  const name = process.env.PROVIDER ?? "anthropic";
  try {
    if (name === "anthropic") {
      const { AnthropicMemoryAdapter } = await import("../memory-sdk/adapters/anthropic.js");
      const Anthropic = (await import("@anthropic-ai/sdk")).default;
      return new AnthropicMemoryAdapter(new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY ?? "" }), {
        model: process.env.MODEL ?? "claude-haiku-4-5-20251001",
      });
    }
    if (name === "openai") {
      const { OpenAIMemoryAdapter } = await import("../memory-sdk/adapters/openai.js");
      const OpenAI = (await import("openai" as string)).default;
      return new OpenAIMemoryAdapter(new OpenAI({ apiKey: process.env.OPENAI_API_KEY ?? "" }));
    }
    if (name === "ollama" || name === "generic") {
      const { GenericMemoryAdapter } = await import("../memory-sdk/adapters/generic.js");
      return new GenericMemoryAdapter({
        baseUrl: process.env.PROVIDER_URL ?? "http://localhost:11434/v1",
        apiKey: process.env.PROVIDER_API_KEY ?? "ollama",
        model: process.env.MODEL ?? "llama3.2",
      });
    }
  } catch { return undefined; }
  return undefined;
}

// ─── Server ───────────────────────────────────────────────────────────────────

const app = express();
const startTime = Date.now();
const WORKSPACE = process.env.WORKSPACE ?? "./persona-store";

const engine = new PersonaEngine({ workspacePath: WORKSPACE, defaultDecayRate: 0.02 });

app.use(express.json());
app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") { res.sendStatus(204); return; }
  next();
});

app.use(express.static(new URL("./public", import.meta.url).pathname));

app.get("/health", (_req: Request, res: Response) => {
  const body: HealthResponse = { status: "ok", version: "1.0.0", uptime: Math.floor((Date.now() - startTime) / 1000) };
  res.json(body);
});

// POST /personas — register
app.post("/personas", async (req: Request, res: Response) => {
  const def = req.body as PersonaDefinition;
  if (!def.id || !def.name || !def.systemPromptTemplate) {
    res.status(400).json({ error: "id, name, and systemPromptTemplate are required" });
    return;
  }
  try {
    await engine.register(def);
    res.status(201).json(def);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// GET /personas
app.get("/personas", (_req: Request, res: Response) => {
  res.json(engine.listDefinitions());
});

// GET /personas/:id
app.get("/personas/:id", (req: Request, res: Response) => {
  const def = engine.getDefinition(req.params.id);
  if (!def) { res.status(404).json({ error: "Persona not found" }); return; }
  res.json(def);
});

// GET /personas/:id/render/:userId
app.get("/personas/:id/render/:userId", async (req: Request, res: Response) => {
  try {
    const rendered = await engine.render(req.params.userId, req.params.id);
    res.json(rendered);
  } catch (err) {
    res.status(err instanceof Error && err.message.includes("not registered") ? 404 : 500)
      .json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// POST /personas/:id/evolve/:userId
app.post("/personas/:id/evolve/:userId", async (req: Request, res: Response) => {
  const { transcript } = req.body as { transcript?: Array<{ role: string; content: string }> };
  if (!transcript || !Array.isArray(transcript)) {
    res.status(400).json({ error: "transcript array is required" });
    return;
  }
  try {
    const provider = await createProvider();
    if (!provider) { res.status(503).json({ error: "No LLM provider configured" }); return; }
    const result = await engine.evolve(req.params.userId, req.params.id, transcript, provider);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// POST /personas/:id/snapshot/:userId
app.post("/personas/:id/snapshot/:userId", async (req: Request, res: Response) => {
  const { reason } = req.body as { reason?: string };
  try {
    const snap = await engine.snapshot(req.params.userId, req.params.id, reason ?? "manual");
    res.json(snap);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// POST /personas/:id/rollback/:userId
app.post("/personas/:id/rollback/:userId", async (req: Request, res: Response) => {
  const { snapshotId } = req.body as { snapshotId?: string };
  if (!snapshotId) { res.status(400).json({ error: "snapshotId is required" }); return; }
  try {
    const ok = await engine.rollback(req.params.userId, req.params.id, snapshotId);
    res.json({ success: ok });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// POST /personas/:id/reset/:userId
app.post("/personas/:id/reset/:userId", async (req: Request, res: Response) => {
  try {
    await engine.reset(req.params.userId, req.params.id);
    res.json({ reset: true });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// GET /personas/:id/analytics/:userId
app.get("/personas/:id/analytics/:userId", async (req: Request, res: Response) => {
  try {
    const analytics = await engine.analytics(req.params.userId, req.params.id);
    res.json(analytics);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// GET /personas/:id/export/:userId
app.get("/personas/:id/export/:userId", async (req: Request, res: Response) => {
  try {
    const data = await engine.export(req.params.userId, req.params.id);
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// ─── Startup: load existing definitions ──────────────────────────────────────

const PORT = parseInt(process.env.PORT ?? "3004", 10);

engine.loadDefinitions().then((defs) => {
  console.log(`[PersonaEngine] Loaded ${defs.length} persona definition(s)`);
  app.listen(PORT, () => {
    console.log(`🎭 Persona Engine running at http://localhost:${PORT}`);
    console.log(`   Workspace:  ${WORKSPACE}`);
    console.log(`   Dashboard:  http://localhost:${PORT}/index.html`);
  });
});

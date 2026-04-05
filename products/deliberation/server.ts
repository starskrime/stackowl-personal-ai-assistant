/**
 * Deliberation Engine — REST API Server
 *
 * Endpoints:
 *   POST /debate         — start a debate (returns full result, waits)
 *   POST /debate/stream  — stream debate events via SSE
 *   GET  /voices         — list available voice presets
 *   GET  /health         — health check
 *
 * Usage:
 *   npx tsx products/deliberation/server.ts
 *   PORT=3001 PROVIDER=anthropic npx tsx products/deliberation/server.ts
 */

import express, { type Request, type Response } from "express";
import { DeliberationEngine } from "./engine.js";
import type {
  VoicePosition,
  VoiceChallenge,
  DebateVerdict,
  DebateOptions,
} from "./engine.js";
import { getAllVoices } from "./voices.js";
import type { HealthResponse } from "../shared/types.js";

// ─── Provider bootstrap ───────────────────────────────────────────────────────

async function createProvider() {
  const providerName = process.env.PROVIDER ?? "anthropic";

  if (providerName === "anthropic") {
    const { AnthropicProvider } = await import(
      "../../src/providers/anthropic.js"
    );
    return new AnthropicProvider({
      apiKey: process.env.ANTHROPIC_API_KEY ?? "",
      defaultModel: process.env.MODEL ?? "claude-haiku-4-5-20251001",
    });
  }

  if (providerName === "openai") {
    const { OpenAIProvider } = await import("../../src/providers/openai.js");
    return new OpenAIProvider({
      apiKey: process.env.OPENAI_API_KEY ?? "",
      defaultModel: process.env.MODEL ?? "gpt-4o-mini",
    });
  }

  if (providerName === "ollama") {
    const { OllamaProvider } = await import("../../src/providers/ollama.js");
    return new OllamaProvider({
      baseUrl: process.env.OLLAMA_URL ?? "http://localhost:11434",
      defaultModel: process.env.MODEL ?? "llama3.2",
    });
  }

  throw new Error(`Unknown provider: ${providerName}`);
}

// ─── Server ───────────────────────────────────────────────────────────────────

const app = express();
const startTime = Date.now();

app.use(express.json());

// CORS
app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  if (req.method === "OPTIONS") {
    res.sendStatus(204);
    return;
  }
  next();
});

// Health
app.get("/health", (_req: Request, res: Response) => {
  const body: HealthResponse = {
    status: "ok",
    version: "1.0.0",
    uptime: Math.floor((Date.now() - startTime) / 1000),
  };
  res.json(body);
});

// List voices
app.get("/voices", (_req: Request, res: Response) => {
  res.json(getAllVoices());
});

// Serve web UI
app.use(express.static(new URL("./public", import.meta.url).pathname));

// POST /debate — full result (waits for completion)
app.post("/debate", async (req: Request, res: Response) => {
  const { topic, voiceIds, model } = req.body as {
    topic?: string;
    voiceIds?: string[];
    model?: string;
  };

  if (!topic || typeof topic !== "string" || topic.trim().length === 0) {
    res.status(400).json({ error: "topic is required" });
    return;
  }

  try {
    const provider = await createProvider();
    const engine = new DeliberationEngine(provider);

    const options: DebateOptions = {};
    if (voiceIds && Array.isArray(voiceIds)) options.voiceIds = voiceIds;
    if (model) options.model = model;

    const result = await engine.debate(topic.trim(), options);
    res.json(result);
  } catch (err) {
    console.error("[Deliberation] Error:", err);
    res.status(500).json({
      error: err instanceof Error ? err.message : "Internal error",
    });
  }
});

// POST /debate/stream — SSE streaming
app.post("/debate/stream", async (req: Request, res: Response) => {
  const { topic, voiceIds, model } = req.body as {
    topic?: string;
    voiceIds?: string[];
    model?: string;
  };

  if (!topic || typeof topic !== "string" || topic.trim().length === 0) {
    res.status(400).json({ error: "topic is required" });
    return;
  }

  // Set up SSE headers
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  const send = (event: string, data: unknown) => {
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };

  try {
    const provider = await createProvider();
    const engine = new DeliberationEngine(provider);

    const options: DebateOptions = {
      callbacks: {
        onRoundStart: (round) => send("round_start", round),
        onPositionReady: (position: VoicePosition) =>
          send("position", position),
        onChallengeReady: (challenge: VoiceChallenge) =>
          send("challenge", challenge),
        onSynthesisReady: (verdict: DebateVerdict, synthesis: string) =>
          send("synthesis", { verdict, synthesis }),
      },
    };

    if (voiceIds && Array.isArray(voiceIds)) options.voiceIds = voiceIds;
    if (model) options.model = model;

    const result = await engine.debate(topic.trim(), options);
    send("complete", result);
    res.write("event: done\ndata: {}\n\n");
    res.end();
  } catch (err) {
    console.error("[Deliberation] Stream error:", err);
    send("error", { error: err instanceof Error ? err.message : "Internal error" });
    res.end();
  }
});

// ─── Start ────────────────────────────────────────────────────────────────────

const PORT = parseInt(process.env.PORT ?? "3001", 10);

app.listen(PORT, () => {
  console.log(`🏛️  Deliberation Engine running at http://localhost:${PORT}`);
  console.log(`   Provider: ${process.env.PROVIDER ?? "anthropic"}`);
  console.log(`   Web UI:   http://localhost:${PORT}/index.html`);
  console.log(`   API:      POST http://localhost:${PORT}/debate`);
  console.log(`   Voices:   GET  http://localhost:${PORT}/voices`);
});

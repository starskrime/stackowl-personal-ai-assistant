/**
 * Longitudinal AI — REST API + Dashboard Server
 *
 * Endpoints:
 *   GET  /timeline/:userId          — full timeline with drift per window
 *   GET  /drift/:userId             — drift report
 *   POST /report/:userId            — generate personality report (LLM)
 *   GET  /health
 *
 * Usage:
 *   MEMORY_PATH=./memory-store PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... \
 *   npx tsx products/longitudinal/server.ts
 */

import express, { type Request, type Response } from "express";
import { TimelineEngine, type WindowSize } from "./timeline.js";
import { DriftDetector } from "./drift.js";
import { PersonalityReportGenerator, type ReportType } from "./report.js";
import type { HealthResponse } from "../shared/types.js";
import type { MemoryProvider } from "../memory-sdk/types.js";

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
  } catch {
    return undefined;
  }
  return undefined;
}

// ─── Server ───────────────────────────────────────────────────────────────────

const app = express();
const startTime = Date.now();
const MEMORY_PATH = process.env.MEMORY_PATH ?? "./memory-store";

app.use(express.json());
app.use((req, res, next) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") { res.sendStatus(204); return; }
  next();
});

app.use(express.static(new URL("./public", import.meta.url).pathname));

app.get("/health", (_req: Request, res: Response) => {
  const body: HealthResponse = { status: "ok", version: "1.0.0", uptime: Math.floor((Date.now() - startTime) / 1000) };
  res.json(body);
});

// GET /timeline/:userId
app.get("/timeline/:userId", async (req: Request, res: Response) => {
  const { userId } = req.params;
  const windowSize = (req.query.window as WindowSize) ?? "month";
  const maxWindows = parseInt(String(req.query.max ?? "12"), 10);

  try {
    const engine = new TimelineEngine();
    const timeline = await engine.build(
      `${MEMORY_PATH}/${userId}`,
      windowSize,
      maxWindows,
    );
    res.json(timeline);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// GET /drift/:userId
app.get("/drift/:userId", async (req: Request, res: Response) => {
  const { userId } = req.params;
  const windowSize = (req.query.window as WindowSize) ?? "month";

  try {
    const engine = new TimelineEngine();
    const timeline = await engine.build(`${MEMORY_PATH}/${userId}`, windowSize);
    const detector = new DriftDetector();
    const drift = detector.detect(timeline);
    res.json(drift);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// POST /report/:userId
app.post("/report/:userId", async (req: Request, res: Response) => {
  const { userId } = req.params;
  const { type, tone, windowSize } = req.body as {
    type?: ReportType;
    tone?: "warm" | "direct" | "analytical";
    windowSize?: WindowSize;
  };

  try {
    const provider = await createProvider();
    if (!provider) {
      res.status(503).json({ error: "No LLM provider configured for report generation" });
      return;
    }

    const engine = new TimelineEngine();
    const timeline = await engine.build(`${MEMORY_PATH}/${userId}`, windowSize ?? "month");
    const drift = new DriftDetector().detect(timeline);
    const generator = new PersonalityReportGenerator(provider);
    const report = await generator.generate(timeline, drift, { type, tone });
    res.json(report);
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : "Internal error" });
  }
});

// ─── Start ────────────────────────────────────────────────────────────────────

const PORT = parseInt(process.env.PORT ?? "3003", 10);

app.listen(PORT, () => {
  console.log(`📈 Longitudinal AI running at http://localhost:${PORT}`);
  console.log(`   Memory path: ${MEMORY_PATH}`);
  console.log(`   Dashboard:   http://localhost:${PORT}/index.html`);
});

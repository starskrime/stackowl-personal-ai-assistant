/**
 * StackOwl — WebSocket Control Plane Server
 *
 * Unified server that routes ALL clients through the Gateway via WebSocket.
 * Replaces the old REST-only server with:
 *   - WebSocket control plane for real-time chat + streaming
 *   - REST APIs for queries (owls, pellets, sessions, status)
 *   - Admin control channel (session management, broadcast, system status)
 *   - Connected client tracking
 *   - Parliament sessions via Gateway
 *
 * Architecture:
 *   Client ──ws──→ ControlPlane ──→ Gateway ──→ OwlEngine
 *                                      ↕
 *                              All other adapters (CLI, Telegram)
 */

import express from "express";
import { createServer, type Server as HTTPServer } from "node:http";
import { WebSocketServer, type WebSocket } from "ws";
import { join } from "node:path";
import { writeFileSync, unlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";
import { v4 as uuidv4 } from "uuid";
import type { OwlRegistry } from "../owls/registry.js";
import type { PelletStore } from "../pellets/store.js";
import type { SessionStore } from "../memory/store.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ChannelAdapter, GatewayResponse } from "../gateway/types.js";
import type { StreamEvent } from "../providers/base.js";
import {
  makeSessionId,
  makeMessageId,
  type OwlGateway,
} from "../gateway/core.js";
import { ParliamentOrchestrator } from "../parliament/orchestrator.js";
import { WhisperSTT } from "../voice/stt.js";
import { log } from "../logger.js";

// ─── Types ──────────────────────────────────────────────────────

interface ConnectedClient {
  id: string;
  ws: WebSocket;
  sessionId: string;
  connectedAt: number;
  lastActivity: number;
  messageCount: number;
  isAdmin: boolean;
  subscriptions: Set<string>;
}

/**
 * Client → Server protocol:
 *   { type: "message", text: string, sessionId?: string }
 *   { type: "ping" }
 *   { type: "admin", command: string, ...args }
 *
 * Server → Client protocol:
 *   { type: "connected", clientId, owl: { name, emoji }, serverTime }
 *   { type: "response", ...GatewayResponse }
 *   { type: "stream", event: StreamEvent }
 *   { type: "progress", text: string }
 *   { type: "file", path, caption? }
 *   { type: "error", message: string }
 *   { type: "pong" }
 *   { type: "admin_response", command, data }
 *   { type: "broadcast", from: string, content: string }
 */

// ─── WebSocket Channel Adapter ──────────────────────────────────

class ControlPlaneAdapter implements ChannelAdapter {
  readonly id = "websocket";
  readonly name = "WebSocket Control Plane";

  private clients: Map<string, ConnectedClient> = new Map();

  readonly gateway: OwlGateway;
  readonly wss: WebSocketServer;

  constructor(gateway: OwlGateway, wss: WebSocketServer) {
    this.gateway = gateway;
    this.wss = wss;
  }

  getClients(): Map<string, ConnectedClient> {
    return this.clients;
  }

  getClientCount(): number {
    return this.clients.size;
  }

  addClient(client: ConnectedClient): void {
    this.clients.set(client.id, client);
  }

  removeClient(clientId: string): void {
    this.clients.delete(clientId);
  }

  getClient(clientId: string): ConnectedClient | undefined {
    return this.clients.get(clientId);
  }

  findClientByWs(ws: WebSocket): ConnectedClient | undefined {
    for (const client of this.clients.values()) {
      if (client.ws === ws) return client;
    }
    return undefined;
  }

  async sendToUser(userId: string, response: GatewayResponse): Promise<void> {
    for (const client of this.clients.values()) {
      if (client.id === userId && client.ws.readyState === client.ws.OPEN) {
        client.ws.send(JSON.stringify({ type: "response", ...response }));
      }
    }
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    const msg = JSON.stringify({
      type: "broadcast",
      from: "system",
      ...response,
    });
    for (const client of this.clients.values()) {
      if (client.ws.readyState === client.ws.OPEN) {
        client.ws.send(msg);
      }
    }
  }

  async start(): Promise<void> {
    log.engine.info("[ControlPlane] WebSocket adapter started");
  }

  stop(): void {
    for (const client of this.clients.values()) {
      client.ws.close();
    }
    this.clients.clear();
    log.engine.info("[ControlPlane] WebSocket adapter stopped");
  }
}

// ─── Server ─────────────────────────────────────────────────────

export class StackOwlServer {
  private app: express.Express;
  private httpServer: HTTPServer;
  private wss: WebSocketServer;
  private adapter: ControlPlaneAdapter;
  private port: number;
  private startTime: number = Date.now();
  private stt: WhisperSTT | null = null;
  private sttReady = false;

  constructor(
    private config: StackOwlConfig,
    private gateway: OwlGateway,
    private owlRegistry: OwlRegistry,
    private pelletStore: PelletStore,
    private sessionStore: SessionStore,
    port = 3000,
  ) {
    this.port = port;
    this.app = express();
    this.httpServer = createServer(this.app);
    this.wss = new WebSocketServer({ server: this.httpServer });

    // Create and register the adapter
    this.adapter = new ControlPlaneAdapter(this.gateway, this.wss);
    this.gateway.register(this.adapter);

    this.setupMiddleware();
    this.setupRESTRoutes();
    this.setupWebSocket();
    this.setupEventBusHook();
  }

  // ─── Event Bus Hook ──────────────────────────────────────────

  private setupEventBusHook(): void {
    const eventBus = this.gateway.ctx.eventBus;
    if (eventBus) {
      eventBus.on(
        "*" as any,
        (eventData: { type: string; payload: unknown }) => {
          const { type, payload } = eventData;
          const msg = JSON.stringify({ type: "event", event: type, payload });
          for (const client of this.adapter.getClients().values()) {
            if (
              (client.subscriptions.has(type) ||
                client.subscriptions.has("*")) &&
              client.ws.readyState === client.ws.OPEN
            ) {
              client.ws.send(msg);
            }
          }
        },
      );
      log.engine.info(
        "[ControlPlane] Subscribed to global EventBus for Pub/Sub",
      );
    }
  }

  // ─── Express Middleware ────────────────────────────────────────

  private setupMiddleware(): void {
    // Raw body for audio uploads — must come BEFORE express.json()
    this.app.use("/api/voice", express.raw({ type: "*/*", limit: "25mb" }));

    this.app.use(express.json());

    // CORS for dev
    this.app.use((_req, res, next) => {
      res.header("Access-Control-Allow-Origin", "*");
      res.header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Id, X-User-Id");
      res.header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
      next();
    });

    // Serve face page at root too — it's the main UI
    const faceHtml = join(process.cwd(), "src", "web", "face", "index.html");
    this.app.get("/", (_req, res) => res.sendFile(faceHtml));

    // Serve static web UI
    const webDir = join(process.cwd(), "src", "web");
    this.app.use(express.static(webDir));

    // Serve node_modules for client-side libs (3d-force-graph, etc.)
    const nmDir = join(process.cwd(), "node_modules");
    this.app.use("/node_modules", express.static(nmDir));
  }

  // ─── STT (lazy, warm on first request) ─────────────────────────

  private getSTT(): WhisperSTT {
    if (!this.stt) {
      const voiceCfg = this.config.voice ?? {};
      this.stt = new WhisperSTT({
        model: (voiceCfg.model as any) ?? "base.en",
        language: "en",
        removeWavAfter: true,
      });
      // Warm in background — don't block the request
      if (!this.sttReady) {
        this.stt.ensureReady()
          .then(() => { this.sttReady = true; })
          .catch((e) => log.engine.warn(`[WebVoice] STT warmup failed: ${e.message}`));
      }
    }
    return this.stt;
  }

  // ─── REST Routes ──────────────────────────────────────────────

  private setupRESTRoutes(): void {
    // --- Health Check (K8s/ALB probes) ---
    this.app.get("/health", (_req, res) => {
      res
        .status(200)
        .json({ status: "ok", timestamp: new Date().toISOString() });
    });

    this.app.get("/ready", (_req, res) => {
      const owl = this.gateway.getOwl();
      const hasOwl = owl && owl.persona.name.length > 0;
      const connectedClients = this.adapter.getClientCount();
      res.status(hasOwl ? 200 : 503).json({
        status: hasOwl ? "ready" : "not_ready",
        owl: hasOwl ? owl.persona.name : null,
        connectedClients,
        timestamp: new Date().toISOString(),
      });
    });

    // --- System Status ---
    this.app.get("/api/status", (_req, res) => {
      const owl = this.gateway.getOwl();
      res.json({
        status: "online",
        uptime: Math.round((Date.now() - this.startTime) / 1000),
        owl: { name: owl.persona.name, emoji: owl.persona.emoji },
        connectedClients: this.adapter.getClientCount(),
        serverTime: new Date().toISOString(),
      });
    });

    // --- Owls ---
    this.app.get("/api/owls", (_req, res) => {
      const owls = this.owlRegistry.listOwls().map((o) => ({
        name: o.persona.name,
        emoji: o.persona.emoji,
        type: o.persona.type,
        challengeLevel: o.dna.evolvedTraits.challengeLevel,
        specialties: o.persona.specialties,
        generation: o.dna.generation,
      }));
      res.json(owls);
    });

    // --- Pellets ---
    this.app.get("/api/pellets", async (req, res) => {
      const { q } = req.query;
      const pellets =
        q && typeof q === "string"
          ? await this.pelletStore.search(q)
          : await this.pelletStore.listAll();
      res.json(pellets);
    });

    // --- Sessions ---
    this.app.get("/api/sessions", async (_req, res) => {
      const sessions = await this.sessionStore.listSessions();
      res.json(
        sessions.slice(0, 50).map((s) => ({
          id: s.id,
          messageCount: s.messages.length,
          startedAt: s.metadata.startedAt,
          lastActivity: s.metadata.lastUpdatedAt,
        })),
      );
    });

    // --- Connected Clients ---
    this.app.get("/api/clients", (_req, res) => {
      const clients = [...this.adapter.getClients().values()].map((c) => ({
        id: c.id,
        sessionId: c.sessionId,
        connectedAt: c.connectedAt,
        lastActivity: c.lastActivity,
        messageCount: c.messageCount,
        isAdmin: c.isAdmin,
      }));
      res.json(clients);
    });

    // --- Chat (REST fallback for simple integrations) ---
    this.app.post("/api/chat", async (req, res) => {
      const { message, sessionId } = req.body;
      if (!message) {
        res.status(400).json({ error: "message is required" });
        return;
      }

      const sid = sessionId || `rest_${uuidv4().slice(0, 8)}`;

      try {
        const response = await this.gateway.handle(
          {
            id: makeMessageId(),
            channelId: "rest",
            userId: sid,
            sessionId: makeSessionId("rest", sid),
            text: message,
          },
          {},
        );
        res.json(response);
      } catch (error) {
        res.status(500).json({ error: "Failed to process message" });
      }
    });

    // --- Parliament (REST) ---
    this.app.post("/api/parliament", async (req, res) => {
      const { topic, owlNames } = req.body;
      if (!topic) {
        res.status(400).json({ error: "topic is required" });
        return;
      }

      const provider = this.gateway.getProvider();
      const orchestrator = new ParliamentOrchestrator(
        provider,
        this.config,
        this.pelletStore,
        this.gateway.getToolRegistry(),
        this.gateway.ctx.db,
      );

      const participants = owlNames
        ? owlNames.map((n: string) => this.owlRegistry.get(n)).filter(Boolean)
        : this.owlRegistry.listOwls().slice(0, 3);

      if (participants.length < 2) {
        res.status(400).json({ error: "At least 2 owls required" });
        return;
      }

      try {
        const session = await orchestrator.convene({
          topic,
          participants,
          contextMessages: [],
        });
        res.json({ report: orchestrator.formatSessionMarkdown(session) });
      } catch {
        res.status(500).json({ error: "Parliament session failed" });
      }
    });

    // --- Main UI (face + comm hub) ---
    const faceHtml = join(process.cwd(), "src", "web", "face", "index.html");
    this.app.get("/face", (_req, res) => res.sendFile(faceHtml));

    // --- Web voice: browser sends raw WAV → STT → gateway → JSON ---
    // Browser records 16kHz mono WAV using AudioContext + ScriptProcessor
    // and POSTs raw bytes here. Returns { transcript, response }.
    this.app.post("/api/voice", async (req, res) => {
      const wavBuf = req.body as Buffer;
      if (!Buffer.isBuffer(wavBuf) || wavBuf.length < 44) {
        res.status(400).json({ error: "No audio data received" });
        return;
      }

      const userId    = (req.headers["x-user-id"]    as string) ?? "web-anon";
      const sessionId = (req.headers["x-session-id"] as string) ?? makeSessionId("web", userId);

      // Write WAV to temp file
      const wavPath = join(tmpdir(), `stackowl-web-${randomUUID()}.wav`);
      try {
        writeFileSync(wavPath, wavBuf);
      } catch (e) {
        res.status(500).json({ error: "Failed to write audio" });
        return;
      }

      // Transcribe
      let transcript: string;
      try {
        const stt = this.getSTT();
        transcript = await stt.transcribe(wavPath);
      } catch (e) {
        if (existsSync(wavPath)) { try { unlinkSync(wavPath); } catch {} }
        res.status(500).json({ error: `STT failed: ${(e as Error).message}` });
        return;
      }

      if (!transcript.trim()) {
        res.json({ transcript: "", response: null });
        return;
      }

      log.engine.info(`[WebVoice] user:${userId} → "${transcript}"`);

      // Route through gateway
      try {
        const response = await this.gateway.handle(
          {
            id: makeMessageId(),
            channelId: "web",
            userId,
            sessionId,
            text: transcript,
          },
          {},
        );
        res.json({ transcript, response });
      } catch (e) {
        res.status(500).json({ error: `Gateway error: ${(e as Error).message}` });
      }
    });

    // Snapshot of the current knowledge graph (pellet nodes + edges)
    // for the face visualization to bootstrap itself on load.
    this.app.get("/api/face/graph", async (_req, res) => {
      try {
        const pellets = await this.pelletStore.listAll();
        const nodes = pellets.map((p) => ({
          id: p.id,
          label: p.title,
          tags: p.tags,
          source: p.source,
          owls: p.owls,
          generatedAt: p.generatedAt,
          excerpt: p.content ? p.content.slice(0, 200) : "",
          val: Math.max(1.5, 1 + (p.tags.length ?? 0) * 0.7),
        }));
        // Co-tag edges: connect pellets that share at least one tag
        const edges: { source: string; target: string }[] = [];
        for (let i = 0; i < nodes.length; i++) {
          for (let j = i + 1; j < nodes.length; j++) {
            const a = pellets[i]?.tags ?? [];
            const b = pellets[j]?.tags ?? [];
            if (a.some((t) => b.includes(t))) {
              edges.push({ source: nodes[i]!.id, target: nodes[j]!.id });
            }
          }
        }
        res.json({ nodes, edges });
      } catch {
        res.json({ nodes: [], edges: [] });
      }
    });

    // --- Broadcast (REST admin) ---
    this.app.post("/api/broadcast", async (req, res) => {
      const { message } = req.body;
      if (!message) {
        res.status(400).json({ error: "message is required" });
        return;
      }
      await this.gateway.broadcastProactive(message);
      res.json({ sent: true, clients: this.adapter.getClientCount() });
    });

    // --- Secure API key entry (Option C path B for Telegram config menu) ---
    // GET  /config/key?token=xxx  → serves a minimal HTML form (no JS framework)
    // POST /config/key            → receives the key, forwards to TelegramConfigMenu
    this.app.get("/config/key", (req, res) => {
      const token = req.query["token"];
      if (!token || typeof token !== "string") {
        res.status(400).send("<h2>Invalid or missing token.</h2>");
        return;
      }

      // Minimal, clean HTML form — no external dependencies
      res.type("html").send(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>StackOwl — Secure Key Entry</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; background: #0d1117; color: #e6edf3;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .card {
      background: #161b22; border: 1px solid #30363d;
      border-radius: 12px; padding: 2rem; width: 100%; max-width: 420px;
    }
    .owl { font-size: 2.5rem; margin-bottom: .5rem; }
    h1 { font-size: 1.25rem; font-weight: 600; margin-bottom: .25rem; }
    p  { font-size: .875rem; color: #8b949e; margin-bottom: 1.5rem; }
    label { display: block; font-size: .875rem; font-weight: 500; margin-bottom: .375rem; }
    input {
      display: block; width: 100%; padding: .625rem .875rem;
      background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
      color: #e6edf3; font-size: .875rem; font-family: monospace;
      margin-bottom: 1rem;
    }
    input:focus { outline: none; border-color: #58a6ff; }
    button {
      width: 100%; padding: .625rem; background: #238636; border: none;
      border-radius: 6px; color: #fff; font-size: .9375rem; font-weight: 600;
      cursor: pointer;
    }
    button:hover { background: #2ea043; }
    .note { font-size: .75rem; color: #8b949e; margin-top: 1rem; text-align: center; }
    .success, .error { padding: .75rem; border-radius: 6px; margin-top: 1rem; font-size: .875rem; }
    .success { background: #0f2a17; border: 1px solid #238636; color: #3fb950; }
    .error   { background: #2a1215; border: 1px solid #da3633; color: #f85149; }
  </style>
</head>
<body>
  <div class="card">
    <div class="owl">🦉</div>
    <h1>Secure API Key Entry</h1>
    <p>Enter your API key below. It will be sent directly to your local StackOwl instance and never stored by Telegram.</p>
    <form id="form" method="POST" action="/config/key">
      <input type="hidden" name="token" value="${token}">
      <label for="key">API Key</label>
      <input type="password" id="key" name="apiKey" placeholder="sk-..." autocomplete="off" required autofocus>
      <button type="submit">Save Key Securely</button>
    </form>
    <div id="result"></div>
    <p class="note">🔒 This page is only accessible from your local machine.</p>
  </div>
  <script>
    document.getElementById('form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const data = new FormData(e.target);
      const res  = await fetch('/config/key', { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ token: data.get('token'), apiKey: data.get('apiKey') }) });
      const json = await res.json();
      const div  = document.getElementById('result');
      if (json.ok) {
        div.innerHTML = '<div class="success">✅ ' + json.message + ' Return to Telegram and tap "I\\'ve entered the key".</div>';
        document.getElementById('form').style.display = 'none';
      } else {
        div.innerHTML = '<div class="error">❌ ' + json.message + '</div>';
      }
    });
  </script>
</body>
</html>`);
    });

    this.app.post("/config/key", async (req, res) => {
      const { token, apiKey } = req.body as { token?: string; apiKey?: string };
      if (!token || !apiKey) {
        res.status(400).json({ ok: false, message: "token and apiKey are required." });
        return;
      }

      // Delegate to the Telegram adapter's config menu token registry
      const telegramAdapter = this.gateway.getAdapters?.().find(
        (a: { id: string }) => a.id === "telegram",
      ) as any;
      if (!telegramAdapter?.configMenu?.consumeWebFormToken) {
        res.status(503).json({ ok: false, message: "Telegram adapter not available." });
        return;
      }

      const result = await telegramAdapter.configMenu.consumeWebFormToken(token, apiKey);
      res.json(result);
    });
  }


  // ─── WebSocket Control Plane ──────────────────────────────────

  private setupWebSocket(): void {
    this.wss.on("connection", (ws, req) => {
      const clientId = `ws_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      const isAdmin = req.url?.includes("admin=true") ?? false;

      const client: ConnectedClient = {
        id: clientId,
        ws,
        sessionId: makeSessionId("websocket", clientId),
        connectedAt: Date.now(),
        lastActivity: Date.now(),
        messageCount: 0,
        isAdmin,
        subscriptions: new Set(),
      };

      this.adapter.addClient(client);
      log.engine.info(
        `[ControlPlane] Client connected: ${clientId}${isAdmin ? " (admin)" : ""}`,
      );

      // Send welcome
      const owl = this.gateway.getOwl();
      this.send(ws, {
        type: "connected",
        clientId,
        owl: { name: owl.persona.name, emoji: owl.persona.emoji },
        serverTime: new Date().toISOString(),
        connectedClients: this.adapter.getClientCount(),
      });

      // Handle messages
      ws.on("message", async (raw) => {
        try {
          const data = JSON.parse(raw.toString());
          if (
            typeof data !== "object" ||
            data === null ||
            Array.isArray(data)
          ) {
            throw new Error("Invalid payload: expected JSON object.");
          }
          client.lastActivity = Date.now();

          switch (data.type) {
            case "message":
              await this.handleChatMessage(client, data.text, data.sessionId);
              break;

            case "ping":
              this.send(ws, { type: "pong", serverTime: Date.now() });
              break;

            case "admin":
              if (client.isAdmin) {
                await this.handleAdminCommand(client, data);
              } else {
                this.send(ws, {
                  type: "error",
                  message: "Not authorized for admin commands.",
                });
              }
              break;

            case "subscribe":
              if (data.event && typeof data.event === "string") {
                client.subscriptions.add(data.event);
                this.send(ws, { type: "subscribed", event: data.event });
                log.engine.info(
                  `[ControlPlane] Client ${client.id} subscribed to ${data.event}`,
                );
              } else {
                this.send(ws, {
                  type: "error",
                  message: "subscribe requires 'event' string",
                });
              }
              break;

            case "unsubscribe":
              if (data.event && typeof data.event === "string") {
                client.subscriptions.delete(data.event);
                this.send(ws, { type: "unsubscribed", event: data.event });
                log.engine.info(
                  `[ControlPlane] Client ${client.id} unsubscribed from ${data.event}`,
                );
              } else {
                this.send(ws, {
                  type: "error",
                  message: "unsubscribe requires 'event' string",
                });
              }
              break;

            default:
              this.send(ws, {
                type: "error",
                message: `Unknown message type: ${data.type}`,
              });
          }
        } catch (err) {
          this.send(ws, {
            type: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      });

      ws.on("close", () => {
        this.adapter.removeClient(clientId);
        log.engine.info(`[ControlPlane] Client disconnected: ${clientId}`);
      });

      ws.on("error", (err) => {
        log.engine.warn(`[ControlPlane] Error for ${clientId}: ${err.message}`);
      });
    });
  }

  // ─── Chat Message Handler ─────────────────────────────────────

  private async handleChatMessage(
    client: ConnectedClient,
    text: string,
    customSessionId?: string,
  ): Promise<void> {
    if (!text) {
      this.send(client.ws, { type: "error", message: "text is required" });
      return;
    }

    client.messageCount++;
    const sessionId = customSessionId || client.sessionId;

    const response = await this.gateway.handle(
      {
        id: makeMessageId(),
        channelId: "websocket",
        userId: client.id,
        sessionId,
        text,
      },
      {
        onProgress: async (msg: string) => {
          if (client.ws.readyState === client.ws.OPEN) {
            this.send(client.ws, { type: "progress", text: msg });
          }
        },
        onStreamEvent: async (event: StreamEvent) => {
          if (client.ws.readyState === client.ws.OPEN) {
            this.send(client.ws, { type: "stream", event });
          }
        },
      },
    );

    if (client.ws.readyState === client.ws.OPEN) {
      this.send(client.ws, { type: "response", ...response });
    }
  }

  // ─── Admin Commands ───────────────────────────────────────────

  private async handleAdminCommand(
    client: ConnectedClient,
    data: Record<string, unknown>,
  ): Promise<void> {
    const command = data.command as string;

    switch (command) {
      case "status": {
        const owl = this.gateway.getOwl();
        this.send(client.ws, {
          type: "admin_response",
          command: "status",
          data: {
            uptime: Math.round((Date.now() - this.startTime) / 1000),
            owl: { name: owl.persona.name, emoji: owl.persona.emoji },
            connectedClients: this.adapter.getClientCount(),
            serverTime: new Date().toISOString(),
          },
        });
        break;
      }

      case "clients": {
        const clients = [...this.adapter.getClients().values()].map((c) => ({
          id: c.id,
          sessionId: c.sessionId,
          connectedAt: c.connectedAt,
          lastActivity: c.lastActivity,
          messageCount: c.messageCount,
          isAdmin: c.isAdmin,
          isConnected: c.ws.readyState === c.ws.OPEN,
        }));
        this.send(client.ws, {
          type: "admin_response",
          command: "clients",
          data: clients,
        });
        break;
      }

      case "sessions": {
        const sessions = await this.sessionStore.listSessions();
        this.send(client.ws, {
          type: "admin_response",
          command: "sessions",
          data: sessions.slice(0, 50).map((s) => ({
            id: s.id,
            messageCount: s.messages.length,
            startedAt: s.metadata.startedAt,
          })),
        });
        break;
      }

      case "broadcast": {
        const message = data.message as string;
        if (!message) {
          this.send(client.ws, {
            type: "error",
            message: "broadcast requires message",
          });
          return;
        }
        await this.gateway.broadcastProactive(message);
        this.send(client.ws, {
          type: "admin_response",
          command: "broadcast",
          data: { sent: true, clients: this.adapter.getClientCount() },
        });
        break;
      }

      case "kick": {
        const targetId = data.clientId as string;
        if (!targetId) {
          this.send(client.ws, {
            type: "error",
            message: "kick requires clientId",
          });
          return;
        }
        const target = this.adapter.getClient(targetId);
        if (target) {
          this.send(target.ws, {
            type: "error",
            message: "You have been disconnected by an admin.",
          });
          target.ws.close();
          this.adapter.removeClient(targetId);
          this.send(client.ws, {
            type: "admin_response",
            command: "kick",
            data: { kicked: targetId },
          });
        } else {
          this.send(client.ws, {
            type: "error",
            message: `Client ${targetId} not found.`,
          });
        }
        break;
      }

      case "end_session": {
        const sessionId = data.sessionId as string;
        if (!sessionId) {
          this.send(client.ws, {
            type: "error",
            message: "end_session requires sessionId",
          });
          return;
        }
        await this.gateway.endSession(sessionId);
        this.send(client.ws, {
          type: "admin_response",
          command: "end_session",
          data: { ended: sessionId },
        });
        break;
      }

      default:
        this.send(client.ws, {
          type: "error",
          message: `Unknown admin command: ${command}. Available: status, clients, sessions, broadcast, kick, end_session`,
        });
    }
  }

  // ─── Helpers ──────────────────────────────────────────────────

  private send(ws: WebSocket, data: Record<string, unknown>): void {
    if (ws.readyState === ws.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }

  /**
   * Get the HTTP server instance (for external use, e.g. attaching more middleware).
   */
  getHttpServer(): HTTPServer {
    return this.httpServer;
  }

  /**
   * Get the adapter (for external use, e.g. proactive messaging).
   */
  getAdapter(): ControlPlaneAdapter {
    return this.adapter;
  }

  /**
   * Start the server.
   */
  start(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.httpServer.on("error", (err: NodeJS.ErrnoException) => {
        if (err.code === "EADDRINUSE") {
          console.warn(
            `\n⚠️  Port ${this.port} already in use — web control plane skipped. Kill the old process or change the port in config.`,
          );
          resolve(); // non-fatal: continue without web server
        } else {
          reject(err);
        }
      });
      this.httpServer.listen(this.port, () => {
        console.log(
          `\n🌐 StackOwl Control Plane running at http://localhost:${this.port}`,
        );
        console.log(`   WebSocket: ws://localhost:${this.port}`);
        console.log(`   REST API:  http://localhost:${this.port}/api/status`);
        resolve();
      });
    });
  }

  /**
   * Graceful shutdown.
   */
  stop(): void {
    this.adapter.stop();
    this.wss.close();
    this.httpServer.close();
  }
}

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
import { v4 as uuidv4 } from "uuid";
import type { OwlRegistry } from "../owls/registry.js";
import type { PelletStore } from "../pellets/store.js";
import type { SessionStore } from "../memory/store.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ChannelAdapter, GatewayResponse } from "../gateway/types.js";
import type { StreamEvent } from "../providers/base.js";
import { makeSessionId, makeMessageId, type OwlGateway } from "../gateway/core.js";
import { ParliamentOrchestrator } from "../parliament/orchestrator.js";
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
    const msg = JSON.stringify({ type: "broadcast", from: "system", ...response });
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
  }

  // ─── Express Middleware ────────────────────────────────────────

  private setupMiddleware(): void {
    this.app.use(express.json());

    // CORS for dev
    this.app.use((_req, res, next) => {
      res.header("Access-Control-Allow-Origin", "*");
      res.header("Access-Control-Allow-Headers", "Content-Type, Authorization");
      res.header("Access-Control-Allow-Methods", "GET, POST, DELETE");
      next();
    });

    // Serve static web UI
    const webDir = join(process.cwd(), "src", "web");
    this.app.use(express.static(webDir));
  }

  // ─── REST Routes ──────────────────────────────────────────────

  private setupRESTRoutes(): void {
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
        provider, this.config, this.pelletStore,
        this.gateway.getToolRegistry(),
      );

      const participants = owlNames
        ? owlNames.map((n: string) => this.owlRegistry.get(n)).filter(Boolean)
        : this.owlRegistry.listOwls().slice(0, 3);

      if (participants.length < 2) {
        res.status(400).json({ error: "At least 2 owls required" });
        return;
      }

      try {
        const session = await orchestrator.convene({ topic, participants, contextMessages: [] });
        res.json({ report: orchestrator.formatSessionMarkdown(session) });
      } catch {
        res.status(500).json({ error: "Parliament session failed" });
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
      };

      this.adapter.addClient(client);
      log.engine.info(`[ControlPlane] Client connected: ${clientId}${isAdmin ? " (admin)" : ""}`);

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
                this.send(ws, { type: "error", message: "Not authorized for admin commands." });
              }
              break;

            default:
              this.send(ws, { type: "error", message: `Unknown message type: ${data.type}` });
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
        onFile: async (filePath: string, caption?: string) => {
          if (client.ws.readyState === client.ws.OPEN) {
            this.send(client.ws, { type: "file", path: filePath, caption });
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
        this.send(client.ws, { type: "admin_response", command: "clients", data: clients });
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
          this.send(client.ws, { type: "error", message: "broadcast requires message" });
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
          this.send(client.ws, { type: "error", message: "kick requires clientId" });
          return;
        }
        const target = this.adapter.getClient(targetId);
        if (target) {
          this.send(target.ws, { type: "error", message: "You have been disconnected by an admin." });
          target.ws.close();
          this.adapter.removeClient(targetId);
          this.send(client.ws, { type: "admin_response", command: "kick", data: { kicked: targetId } });
        } else {
          this.send(client.ws, { type: "error", message: `Client ${targetId} not found.` });
        }
        break;
      }

      case "end_session": {
        const sessionId = data.sessionId as string;
        if (!sessionId) {
          this.send(client.ws, { type: "error", message: "end_session requires sessionId" });
          return;
        }
        await this.gateway.endSession(sessionId);
        this.send(client.ws, { type: "admin_response", command: "end_session", data: { ended: sessionId } });
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
    return new Promise((resolve) => {
      this.httpServer.listen(this.port, () => {
        console.log(`\n🌐 StackOwl Control Plane running at http://localhost:${this.port}`);
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

/**
 * StackOwl — WebSocket Channel Adapter
 *
 * Real-time bidirectional communication with block streaming.
 * Attaches to an existing HTTP server (from Express).
 *
 * Protocol:
 *   Client → Server: { type: "message", text: string, sessionId?: string }
 *   Server → Client: { type: "response", ...GatewayResponse }
 *                   | { type: "stream", event: StreamEvent }
 *                   | { type: "progress", text: string }
 */

import { WebSocketServer, type WebSocket } from "ws";
import type { Server as HTTPServer } from "node:http";
import type { StreamEvent } from "../../providers/base.js";
import { makeSessionId, makeMessageId, type OwlGateway } from "../core.js";
import { log } from "../../logger.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";

export class WebSocketAdapter implements ChannelAdapter {
  readonly id = "websocket";
  readonly name = "WebSocket";

  private wss: WebSocketServer | null = null;
  private clients: Map<WebSocket, string> = new Map(); // ws → userId

  constructor(
    private gateway: OwlGateway,
    private httpServer: HTTPServer,
  ) {}

  async sendToUser(userId: string, response: GatewayResponse): Promise<void> {
    for (const [ws, uid] of this.clients) {
      if (uid === userId && ws.readyState === ws.OPEN) {
        ws.send(JSON.stringify({ type: "response", ...response }));
      }
    }
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    const msg = JSON.stringify({ type: "response", ...response });
    for (const [ws] of this.clients) {
      if (ws.readyState === ws.OPEN) {
        ws.send(msg);
      }
    }
  }

  async start(): Promise<void> {
    this.wss = new WebSocketServer({ server: this.httpServer });

    this.wss.on("connection", (ws) => {
      const userId = `ws_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      this.clients.set(ws, userId);
      log.engine.info(`[WS] Client connected: ${userId}`);

      ws.on("message", async (raw) => {
        try {
          const data = JSON.parse(raw.toString());
          if (data.type === "message" && data.text) {
            await this.handleMessage(ws, userId, data.text, data.sessionId);
          }
        } catch (err) {
          ws.send(
            JSON.stringify({
              type: "error",
              message: err instanceof Error ? err.message : String(err),
            }),
          );
        }
      });

      ws.on("close", () => {
        this.clients.delete(ws);
        log.engine.info(`[WS] Client disconnected: ${userId}`);
      });

      ws.on("error", (err) => {
        log.engine.warn(`[WS] Error for ${userId}: ${err.message}`);
      });

      // Send welcome
      ws.send(
        JSON.stringify({
          type: "connected",
          userId,
          owl: {
            name: this.gateway.getOwl().persona.name,
            emoji: this.gateway.getOwl().persona.emoji,
          },
        }),
      );
    });

    log.engine.info("[WS] WebSocket adapter started");
  }

  stop(): void {
    if (this.wss) {
      for (const [ws] of this.clients) {
        ws.close();
      }
      this.wss.close();
      this.wss = null;
    }
    log.engine.info("[WS] WebSocket adapter stopped");
  }

  private async handleMessage(
    ws: WebSocket,
    userId: string,
    text: string,
    customSessionId?: string,
  ): Promise<void> {
    const sessionId =
      customSessionId || makeSessionId(this.id, userId);

    const response = await this.gateway.handle(
      {
        id: makeMessageId(),
        channelId: this.id,
        userId,
        sessionId,
        text,
      },
      {
        onProgress: async (msg: string) => {
          if (ws.readyState === ws.OPEN) {
            ws.send(JSON.stringify({ type: "progress", text: msg }));
          }
        },
        onStreamEvent: async (event: StreamEvent) => {
          if (ws.readyState === ws.OPEN) {
            ws.send(JSON.stringify({ type: "stream", event }));
          }
        },
        onFile: async (filePath: string, caption?: string) => {
          if (ws.readyState === ws.OPEN) {
            ws.send(
              JSON.stringify({ type: "file", path: filePath, caption }),
            );
          }
        },
      },
    );

    if (ws.readyState === ws.OPEN) {
      ws.send(JSON.stringify({ type: "response", ...response }));
    }
  }
}

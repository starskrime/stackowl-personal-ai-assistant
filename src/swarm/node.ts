import { createServer, type Server } from 'node:http';
import { WebSocketServer, WebSocket } from 'ws';
import { randomUUID } from 'node:crypto';
import { Logger } from '../logger.js';
import type {
  SwarmConfig,
  SwarmNode,
  SwarmMessage,
  NodeCapability,
  NodeStatus,
} from './types.js';

const log = new Logger('SWARM');

interface PeerEntry {
  ws: WebSocket;
  node: SwarmNode;
}

export class LocalSwarmNode {
  private server: Server | null = null;
  private wss: WebSocketServer | null = null;
  private peers = new Map<string, PeerEntry>();
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private status: NodeStatus = 'idle';
  private currentLoad = 0;
  private onTaskRequest?: (description: string) => Promise<string>;

  constructor(
    private config: SwarmConfig,
    private localCapabilities: NodeCapability[],
  ) {}

  async start(): Promise<void> {
    this.server = createServer();
    this.wss = new WebSocketServer({ server: this.server });

    this.wss.on('connection', (ws: WebSocket) => {
      log.info(`Incoming peer connection`);

      ws.on('message', (raw: Buffer) => {
        try {
          const message = JSON.parse(raw.toString()) as SwarmMessage;
          this.handleMessage(ws, message);
        } catch (err) {
          log.error(`Failed to parse incoming message: ${err}`);
        }
      });

      ws.on('close', () => {
        for (const [id, peer] of this.peers) {
          if (peer.ws === ws) {
            log.info(`Peer disconnected: ${peer.node.name} (${id})`);
            this.peers.delete(id);
            break;
          }
        }
      });

      ws.on('error', (err) => {
        log.error(`WebSocket error: ${err.message}`);
      });

      const greeting: SwarmMessage = {
        type: 'capability_response',
        sourceNode: this.config.nodeId,
        payload: this.getInfo(),
        timestamp: Date.now(),
      };
      ws.send(JSON.stringify(greeting));
    });

    await new Promise<void>((resolve, reject) => {
      this.server!.listen(this.config.port, () => {
        log.info(`Swarm node listening on port ${this.config.port}`);
        resolve();
      });
      this.server!.on('error', reject);
    });

    this.heartbeatTimer = setInterval(() => {
      this.broadcastHeartbeat();
    }, this.config.heartbeatIntervalMs);
  }

  async stop(): Promise<void> {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }

    for (const [, peer] of this.peers) {
      peer.ws.close();
    }
    this.peers.clear();

    if (this.wss) {
      this.wss.close();
      this.wss = null;
    }

    if (this.server) {
      await new Promise<void>((resolve) => {
        this.server!.close(() => resolve());
      });
      this.server = null;
    }

    log.info('Swarm node stopped');
  }

  getInfo(): SwarmNode {
    return {
      id: this.config.nodeId,
      name: this.config.nodeName,
      host: '0.0.0.0',
      port: this.config.port,
      capabilities: this.localCapabilities,
      status: this.status,
      lastSeen: new Date().toISOString(),
      latencyMs: 0,
      currentLoad: this.currentLoad,
      platform: process.platform,
    };
  }

  setTaskHandler(handler: (description: string) => Promise<string>): void {
    this.onTaskRequest = handler;
  }

  getPeers(): Map<string, PeerEntry> {
    return this.peers;
  }

  registerPeer(id: string, ws: WebSocket, node: SwarmNode): void {
    this.peers.set(id, { ws, node });
  }

  private handleMessage(ws: WebSocket, message: SwarmMessage): void {
    switch (message.type) {
      case 'capability_query': {
        const response: SwarmMessage = {
          type: 'capability_response',
          sourceNode: this.config.nodeId,
          targetNode: message.sourceNode,
          payload: this.getInfo(),
          timestamp: Date.now(),
        };
        ws.send(JSON.stringify(response));
        break;
      }

      case 'capability_response': {
        const nodeInfo = message.payload as SwarmNode;
        this.peers.set(message.sourceNode, { ws, node: nodeInfo });
        log.info(`Registered peer: ${nodeInfo.name} (${message.sourceNode})`);
        break;
      }

      case 'heartbeat': {
        const heartbeatNode = message.payload as SwarmNode;
        const peer = this.peers.get(message.sourceNode);
        if (peer) {
          peer.node = heartbeatNode;
        } else {
          this.peers.set(message.sourceNode, { ws, node: heartbeatNode });
        }
        break;
      }

      case 'task_request': {
        const task = message.payload as { id: string; description: string };
        this.handleTaskRequest(ws, message.sourceNode, task);
        break;
      }

      case 'task_result': {
        break;
      }
    }
  }

  private async handleTaskRequest(
    ws: WebSocket,
    sourceNode: string,
    task: { id: string; description: string },
  ): Promise<void> {
    log.info(`Received task from ${sourceNode}: ${task.description}`);
    this.status = 'busy';
    this.currentLoad = 0.8;

    let result: string;
    let error: string | undefined;

    try {
      if (this.onTaskRequest) {
        result = await this.onTaskRequest(task.description);
      } else {
        result = `Task "${task.description}" acknowledged but no handler registered`;
      }
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
      result = '';
    }

    this.status = 'idle';
    this.currentLoad = 0;

    const response: SwarmMessage = {
      type: 'task_result',
      sourceNode: this.config.nodeId,
      targetNode: sourceNode,
      payload: {
        taskId: task.id,
        result,
        error,
        completedAt: new Date().toISOString(),
      },
      timestamp: Date.now(),
    };

    ws.send(JSON.stringify(response));
  }

  private broadcastHeartbeat(): void {
    const heartbeat: SwarmMessage = {
      type: 'heartbeat',
      sourceNode: this.config.nodeId,
      payload: this.getInfo(),
      timestamp: Date.now(),
    };

    const data = JSON.stringify(heartbeat);
    for (const [id, peer] of this.peers) {
      if (peer.ws.readyState === WebSocket.OPEN) {
        peer.ws.send(data);
      } else {
        this.peers.delete(id);
      }
    }
  }
}

import { WebSocket } from 'ws';
import { createSocket, type Socket } from 'node:dgram';
import { randomUUID } from 'node:crypto';
import { Logger } from '../logger.js';
import type {
  SwarmConfig,
  SwarmNode,
  SwarmTask,
  SwarmMessage,
  NodeCapability,
} from './types.js';
import { LocalSwarmNode } from './node.js';

const log = new Logger('SWARM');

export class SwarmCoordinator {
  private localNode: LocalSwarmNode;
  private tasks = new Map<string, SwarmTask>();
  private taskResolvers = new Map<string, (task: SwarmTask) => void>();
  private totalCompleted = 0;

  constructor(
    private config: SwarmConfig,
    localNode: LocalSwarmNode,
  ) {
    this.localNode = localNode;
  }

  async connectToNode(host: string, port: number): Promise<SwarmNode> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`ws://${host}:${port}`);
      const timeout = setTimeout(() => {
        ws.close();
        reject(new Error(`Connection to ${host}:${port} timed out`));
      }, 10_000);

      const connectTime = Date.now();

      ws.on('open', () => {
        const query: SwarmMessage = {
          type: 'capability_query',
          sourceNode: this.config.nodeId,
          payload: this.localNode.getInfo(),
          timestamp: Date.now(),
        };
        ws.send(JSON.stringify(query));
      });

      ws.on('message', (raw: Buffer) => {
        try {
          const message = JSON.parse(raw.toString()) as SwarmMessage;

          if (message.type === 'capability_response') {
            clearTimeout(timeout);
            const nodeInfo = message.payload as SwarmNode;
            nodeInfo.latencyMs = Date.now() - connectTime;
            nodeInfo.lastSeen = new Date().toISOString();
            this.localNode.registerPeer(message.sourceNode, ws, nodeInfo);
            log.info(`Connected to node: ${nodeInfo.name} (${host}:${port})`);
            resolve(nodeInfo);
          }

          if (message.type === 'task_result') {
            this.handleTaskResult(message);
          }

          if (message.type === 'heartbeat') {
            const heartbeatNode = message.payload as SwarmNode;
            const peers = this.localNode.getPeers();
            const peer = peers.get(message.sourceNode);
            if (peer) {
              peer.node = heartbeatNode;
            }
          }
        } catch (err) {
          log.error(`Failed to parse message from ${host}:${port}: ${err}`);
        }
      });

      ws.on('error', (err) => {
        clearTimeout(timeout);
        reject(new Error(`Connection to ${host}:${port} failed: ${err.message}`));
      });

      ws.on('close', () => {
        const peers = this.localNode.getPeers();
        for (const [id, peer] of peers) {
          if (peer.ws === ws) {
            peers.delete(id);
            break;
          }
        }
      });
    });
  }

  disconnectNode(nodeId: string): void {
    const peers = this.localNode.getPeers();
    const peer = peers.get(nodeId);
    if (peer) {
      peer.ws.close();
      peers.delete(nodeId);
      log.info(`Disconnected node: ${nodeId}`);
    }
  }

  getNodes(): SwarmNode[] {
    const nodes: SwarmNode[] = [this.localNode.getInfo()];
    for (const [, peer] of this.localNode.getPeers()) {
      nodes.push(peer.node);
    }
    return nodes;
  }

  findBestNode(requiredCapabilities: NodeCapability[]): SwarmNode | null {
    const candidates = this.getNodes()
      .filter((n) => n.status === 'online' || n.status === 'idle')
      .filter((n) =>
        requiredCapabilities.every((cap) => n.capabilities.includes(cap)),
      )
      .sort((a, b) => {
        if (a.currentLoad !== b.currentLoad) {
          return a.currentLoad - b.currentLoad;
        }
        return a.latencyMs - b.latencyMs;
      });

    return candidates[0] ?? null;
  }

  async submitTask(
    description: string,
    requiredCapabilities: NodeCapability[],
  ): Promise<SwarmTask> {
    const task: SwarmTask = {
      id: randomUUID(),
      description,
      requiredCapabilities,
      status: 'pending',
      createdAt: new Date().toISOString(),
    };

    const bestNode = this.findBestNode(requiredCapabilities);
    if (!bestNode) {
      task.status = 'failed';
      task.error = 'No capable node available';
      this.tasks.set(task.id, task);
      return task;
    }

    task.assignedNode = bestNode.id;
    task.status = 'assigned';
    this.tasks.set(task.id, task);

    if (bestNode.id === this.config.nodeId) {
      this.executeLocally(task);
    } else {
      const peers = this.localNode.getPeers();
      const peer = peers.get(bestNode.id);
      if (!peer || peer.ws.readyState !== WebSocket.OPEN) {
        task.status = 'failed';
        task.error = 'Peer connection lost';
        return task;
      }

      const message: SwarmMessage = {
        type: 'task_request',
        sourceNode: this.config.nodeId,
        targetNode: bestNode.id,
        payload: { id: task.id, description: task.description },
        timestamp: Date.now(),
      };
      peer.ws.send(JSON.stringify(message));
    }

    log.info(`Task ${task.id} assigned to ${bestNode.name}`);
    return task;
  }

  getTask(taskId: string): SwarmTask | null {
    return this.tasks.get(taskId) ?? null;
  }

  async waitForTask(taskId: string, timeoutMs?: number): Promise<SwarmTask> {
    const task = this.tasks.get(taskId);
    if (!task) {
      throw new Error(`Task ${taskId} not found`);
    }

    if (task.status === 'completed' || task.status === 'failed') {
      return task;
    }

    const effectiveTimeout = timeoutMs ?? this.config.taskTimeoutMs;

    return new Promise<SwarmTask>((resolve) => {
      const timeout = setTimeout(() => {
        task.status = 'failed';
        task.error = 'Task timed out';
        this.taskResolvers.delete(taskId);
        resolve(task);
      }, effectiveTimeout);

      this.taskResolvers.set(taskId, (completed) => {
        clearTimeout(timeout);
        this.taskResolvers.delete(taskId);
        resolve(completed);
      });
    });
  }

  getSwarmStatus(): {
    nodes: SwarmNode[];
    activeTasks: SwarmTask[];
    totalCompleted: number;
  } {
    const activeTasks: SwarmTask[] = [];
    for (const [, task] of this.tasks) {
      if (
        task.status === 'pending' ||
        task.status === 'assigned' ||
        task.status === 'running'
      ) {
        activeTasks.push(task);
      }
    }

    return {
      nodes: this.getNodes(),
      activeTasks,
      totalCompleted: this.totalCompleted,
    };
  }

  async discover(): Promise<SwarmNode[]> {
    return new Promise<SwarmNode[]>((resolve) => {
      const discovered: SwarmNode[] = [];
      let socket: Socket;

      try {
        socket = createSocket('udp4');
      } catch (err) {
        log.error(`Failed to create UDP socket: ${err}`);
        resolve([]);
        return;
      }

      socket.on('error', (err) => {
        log.error(`Discovery error: ${err.message}`);
        socket.close();
        resolve(discovered);
      });

      socket.on('message', (msg, rinfo) => {
        try {
          const message = JSON.parse(msg.toString()) as SwarmMessage;
          if (
            message.type === 'capability_response' &&
            message.sourceNode !== this.config.nodeId
          ) {
            const nodeInfo = message.payload as SwarmNode;
            nodeInfo.host = rinfo.address;
            discovered.push(nodeInfo);
          }
        } catch {
          // ignore malformed responses
        }
      });

      socket.bind(() => {
        socket.setBroadcast(true);

        const query: SwarmMessage = {
          type: 'capability_query',
          sourceNode: this.config.nodeId,
          payload: this.localNode.getInfo(),
          timestamp: Date.now(),
        };

        const buf = Buffer.from(JSON.stringify(query));
        socket.send(buf, 0, buf.length, this.config.discoveryPort, '255.255.255.255', (err) => {
          if (err) {
            log.error(`Discovery broadcast failed: ${err.message}`);
          }
        });
      });

      setTimeout(async () => {
        socket.close();

        for (const node of discovered) {
          try {
            await this.connectToNode(node.host, node.port);
          } catch (err) {
            log.warn(`Failed to connect to discovered node ${node.name}: ${err}`);
          }
        }

        resolve(discovered);
      }, 3000);
    });
  }

  private async executeLocally(task: SwarmTask): Promise<void> {
    task.status = 'running';

    try {
      const handler = (this.localNode as any).onTaskRequest as
        | ((desc: string) => Promise<string>)
        | undefined;

      if (handler) {
        task.result = await handler(task.description);
      } else {
        task.result = `Task "${task.description}" completed locally (no handler)`;
      }
      task.status = 'completed';
      task.completedAt = new Date().toISOString();
      this.totalCompleted++;
    } catch (err) {
      task.status = 'failed';
      task.error = err instanceof Error ? err.message : String(err);
      task.completedAt = new Date().toISOString();
    }

    const resolver = this.taskResolvers.get(task.id);
    if (resolver) {
      resolver(task);
    }
  }

  private handleTaskResult(message: SwarmMessage): void {
    const result = message.payload as {
      taskId: string;
      result?: string;
      error?: string;
      completedAt: string;
    };

    const task = this.tasks.get(result.taskId);
    if (!task) return;

    if (result.error) {
      task.status = 'failed';
      task.error = result.error;
    } else {
      task.status = 'completed';
      task.result = result.result;
      this.totalCompleted++;
    }
    task.completedAt = result.completedAt;

    const resolver = this.taskResolvers.get(result.taskId);
    if (resolver) {
      resolver(task);
    }
  }
}

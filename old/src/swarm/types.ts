export type NodeCapability =
  | "desktop_automation"
  | "gpu_compute"
  | "web_scraping"
  | "file_storage"
  | "always_on"
  | "location_aware"
  | "macos_native"
  | "linux_native"
  | "docker";

export type NodeStatus = "online" | "offline" | "busy" | "idle";

export interface SwarmNode {
  id: string;
  name: string;
  host: string;
  port: number;
  capabilities: NodeCapability[];
  status: NodeStatus;
  lastSeen: string;
  latencyMs: number;
  currentLoad: number;
  platform: string;
}

export interface SwarmTask {
  id: string;
  description: string;
  requiredCapabilities: NodeCapability[];
  assignedNode?: string;
  status: "pending" | "assigned" | "running" | "completed" | "failed";
  result?: string;
  error?: string;
  createdAt: string;
  completedAt?: string;
}

export interface SwarmMessage {
  type:
    | "task_request"
    | "task_result"
    | "heartbeat"
    | "capability_query"
    | "capability_response";
  sourceNode: string;
  targetNode?: string;
  payload: unknown;
  timestamp: number;
}

export interface SwarmConfig {
  nodeId: string;
  nodeName: string;
  port: number;
  discoveryPort: number;
  heartbeatIntervalMs: number;
  taskTimeoutMs: number;
}

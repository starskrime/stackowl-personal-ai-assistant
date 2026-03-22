/**
 * StackOwl — Infrastructure Profile Types
 *
 * Persistent model of the user's infrastructure learned from conversations.
 */

export interface InfraService {
  name: string;
  type: "app" | "database" | "cache" | "queue" | "storage" | "cdn" | "auth" | "monitoring" | "ci" | "other";
  provider?: string;        // "aws", "gcp", "azure", "self-hosted", etc.
  url?: string;             // endpoint or dashboard URL
  port?: number;
  credentials?: string;     // reference key (never store actual secrets)
  tags: string[];
  notes: string;
  discoveredAt: number;
  lastMentioned: number;
}

export interface InfraConnection {
  from: string;   // service name
  to: string;     // service name
  type: "depends" | "calls" | "reads" | "writes" | "proxies";
  description?: string;
}

export interface InfraEnvironment {
  name: string;          // "production", "staging", "dev", "local"
  services: InfraService[];
  connections: InfraConnection[];
}

export interface InfraProfile {
  version: number;
  environments: InfraEnvironment[];
  lastUpdated: number;
  metadata: {
    totalServices: number;
    primaryProvider?: string;
    techStack: string[];     // detected languages/frameworks
  };
}

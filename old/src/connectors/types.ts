/**
 * StackOwl — Connector Types
 *
 * Predefined MCP server configurations for common DevOps tools.
 */

export interface ConnectorPreset {
  /** Unique preset identifier */
  id: string;
  /** Human-readable name */
  name: string;
  /** What this connector does */
  description: string;
  /** Category for grouping */
  category:
    | "cloud"
    | "database"
    | "monitoring"
    | "ci"
    | "vcs"
    | "communication"
    | "infra"
    | "custom";
  /** MCP transport type */
  transport: "stdio" | "sse";
  /** Command to run (stdio) */
  command?: string;
  /** NPX package or docker image */
  package?: string;
  /** Command arguments */
  args?: string[];
  /** URL for SSE transport */
  url?: string;
  /** Required environment variables (user must provide values) */
  requiredEnv: string[];
  /** Optional environment variables */
  optionalEnv?: string[];
  /** Icon/emoji for display */
  icon: string;
}

export interface ConnectorInstance {
  presetId: string;
  name: string;
  enabled: boolean;
  env: Record<string, string>;
  configuredAt: number;
  lastHealthCheck?: number;
  healthy?: boolean;
}

export interface ConnectorConfig {
  instances: ConnectorInstance[];
}

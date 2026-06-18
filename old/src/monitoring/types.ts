/**
 * StackOwl — Passive Monitoring Types
 *
 * Health checks and alerting for user infrastructure.
 */

export type CheckType = "http" | "tcp" | "dns" | "command";
export type CheckStatus = "healthy" | "degraded" | "down" | "unknown";
export type AlertSeverity = "info" | "warning" | "critical";

export interface HealthCheck {
  id: string;
  name: string;
  type: CheckType;
  /** Target URL, host:port, or command */
  target: string;
  /** Check interval in seconds. Default: 300 (5 min) */
  intervalSeconds: number;
  /** Timeout for each check in ms. Default: 10000 */
  timeoutMs: number;
  /** Number of consecutive failures before alerting. Default: 2 */
  failThreshold: number;
  /** Associated infrastructure service name */
  serviceName?: string;
  enabled: boolean;
  /** Tags for grouping */
  tags: string[];
}

export interface CheckResult {
  checkId: string;
  status: CheckStatus;
  responseTimeMs: number;
  statusCode?: number;
  error?: string;
  timestamp: number;
}

export interface Alert {
  id: string;
  checkId: string;
  severity: AlertSeverity;
  message: string;
  previousStatus: CheckStatus;
  currentStatus: CheckStatus;
  timestamp: number;
  acknowledged: boolean;
}

export interface MonitoringState {
  checks: HealthCheck[];
  lastResults: Record<string, CheckResult>;
  consecutiveFailures: Record<string, number>;
  alerts: Alert[];
}

/**
 * StackOwl Products — Shared Types
 */

export interface ServerConfig {
  port: number;
  host?: string;
  corsOrigins?: string[];
}

export interface ApiError {
  error: string;
  code?: string;
  details?: unknown;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  uptime: number;
}

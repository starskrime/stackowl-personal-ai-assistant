/**
 * StackOwl — Health Checker
 *
 * Runs periodic health checks against configured endpoints.
 * Integrates with the heartbeat system for proactive alerting.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join, dirname } from "node:path";
import { existsSync } from "node:fs";
import type {
  HealthCheck,
  CheckResult,
  CheckStatus,
  Alert,
  MonitoringState,
} from "./types.js";
import { log } from "../logger.js";

export class HealthChecker {
  private state: MonitoringState = {
    checks: [],
    lastResults: {},
    consecutiveFailures: {},
    alerts: [],
  };
  private intervals = new Map<string, NodeJS.Timeout>();
  private filePath: string;
  private onAlert?: (alert: Alert) => void;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "monitoring-state.json");
  }

  async load(): Promise<void> {
    try {
      if (existsSync(this.filePath)) {
        const raw = await readFile(this.filePath, "utf-8");
        this.state = JSON.parse(raw);
        log.engine.info(
          `[HealthChecker] Loaded ${this.state.checks.length} check(s)`,
        );
      }
    } catch (err) {
      log.engine.warn(`[HealthChecker] Failed to load state: ${err}`);
    }
  }

  async save(): Promise<void> {
    try {
      const dir = dirname(this.filePath);
      if (!existsSync(dir)) await mkdir(dir, { recursive: true });
      // Keep only last 100 alerts
      this.state.alerts = this.state.alerts.slice(-100);
      await writeFile(
        this.filePath,
        JSON.stringify(this.state, null, 2),
        "utf-8",
      );
    } catch (err) {
      log.engine.warn(`[HealthChecker] Failed to save state: ${err}`);
    }
  }

  /**
   * Register an alert callback (e.g., for sending Telegram messages).
   */
  setAlertHandler(handler: (alert: Alert) => void): void {
    this.onAlert = handler;
  }

  /**
   * Add a health check and start monitoring.
   */
  addCheck(check: HealthCheck): void {
    const existing = this.state.checks.findIndex((c) => c.id === check.id);
    if (existing >= 0) {
      this.state.checks[existing] = check;
    } else {
      this.state.checks.push(check);
    }

    if (check.enabled) {
      this.startCheck(check);
    }
  }

  removeCheck(id: string): void {
    this.stopCheck(id);
    this.state.checks = this.state.checks.filter((c) => c.id !== id);
    delete this.state.lastResults[id];
    delete this.state.consecutiveFailures[id];
  }

  /**
   * Start all enabled checks.
   */
  startAll(): void {
    for (const check of this.state.checks) {
      if (check.enabled) {
        this.startCheck(check);
      }
    }
    log.engine.info(`[HealthChecker] Started ${this.intervals.size} check(s)`);
  }

  /**
   * Stop all checks.
   */
  stopAll(): void {
    for (const [id] of this.intervals) {
      this.stopCheck(id);
    }
  }

  private startCheck(check: HealthCheck): void {
    this.stopCheck(check.id); // Clear any existing
    const interval = setInterval(
      () => this.runCheck(check),
      check.intervalSeconds * 1000,
    );
    interval.unref?.();
    this.intervals.set(check.id, interval);

    // Run immediately
    this.runCheck(check);
  }

  private stopCheck(id: string): void {
    const interval = this.intervals.get(id);
    if (interval) {
      clearInterval(interval);
      this.intervals.delete(id);
    }
  }

  private async runCheck(check: HealthCheck): Promise<void> {
    const start = Date.now();
    let result: CheckResult;

    try {
      switch (check.type) {
        case "http":
          result = await this.checkHttp(check, start);
          break;
        case "tcp":
          result = await this.checkTcp(check, start);
          break;
        case "dns":
          result = await this.checkDns(check, start);
          break;
        case "command":
          result = await this.checkCommand(check, start);
          break;
        default:
          result = {
            checkId: check.id,
            status: "unknown",
            responseTimeMs: 0,
            error: `Unknown check type: ${check.type}`,
            timestamp: Date.now(),
          };
      }
    } catch (err) {
      result = {
        checkId: check.id,
        status: "down",
        responseTimeMs: Date.now() - start,
        error: err instanceof Error ? err.message : String(err),
        timestamp: Date.now(),
      };
    }

    this.processResult(check, result);
  }

  private async checkHttp(
    check: HealthCheck,
    start: number,
  ): Promise<CheckResult> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), check.timeoutMs);

    try {
      const response = await fetch(check.target, {
        signal: controller.signal,
        method: "HEAD",
      });

      const status: CheckStatus = response.ok
        ? "healthy"
        : response.status >= 500
          ? "down"
          : "degraded";
      return {
        checkId: check.id,
        status,
        responseTimeMs: Date.now() - start,
        statusCode: response.status,
        timestamp: Date.now(),
      };
    } finally {
      clearTimeout(timeout);
    }
  }

  private async checkTcp(
    check: HealthCheck,
    start: number,
  ): Promise<CheckResult> {
    const { createConnection } = await import("node:net");
    const [host, portStr] = check.target.split(":");
    const port = parseInt(portStr, 10);

    return new Promise((resolve) => {
      const socket = createConnection({ host, port, timeout: check.timeoutMs });

      socket.on("connect", () => {
        socket.destroy();
        resolve({
          checkId: check.id,
          status: "healthy",
          responseTimeMs: Date.now() - start,
          timestamp: Date.now(),
        });
      });

      socket.on("timeout", () => {
        socket.destroy();
        resolve({
          checkId: check.id,
          status: "down",
          responseTimeMs: Date.now() - start,
          error: "Connection timeout",
          timestamp: Date.now(),
        });
      });

      socket.on("error", (err) => {
        socket.destroy();
        resolve({
          checkId: check.id,
          status: "down",
          responseTimeMs: Date.now() - start,
          error: err.message,
          timestamp: Date.now(),
        });
      });
    });
  }

  private async checkDns(
    check: HealthCheck,
    start: number,
  ): Promise<CheckResult> {
    const { promises: dns } = await import("node:dns");
    try {
      await dns.resolve(check.target);
      return {
        checkId: check.id,
        status: "healthy",
        responseTimeMs: Date.now() - start,
        timestamp: Date.now(),
      };
    } catch (err) {
      return {
        checkId: check.id,
        status: "down",
        responseTimeMs: Date.now() - start,
        error: err instanceof Error ? err.message : String(err),
        timestamp: Date.now(),
      };
    }
  }

  private async checkCommand(
    check: HealthCheck,
    start: number,
  ): Promise<CheckResult> {
    const { exec } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const execAsync = promisify(exec);

    try {
      await execAsync(check.target, { timeout: check.timeoutMs });
      return {
        checkId: check.id,
        status: "healthy",
        responseTimeMs: Date.now() - start,
        timestamp: Date.now(),
      };
    } catch (err) {
      return {
        checkId: check.id,
        status: "down",
        responseTimeMs: Date.now() - start,
        error: err instanceof Error ? err.message : String(err),
        timestamp: Date.now(),
      };
    }
  }

  private processResult(check: HealthCheck, result: CheckResult): void {
    const previousResult = this.state.lastResults[check.id];
    this.state.lastResults[check.id] = result;

    if (result.status === "healthy") {
      // Reset failure counter
      const wasDown =
        (this.state.consecutiveFailures[check.id] ?? 0) >= check.failThreshold;
      this.state.consecutiveFailures[check.id] = 0;

      if (wasDown && previousResult) {
        // Recovery alert
        const alert: Alert = {
          id: `alert-${Date.now()}`,
          checkId: check.id,
          severity: "info",
          message: `${check.name} recovered (was ${previousResult.status})`,
          previousStatus: previousResult.status,
          currentStatus: "healthy",
          timestamp: Date.now(),
          acknowledged: false,
        };
        this.state.alerts.push(alert);
        this.onAlert?.(alert);
        log.engine.info(`[HealthChecker] ${check.name} recovered`);
      }
    } else {
      const failures = (this.state.consecutiveFailures[check.id] ?? 0) + 1;
      this.state.consecutiveFailures[check.id] = failures;

      if (failures === check.failThreshold) {
        const alert: Alert = {
          id: `alert-${Date.now()}`,
          checkId: check.id,
          severity: result.status === "down" ? "critical" : "warning",
          message: `${check.name} is ${result.status}: ${result.error ?? "no details"}`,
          previousStatus: previousResult?.status ?? "unknown",
          currentStatus: result.status,
          timestamp: Date.now(),
          acknowledged: false,
        };
        this.state.alerts.push(alert);
        this.onAlert?.(alert);
        log.engine.warn(
          `[HealthChecker] ALERT: ${check.name} is ${result.status}`,
        );
      }
    }
  }

  getChecks(): HealthCheck[] {
    return [...this.state.checks];
  }

  getLastResult(checkId: string): CheckResult | undefined {
    return this.state.lastResults[checkId];
  }

  getActiveAlerts(): Alert[] {
    return this.state.alerts.filter((a) => !a.acknowledged);
  }

  acknowledgeAlert(alertId: string): void {
    const alert = this.state.alerts.find((a) => a.id === alertId);
    if (alert) alert.acknowledged = true;
  }

  /** Generate a status summary for injection into prompts */
  toContextString(): string {
    const active = this.getActiveAlerts();
    if (active.length === 0) return "";

    const lines = ["## Active Alerts"];
    for (const alert of active) {
      const check = this.state.checks.find((c) => c.id === alert.checkId);
      lines.push(
        `- [${alert.severity.toUpperCase()}] ${check?.name ?? alert.checkId}: ${alert.message}`,
      );
    }
    return lines.join("\n");
  }
}

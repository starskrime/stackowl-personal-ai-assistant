/**
 * StackOwl — Logger (back-compat re-export)
 *
 * All logging is now implemented in src/infra/observability/.
 * This file exists solely to preserve the import path for the 205
 * files that import from "../logger" or "../../logger" etc.
 *
 * Exports (identical API to the old logger.ts):
 *   log         — module singletons (telegram, cli, engine, …)
 *   Logger      — the Logger class
 *   LogLevel    — "debug" | "info" | "warn" | "error" | "fatal"
 *   initFileLog — call once at startup with workspacePath
 */

export { log, initFileLog, Logger } from "./infra/observability/compat.js";
export type { LogLevel } from "./infra/observability/schema.js";

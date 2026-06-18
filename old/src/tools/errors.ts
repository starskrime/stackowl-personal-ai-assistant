/**
 * StackOwl — Structured Tool Errors
 *
 * Typed error hierarchy replacing catch-all string returns.
 * Each error carries structured fields so the engine can make
 * intelligent retry/escalation decisions without string parsing.
 */

export class ToolError extends Error {
  readonly toolName: string;
  readonly retryable: boolean;
  readonly errorCode: string;

  constructor(
    toolName: string,
    message: string,
    opts: { retryable?: boolean; errorCode?: string } = {},
  ) {
    super(message);
    this.name = "ToolError";
    this.toolName = toolName;
    this.retryable = opts.retryable ?? true;
    this.errorCode = opts.errorCode ?? "TOOL_ERROR";
  }
}

export class ToolNotFoundError extends ToolError {
  constructor(toolName: string) {
    super(toolName, `Tool "${toolName}" not found in registry.`, {
      retryable: false,
      errorCode: "TOOL_NOT_FOUND",
    });
    this.name = "ToolNotFoundError";
  }
}

export class ToolValidationError extends ToolError {
  readonly violations: string[];

  constructor(toolName: string, violations: string[]) {
    super(
      toolName,
      `Validation failed for "${toolName}": ${violations.join("; ")}`,
      { retryable: false, errorCode: "VALIDATION_FAILED" },
    );
    this.name = "ToolValidationError";
    this.violations = violations;
  }
}

export class ToolPermissionError extends ToolError {
  readonly category: string;

  constructor(toolName: string, category: string) {
    super(
      toolName,
      `Permission denied: tool "${toolName}" (category: ${category}) is not allowed.`,
      { retryable: false, errorCode: "PERMISSION_DENIED" },
    );
    this.name = "ToolPermissionError";
    this.category = category;
  }
}

export class ToolExecutionError extends ToolError {
  readonly exitCode?: number;
  readonly stderr?: string;

  constructor(
    toolName: string,
    message: string,
    opts: { exitCode?: number; stderr?: string; retryable?: boolean } = {},
  ) {
    super(toolName, message, {
      retryable: opts.retryable ?? true,
      errorCode: "EXECUTION_FAILED",
    });
    this.name = "ToolExecutionError";
    this.exitCode = opts.exitCode;
    this.stderr = opts.stderr;
  }
}

export class ToolTimeoutError extends ToolError {
  readonly timeoutMs: number;

  constructor(toolName: string, timeoutMs: number) {
    super(toolName, `Tool "${toolName}" timed out after ${timeoutMs}ms.`, {
      retryable: true,
      errorCode: "TIMEOUT",
    });
    this.name = "ToolTimeoutError";
    this.timeoutMs = timeoutMs;
  }
}

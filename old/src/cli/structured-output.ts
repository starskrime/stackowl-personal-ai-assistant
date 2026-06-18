/**
 * StackOwl — Structured JSON Output
 *
 * Provides structured JSON output for non-interactive CLI commands.
 * Suppresses TUI and formats all output as clean JSON.
 */

export type OutputStatus = "ok" | "error";

export interface StructuredOutput {
  status: OutputStatus;
  content?: string;
  owlName?: string;
  timestamp: string;
  usage?: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  };
  error?: string;
  code?: string;
}

export interface StructuredCommandOutput extends StructuredOutput {
  command: string;
  durationMs?: number;
}

const isJsonMode = (): boolean => {
  return process.env.STACKOWL_JSON === "true" ||
    process.argv.includes("--json") ||
    process.argv.includes("--quiet");
};

const isTuiSuppressed = (): boolean => {
  return process.env.STACKOWL_NO_TUI === "true" ||
    isJsonMode();
};

export class StructuredOutputManager {
  private jsonMode: boolean;
  private outputs: StructuredOutput[] = [];
  private startTime: number;

  constructor() {
    this.jsonMode = isJsonMode();
    this.startTime = Date.now();
  }

  /**
   * Check if JSON output mode is active.
   */
  isActive(): boolean {
    return this.jsonMode;
  }

  /**
   * Force enable JSON mode (e.g., for piping scenarios).
   */
  enableJsonMode(): void {
    this.jsonMode = true;
  }

  /**
   * Force disable JSON mode.
   */
  disableJsonMode(): void {
    this.jsonMode = false;
  }

  /**
   * Create a success output structure.
   */
  success(content: string, options?: {
    owlName?: string;
    usage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  }): StructuredOutput {
    return {
      status: "ok",
      content,
      owlName: options?.owlName,
      timestamp: new Date().toISOString(),
      usage: options?.usage,
    };
  }

  /**
   * Create an error output structure.
   */
  error(message: string, code?: string): StructuredOutput {
    return {
      status: "error",
      error: message,
      code,
      timestamp: new Date().toISOString(),
    };
  }

  /**
   * Format a command output as structured JSON.
   */
  formatCommandOutput(command: string, output: StructuredOutput): StructuredCommandOutput {
    return {
      ...output,
      command,
      durationMs: Date.now() - this.startTime,
    };
  }

  /**
   * Output structured JSON to stdout.
   */
  print(output: StructuredOutput): void {
    if (this.jsonMode) {
      process.stdout.write(JSON.stringify(output) + "\n");
    }
  }

  /**
   * Output structured JSON to stderr.
   */
  printError(output: StructuredOutput): void {
    if (this.jsonMode) {
      process.stderr.write(JSON.stringify(output) + "\n");
    } else {
      console.error(output.error ?? output.content);
    }
  }

  /**
   * Print success and exit.
   */
  printSuccess(content: string, options?: {
    owlName?: string;
    usage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  }): never {
    const output = this.success(content, options);
    this.print(output);
    process.exit(0);
  }

  /**
   * Print error and exit with non-zero code.
   */
  printFatalError(message: string, code?: string): never {
    const output = this.error(message, code);
    this.printError(output);
    process.exit(1);
  }

  /**
   * Suppress TUI rendering when in JSON mode.
   */
  shouldSuppressTui(): boolean {
    return isTuiSuppressed();
  }

  /**
   * Get elapsed time since this manager was created.
   */
  getElapsedMs(): number {
    return Date.now() - this.startTime;
  }

  /**
   * Queue output for batch processing.
   */
  queue(output: StructuredOutput): void {
    this.outputs.push(output);
  }

  /**
   * Flush all queued outputs as JSON array.
   */
  flush(): void {
    if (this.jsonMode && this.outputs.length > 0) {
      process.stdout.write(JSON.stringify(this.outputs, null, 2) + "\n");
      this.outputs = [];
    }
  }
}

export function createStructuredOutput(): StructuredOutputManager {
  return new StructuredOutputManager();
}

export function isJsonModeEnabled(): boolean {
  return process.env.STACKOWL_JSON === "true" ||
    process.argv.includes("--json");
}

export function isQuietModeEnabled(): boolean {
  return process.env.STACKOWL_QUIET === "true" ||
    process.argv.includes("--quiet") ||
    process.argv.includes("-q");
}
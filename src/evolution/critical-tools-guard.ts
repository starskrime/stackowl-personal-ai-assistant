/**
 * StackOwl — Critical Tools Permission Gate
 *
 * Detects dangerous primitives in synthesized TypeScript tool code and requires
 * explicit user approval before the tool is registered. Grants are persisted to
 * a JSON file so the user is not prompted again for the same tool.
 *
 * Dangerous primitives that trigger the gate:
 *   - child_process (any import/usage)
 *   - eval(
 *   - new Function(
 *   - exec( / execSync(
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname } from "node:path";
import { log } from "../logger.js";

export interface ApprovalChannel {
  ask(message: string): Promise<boolean>;
}

const DANGEROUS_PATTERNS: Array<{ name: string; regex: RegExp }> = [
  { name: "child_process", regex: /child_process/u },
  { name: "eval",          regex: /\beval\s*\(/u },
  { name: "new Function",  regex: /new\s+Function\s*\(/u },
  { name: "exec",          regex: /\bexec(?:Sync)?\s*\(/u },
];

type PermissionStore = Record<string, string[]>;

export class CriticalToolsGuard {
  private grants: PermissionStore = {};
  private loaded = false;

  constructor(
    private readonly permissionsFile: string,
    private readonly channel: ApprovalChannel,
  ) {}

  /**
   * Returns names of dangerous patterns found in the given code.
   * An empty array means the code is safe.
   */
  static detectDangerousPatterns(code: string): string[] {
    return DANGEROUS_PATTERNS
      .filter(({ regex }) => regex.test(code))
      .map(({ name }) => name);
  }

  /**
   * Check whether a tool's source code is safe to register.
   *
   * - Safe code: returns true immediately.
   * - Dangerous code with existing grant: returns true without asking.
   * - Dangerous code with no grant: asks the user via the ApprovalChannel.
   *   - Approved → persists the grant, returns true.
   *   - Denied   → returns false (caller must abort registration).
   */
  async check(toolName: string, code: string): Promise<boolean> {
    log.synthesis.debug("critical-tools-guard.check: entry", { toolName, codeLen: code.length });

    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    if (patterns.length === 0) {
      log.synthesis.debug("critical-tools-guard.check: exit clean — no dangerous patterns", { toolName });
      return true;
    }

    await this.loadGrants();

    const existing = this.grants[toolName] ?? [];
    const alreadyApproved = patterns.every((p) => existing.includes(p));
    if (alreadyApproved) {
      log.synthesis.debug("critical-tools-guard.check: previously approved, skipping prompt", { toolName, patterns });
      return true;
    }

    log.synthesis.warn(
      "critical-tools-guard.check: dangerous patterns found — prompting user for approval",
      { toolName, patterns },
    );

    const message =
      `New tool "${toolName}" uses potentially dangerous capabilities: [${patterns.join(", ")}].\n` +
      `Allow this tool to be registered? (Grant is remembered for future sessions.)`;

    const granted = await this.channel.ask(message);
    log.synthesis.debug("critical-tools-guard.check: user decision", { toolName, granted });

    if (granted) {
      this.grants[toolName] = [...new Set([...existing, ...patterns])];
      await this.persistGrants();
    }

    log.synthesis.debug("critical-tools-guard.check: exit", { toolName, granted });
    return granted;
  }

  private async loadGrants(): Promise<void> {
    if (this.loaded) return;
    this.loaded = true;
    if (!existsSync(this.permissionsFile)) return;
    try {
      const raw = await readFile(this.permissionsFile, "utf-8");
      this.grants = JSON.parse(raw) as PermissionStore;
      log.synthesis.debug("critical-tools-guard.loadGrants: loaded", {
        file: this.permissionsFile,
        toolCount: Object.keys(this.grants).length,
      });
    } catch (err) {
      log.synthesis.warn(
        "critical-tools-guard.loadGrants: failed to read permissions file — starting fresh",
        err as Error,
        { file: this.permissionsFile },
      );
      this.grants = {};
    }
  }

  private async persistGrants(): Promise<void> {
    try {
      await mkdir(dirname(this.permissionsFile), { recursive: true });
      await writeFile(this.permissionsFile, JSON.stringify(this.grants, null, 2), "utf-8");
      log.synthesis.debug("critical-tools-guard.persistGrants: saved", { file: this.permissionsFile });
    } catch (err) {
      log.synthesis.error(
        "critical-tools-guard.persistGrants: failed to persist grants",
        err as Error,
        { file: this.permissionsFile },
      );
    }
  }
}

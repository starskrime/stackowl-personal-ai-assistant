/**
 * StackOwl — CLI Approval Channel
 *
 * Implements ApprovalChannel for the CLI context: prints a warning to stdout
 * and reads a y/N answer from stdin.  Used by CriticalToolsGuard when
 * TypeScript tool synthesis produces code with dangerous primitives.
 */

import * as readline from "node:readline";
import type { ApprovalChannel } from "./critical-tools-guard.js";

export const cliApprovalChannel: ApprovalChannel = {
  async ask(message: string): Promise<boolean> {
    return new Promise((resolve) => {
      const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
      });
      rl.question(`\n⚠️  ${message}\n[y/N] `, (answer) => {
        rl.close();
        resolve(answer.trim().toLowerCase() === "y");
      });
    });
  },
};

/**
 * Auto-updater — runs at startup so any launch method (npm start, stackowl binary,
 * Docker entrypoint, etc.) picks up schema migrations and code fixes automatically.
 *
 * start.sh is dev-only tooling. All production self-healing must live here.
 */

import { execSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { log } from "../logger.js";

const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function inGitRepo(): boolean {
  return existsSync(resolve(PROJECT_ROOT, ".git"));
}

function hasRemoteTracking(): boolean {
  try {
    execSync("git rev-parse --abbrev-ref --symbolic-full-name @{u}", {
      cwd: PROJECT_ROOT,
      stdio: "pipe",
    });
    return true;
  } catch {
    return false;
  }
}

function commitsBehind(): number {
  try {
    const out = execSync("git rev-list HEAD..@{u} --count", {
      cwd: PROJECT_ROOT,
      stdio: "pipe",
      timeout: 10_000,
    })
      .toString()
      .trim();
    return parseInt(out, 10) || 0;
  } catch {
    return 0;
  }
}

function runningFromCompiledDist(): boolean {
  // When launched via `node dist/index.js` or `stackowl` binary, argv[1] ends in dist/
  return process.argv[1]?.includes("/dist/") ?? false;
}

/**
 * Check for updates, pull if behind, rebuild compiled output when needed, then
 * restart the process so the new code (including new migrations) takes effect.
 *
 * Silent and non-fatal: any failure falls through and the app continues on the
 * current version. Never blocks startup if the network is unavailable.
 */
export async function tryAutoUpdate(): Promise<void> {
  if (!inGitRepo() || !hasRemoteTracking()) return;

  try {
    log.engine.debug("[AutoUpdate] Fetching remote state...");
    execSync("git fetch --quiet", { cwd: PROJECT_ROOT, stdio: "pipe", timeout: 10_000 });
  } catch {
    log.engine.debug("[AutoUpdate] Fetch skipped (offline or no remote)");
    return;
  }

  const behind = commitsBehind();
  if (behind === 0) {
    log.engine.debug("[AutoUpdate] Already up to date");
    return;
  }

  log.engine.info(`[AutoUpdate] ${behind} update(s) available — pulling...`);

  try {
    execSync("git pull --quiet", { cwd: PROJECT_ROOT, stdio: "pipe", timeout: 30_000 });
    log.engine.info("[AutoUpdate] Code updated successfully");
  } catch (err) {
    log.engine.warn(`[AutoUpdate] git pull failed: ${err}`);
    return;
  }

  // Rebuild compiled output so dist/index.js contains the new migrations.
  // tsx users skip this — tsx compiles source on demand.
  if (runningFromCompiledDist()) {
    log.engine.info("[AutoUpdate] Rebuilding compiled output...");
    try {
      execSync("npm run build --quiet", {
        cwd: PROJECT_ROOT,
        stdio: "pipe",
        timeout: 120_000,
      });
      log.engine.info("[AutoUpdate] Build complete");
    } catch (err) {
      log.engine.warn(`[AutoUpdate] Build failed — restarting with pulled source anyway: ${err}`);
    }
  }

  // Restart: spawn the updated process (inheriting stdio and process group so
  // signals like Ctrl+C reach it), then exit the old one.
  log.engine.info("[AutoUpdate] Restarting to apply updates and run migrations...");
  process.stdout.write("\n[AutoUpdate] Restarting with updated code…\n");

  const child = spawn(process.argv[0], process.argv.slice(1), {
    stdio: "inherit",
    env: process.env,
    cwd: PROJECT_ROOT,
    detached: false,
  });

  child.on("error", (err) => {
    process.stderr.write(`[AutoUpdate] Restart failed: ${err.message}\n`);
    process.exit(1);
  });

  child.on("exit", (code, signal) => {
    process.exit(signal ? 1 : (code ?? 0));
  });

  // Unref so this (old) process doesn't keep the event loop alive — the child
  // has inherited stdio and will keep the terminal alive on its own.
  child.unref();
  process.exit(0);
}

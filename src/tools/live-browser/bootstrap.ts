/**
 * StackOwl — Element 7 T21 — Chrome auto-bootstrap
 *
 * The CDP driver only works when Chrome was launched with
 * --remote-debugging-port=9222. Most users don't run their browser that
 * way, so the live_browser tool needs a one-shot "relaunch in debug mode"
 * flow: detect → prompt → quit-with-session-preserve → relaunch → reconnect.
 *
 * Every side effect is injectable so we can test the orchestration without
 * touching the real machine. The default production wiring lives in
 * `bootstrap-defaults.ts` (osascript quit, `open -a` relaunch with
 * --restore-last-session, polling fetch on /json/version).
 */
import { exec } from "node:child_process";
import { log } from "../../logger.js";
import { promisify } from "node:util";

const execAsync = promisify(exec);

export interface BootstrapDeps {
  /** Probe whether the CDP port is currently accepting connections. */
  isPortOpen: () => Promise<boolean>;
  /** Ask the user for permission to relaunch their Chrome. */
  prompt: () => Promise<boolean>;
  /** Quit Chrome and relaunch with the debug-port flag (session preserved). */
  relaunchChrome: () => Promise<void>;
  /** Poll the port until it answers, or give up at maxWaitMs. */
  waitForPort: () => Promise<boolean>;
  /** Connect the live_browser CDP client once the port is up. */
  connect: () => Promise<void>;

  /** Poll cadence + ceiling, exposed for tests. */
  pollIntervalMs?: number;
  maxWaitMs?: number;
}

/**
 * Run the full bootstrap sequence. Returns true when Chrome is connected
 * and ready for live_browser actions; false when the user declined or the
 * port never came up.
 */
export async function ensureChromeBootstrap(deps: BootstrapDeps): Promise<boolean> {
  log.tool.debug("bootstrap.ensureChromeBootstrap: entry");
  if (await deps.isPortOpen()) {
    log.tool.debug("bootstrap.ensureChromeBootstrap: CDP port already open, connecting");
    await deps.connect();
    log.tool.debug("bootstrap.ensureChromeBootstrap: exit", { success: true, path: "port-already-open" });
    return true;
  }

  log.tool.debug("bootstrap.ensureChromeBootstrap: port not open, prompting user");
  const approved = await deps.prompt();
  if (!approved) {
    log.tool.debug("bootstrap.ensureChromeBootstrap: exit", { success: false, path: "user-declined" });
    return false;
  }

  log.tool.debug("bootstrap.ensureChromeBootstrap: relaunching Chrome with debug port");
  await deps.relaunchChrome();

  log.tool.debug("bootstrap.ensureChromeBootstrap: waiting for port");
  const ready = await deps.waitForPort();
  if (!ready) {
    log.tool.debug("bootstrap.ensureChromeBootstrap: exit", { success: false, path: "port-timeout" });
    return false;
  }

  await deps.connect();
  log.tool.debug("bootstrap.ensureChromeBootstrap: exit", { success: true, path: "relaunched" });
  return true;
}

// ─── Default production wiring ─────────────────────────────────────────────

const DEBUG_PORT = 9222;
const DEFAULT_POLL_MS = 250;
const DEFAULT_MAX_WAIT_MS = 8_000;

export async function defaultIsPortOpen(port: number = DEBUG_PORT): Promise<boolean> {
  log.tool.debug("bootstrap.defaultIsPortOpen: checking", { port });
  try {
    const res = await fetch(`http://127.0.0.1:${port}/json/version`, {
      signal: AbortSignal.timeout(500),
    });
    log.tool.debug("bootstrap.defaultIsPortOpen: result", { port, open: res.ok });
    return res.ok;
  } catch (err) {
    log.tool.warn('operation failed', err);
    return false;
  }
}

export async function defaultRelaunchChrome(): Promise<void> {
  log.tool.debug("bootstrap.defaultRelaunchChrome: entry", { debugPort: DEBUG_PORT });
  // Quit Chrome (preserves session via Chrome's own restore mechanism)
  // then reopen with debug port + restore-last-session so tabs come back.
  try {
    await execAsync(`osascript -e 'tell application "Google Chrome" to quit'`);
    log.tool.debug("bootstrap.defaultRelaunchChrome: Chrome quit");
  } catch (err) {
    log.tool.warn('operation failed', err);
    // Chrome wasn't running — that's fine, we're about to launch it fresh.
  }
  // Small grace period so Chrome finishes its session-state flush before
  // the new instance reads it back.
  await new Promise((r) => setTimeout(r, 500));
  await execAsync(
    `open -a "Google Chrome" --args --remote-debugging-port=${DEBUG_PORT} --restore-last-session`,
  );
  log.tool.debug("bootstrap.defaultRelaunchChrome: Chrome relaunched", { debugPort: DEBUG_PORT });
}

export async function defaultWaitForPort(
  pollMs: number = DEFAULT_POLL_MS,
  maxMs: number = DEFAULT_MAX_WAIT_MS,
): Promise<boolean> {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    if (await defaultIsPortOpen()) return true;
    await new Promise((r) => setTimeout(r, pollMs));
  }
  return false;
}

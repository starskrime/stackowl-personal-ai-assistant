/**
 * StackOwl — Element 7 T21 — Chrome auto-bootstrap
 *
 * One-time setup helper: when the user's Chrome isn't already running with
 * --remote-debugging-port=9222, prompt them, then close-and-relaunch Chrome
 * with the debug flag (preserving the session) before connecting.
 *
 * Tests inject every side effect (port check, prompter, relauncher,
 * connector) so we can verify ordering without poking real Chrome.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  ensureChromeBootstrap,
  type BootstrapDeps,
} from "../../src/tools/live-browser/bootstrap.js";

function makeDeps(overrides: Partial<BootstrapDeps>): {
  deps: BootstrapDeps;
  log: {
    portChecks: number;
    prompted: number;
    relaunches: number;
    connects: number;
  };
} {
  const log = {
    portChecks: 0,
    prompted: 0,
    relaunches: 0,
    connects: 0,
  };
  const deps: BootstrapDeps = {
    isPortOpen: async () => {
      log.portChecks++;
      return false;
    },
    prompt: async () => {
      log.prompted++;
      return true;
    },
    relaunchChrome: async () => {
      log.relaunches++;
    },
    connect: async () => {
      log.connects++;
    },
    waitForPort: async () => true,
    pollIntervalMs: 1,
    maxWaitMs: 20,
    ...overrides,
  };
  return { deps, log };
}

describe("ensureChromeBootstrap", () => {
  it("connects directly when port 9222 is already open", async () => {
    const { deps, log } = makeDeps({
      isPortOpen: async () => {
        log.portChecks++;
        return true;
      },
    } as Partial<BootstrapDeps>);
    // Reattach the counter through the override:
    deps.isPortOpen = async () => {
      log.portChecks++;
      return true;
    };

    const result = await ensureChromeBootstrap(deps);
    expect(result).toBe(true);
    expect(log.connects).toBe(1);
    expect(log.prompted).toBe(0);
    expect(log.relaunches).toBe(0);
  });

  it("prompts, relaunches, waits, then connects when port is closed", async () => {
    const order: string[] = [];
    const deps: BootstrapDeps = {
      isPortOpen: async () => {
        order.push("check");
        return false;
      },
      prompt: async () => {
        order.push("prompt");
        return true;
      },
      relaunchChrome: async () => {
        order.push("relaunch");
      },
      waitForPort: async () => {
        order.push("wait");
        return true;
      },
      connect: async () => {
        order.push("connect");
      },
      pollIntervalMs: 1,
      maxWaitMs: 20,
    };

    const result = await ensureChromeBootstrap(deps);
    expect(result).toBe(true);
    expect(order).toEqual(["check", "prompt", "relaunch", "wait", "connect"]);
  });

  it("returns false and skips relaunch when prompt is declined", async () => {
    const { deps, log } = makeDeps({});
    deps.prompt = async () => {
      log.prompted++;
      return false;
    };

    const result = await ensureChromeBootstrap(deps);
    expect(result).toBe(false);
    expect(log.relaunches).toBe(0);
    expect(log.connects).toBe(0);
  });

  it("returns false when waitForPort times out (does not call connect)", async () => {
    const { deps, log } = makeDeps({});
    deps.waitForPort = async () => false;

    const result = await ensureChromeBootstrap(deps);
    expect(result).toBe(false);
    expect(log.relaunches).toBe(1);
    expect(log.connects).toBe(0);
  });
});

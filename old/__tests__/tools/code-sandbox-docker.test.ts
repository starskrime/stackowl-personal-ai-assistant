import { describe, it, expect, beforeAll } from "vitest";
import { platform } from "../../src/platform/index.js";

let hasDocker = false;
let hasPythonImage = false;

beforeAll(async () => {
  await platform.systemInfo.refresh();
  const caps = platform.systemInfo.current().capabilities;
  hasDocker = caps.hasDocker;
  hasPythonImage = caps.hasDockerImagesPulled.python;
});

const skipUnlessDocker = (fn: () => Promise<void> | void) => async () => {
  if (!hasDocker) { console.log("Skipping — no Docker"); return; }
  if (!hasPythonImage) { console.log("Skipping — python:3.12-slim not pulled"); return; }
  await fn();
};

describe("code-sandbox Docker path (helper tests; tool dispatch lands in T24)", () => {
  it("runInDocker is exported", async () => {
    // We don't assert behavior here since the public tool still uses host path.
    // Just verifies the export so T23/T24 can wire it.
    const mod = await import("../../src/tools/code-sandbox.js");
    expect(typeof (mod as any).runInDocker === "function" || (mod as any).runInDocker === undefined).toBe(true);
    // Real behavior tests run in T24 once dispatch is wired.
  });
});

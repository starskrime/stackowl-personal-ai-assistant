import { describe, it, expect } from "vitest";
import { mkdtemp, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mutateConsent } from "../../src/config/loader.js";

async function freshConfigDir(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "stackowl-test-"));
  const minimal = {
    providers: {
      openai: { baseUrl: "https://api.openai.com/v1", apiKey: "test", defaultModel: "gpt-4o-mini" },
    },
    defaultProvider: "openai",
    defaultModel: "gpt-4o-mini",
    workspace: ".",
    gateway: { port: 3000, host: "localhost" },
    parliament: { maxRounds: 1, maxOwls: 1 },
    heartbeat: { enabled: false, intervalMinutes: 60 },
    owlDna: { enabled: false, evolutionBatchSize: 1, decayRatePerWeek: 0.1 },
  };
  await writeFile(
    join(dir, "stackowl.config.json"),
    JSON.stringify(minimal, null, 2),
  );
  return dir;
}

describe("mutateConsent", () => {
  it("creates perches.consent block when missing", async () => {
    const dir = await freshConfigDir();
    await mutateConsent(dir, "clipboard", true);
    const cfg = JSON.parse(
      await readFile(join(dir, "stackowl.config.json"), "utf-8"),
    );
    expect(cfg.perches?.consent?.clipboard).toBe(true);
  });

  it("toggles existing consent value", async () => {
    const dir = await freshConfigDir();
    await mutateConsent(dir, "clipboard", true);
    await mutateConsent(dir, "clipboard", false);
    const cfg = JSON.parse(
      await readFile(join(dir, "stackowl.config.json"), "utf-8"),
    );
    expect(cfg.perches?.consent?.clipboard).toBe(false);
  });

  it("preserves consent state for other sources during mutation", async () => {
    const dir = await freshConfigDir();
    await mutateConsent(dir, "clipboard", true);
    await mutateConsent(dir, "email", true);
    const cfg = JSON.parse(
      await readFile(join(dir, "stackowl.config.json"), "utf-8"),
    );
    expect(cfg.perches.consent.clipboard).toBe(true);
    expect(cfg.perches.consent.email).toBe(true);
  });

  it("serializes concurrent mutations (last write wins per source)", async () => {
    const dir = await freshConfigDir();
    await Promise.all([
      mutateConsent(dir, "clipboard", true),
      mutateConsent(dir, "email", true),
      mutateConsent(dir, "calendar", true),
    ]);
    const cfg = JSON.parse(
      await readFile(join(dir, "stackowl.config.json"), "utf-8"),
    );
    expect(cfg.perches.consent.clipboard).toBe(true);
    expect(cfg.perches.consent.email).toBe(true);
    expect(cfg.perches.consent.calendar).toBe(true);
  });
});

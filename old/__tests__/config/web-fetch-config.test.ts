import { describe, it, expect } from "vitest";
import { loadConfig } from "../../src/config/loader.js";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

describe("webFetch config", () => {
  it("defaults webFetch.obscura.enabled to false when missing", async () => {
    const dir = mkdtempSync(join(tmpdir(), "stackowl-cfg-"));
    const cfgPath = join(dir, "stackowl.config.json");
    writeFileSync(cfgPath, JSON.stringify({ providers: {} }));
    const cfg = await loadConfig(dir);
    expect(cfg.webFetch?.obscura?.enabled).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("respects webFetch.obscura.enabled = true", async () => {
    const dir = mkdtempSync(join(tmpdir(), "stackowl-cfg-"));
    const cfgPath = join(dir, "stackowl.config.json");
    writeFileSync(
      cfgPath,
      JSON.stringify({ providers: {}, webFetch: { obscura: { enabled: true } } }),
    );
    const cfg = await loadConfig(dir);
    expect(cfg.webFetch?.obscura?.enabled).toBe(true);
    rmSync(dir, { recursive: true });
  });
});

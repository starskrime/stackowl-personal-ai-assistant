import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { platform } from "../../../src/platform/index.js";
import { SearchFilesTool } from "../../../src/tools/filesystem/search-files.js";

let workspace: string;

beforeEach(async () => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-search-files-rg-"));
  await platform.systemInfo.refresh();
  delete process.env.STACKOWL_DISABLE_RG;
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
  delete process.env.STACKOWL_DISABLE_RG;
});

describe("SearchFilesTool (ripgrep path)", () => {
  it("uses ripgrep when capability is present and not disabled", async () => {
    if (!platform.systemInfo.current().capabilities.hasRipgrep) {
      console.log("Skipping ripgrep test — rg not installed on host");
      return;
    }
    writeFileSync(join(workspace, "a.ts"), "needle\nhaystack\nneedle");
    const res = await SearchFilesTool.execute({ pattern: "needle", path: workspace }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.via).toBe("ripgrep");
    expect(parsed.data.matches.length).toBe(2);
  });

  it("returns same path shape across rg and JS fallback", async () => {
    if (!platform.systemInfo.current().capabilities.hasRipgrep) return;
    writeFileSync(join(workspace, "a.ts"), "foo");
    const rgRes = JSON.parse(await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace } as any));
    process.env.STACKOWL_DISABLE_RG = "true";
    const jsRes = JSON.parse(await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace } as any));
    expect(rgRes.data.matches[0].path).toBe(jsRes.data.matches[0].path);
    expect(rgRes.data.matches[0].line).toBe(jsRes.data.matches[0].line);
  });

  it("respects max_matches via --max-count", async () => {
    if (!platform.systemInfo.current().capabilities.hasRipgrep) return;
    let content = "";
    for (let i = 0; i < 20; i++) content += "needle\n";
    writeFileSync(join(workspace, "a.ts"), content);
    const res = await SearchFilesTool.execute({ pattern: "needle", path: workspace, max_matches: 5 }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(5);
  });
});

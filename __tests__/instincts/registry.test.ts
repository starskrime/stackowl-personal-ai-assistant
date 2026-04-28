import { describe, it, expect, beforeEach } from "vitest";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { InstinctRegistry } from "../../src/instincts/registry.js";

let owlsDir: string;

beforeEach(async () => {
  owlsDir = await mkdtemp(join(tmpdir(), "instinct-test-"));
});

async function cleanup() {
  await rm(owlsDir, { recursive: true, force: true });
}

async function writeInstinct(owlName: string, fileName: string, content: string) {
  const dir = join(owlsDir, owlName, "instincts");
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, fileName), content, "utf-8");
}

describe("InstinctRegistry", () => {
  it("returns empty array when owl has no instincts dir", async () => {
    await mkdir(join(owlsDir, "noctua"), { recursive: true });
    const registry = new InstinctRegistry();
    await registry.loadForOwl(owlsDir, "noctua");
    expect(registry.get("noctua")).toEqual([]);
    await cleanup();
  });

  it("loads valid instinct from markdown frontmatter", async () => {
    await writeInstinct(
      "noctua",
      "be-concise.md",
      `---\nname: be-concise\ndescription: user wants a short answer\nconstraint: Keep reply under 3 sentences.\n---\n`,
    );
    const registry = new InstinctRegistry();
    await registry.loadForOwl(owlsDir, "noctua");
    const instincts = registry.get("noctua");
    expect(instincts).toHaveLength(1);
    expect(instincts[0]).toMatchObject({
      name: "be-concise",
      description: "user wants a short answer",
      constraint: "Keep reply under 3 sentences.",
      owlName: "noctua",
    });
    await cleanup();
  });

  it("skips files missing required fields", async () => {
    await writeInstinct("noctua", "incomplete.md", `---\nname: only-name\n---\n`);
    const registry = new InstinctRegistry();
    await registry.loadForOwl(owlsDir, "noctua");
    expect(registry.get("noctua")).toEqual([]);
    await cleanup();
  });

  it("loads multiple instincts for same owl", async () => {
    await writeInstinct("noctua", "a.md", `---\nname: a\ndescription: desc a\nconstraint: constraint a\n---\n`);
    await writeInstinct("noctua", "b.md", `---\nname: b\ndescription: desc b\nconstraint: constraint b\n---\n`);
    const registry = new InstinctRegistry();
    await registry.loadForOwl(owlsDir, "noctua");
    expect(registry.get("noctua")).toHaveLength(2);
    await cleanup();
  });

  it("clear removes cached instincts", async () => {
    await writeInstinct("noctua", "a.md", `---\nname: a\ndescription: d\nconstraint: c\n---\n`);
    const registry = new InstinctRegistry();
    await registry.loadForOwl(owlsDir, "noctua");
    registry.clear("noctua");
    expect(registry.get("noctua")).toEqual([]);
    await cleanup();
  });

  it("isolates instincts per owl", async () => {
    await writeInstinct("owl1", "x.md", `---\nname: x\ndescription: d\nconstraint: c\n---\n`);
    const registry = new InstinctRegistry();
    await registry.loadForOwl(owlsDir, "owl1");
    await registry.loadForOwl(owlsDir, "owl2");
    expect(registry.get("owl1")).toHaveLength(1);
    expect(registry.get("owl2")).toEqual([]);
    await cleanup();
  });
});

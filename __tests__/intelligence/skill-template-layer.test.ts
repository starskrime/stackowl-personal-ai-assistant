import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { SkillTemplateLayer } from "../../src/intelligence/skill-template-layer.js";

describe("SkillTemplateLayer", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("returns empty string when no templates exist", async () => {
    const layer = new SkillTemplateLayer(db as any);
    const result = await layer.retrieve("research TypeScript");
    expect(result).toBe("");
  });

  it("returns proven_approach block when matching template exists", async () => {
    const buf = Buffer.alloc(4 * 4);
    [0.9, 0.1, 0.1, 0.1].forEach((v, i) => buf.writeFloatLE(v, i * 4));
    db.prepare(`
      INSERT INTO skill_templates (id, name, source, template_text, trigger_desc, embedding, success_count, installed_at)
      VALUES ('t1', 'web-research', 'auto', 'To research a topic: web(search) → web(fetch) → summarize', 'research, find information, look up', ?, 3, ?)
    `).run(buf, new Date().toISOString());

    const layer = new SkillTemplateLayer(db as any);
    (layer as any).embedFn = async () => [0.9, 0.1, 0.1, 0.1];
    const result = await layer.retrieve("find information about Node.js");
    expect(result).toContain("<proven_approach>");
    expect(result).toContain("web(search)");
  });
});

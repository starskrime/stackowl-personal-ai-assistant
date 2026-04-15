/**
 * StackOwl — Self-Seed
 *
 * On first startup (empty pellet store), generates foundational "about me"
 * pellets so the assistant knows what it is and what it can do — even fresh
 * out of a reset.  This prevents the "acts like generic LLM" regression.
 *
 * Seeds three pellets:
 *   1. identity   — who this owl is (from OWL.md)
 *   2. tools      — what tools are available
 *   3. skills     — what skills/playbooks exist
 */

import { readFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import matter from "gray-matter";
import { log } from "../logger.js";
import type { PelletStore, Pellet } from "./store.js";

// ─── Helpers ─────────────────────────────────────────────────────

function makePellet(
  id: string,
  title: string,
  tags: string[],
  content: string,
): Pellet {
  return {
    id,
    title,
    generatedAt: new Date().toISOString(),
    source: "self-seed",
    owls: ["system"],
    tags,
    content,
    version: 1,
  };
}

// ─── Seed ────────────────────────────────────────────────────────

/**
 * Seed foundational pellets if the store is empty.
 * Safe to call every startup — checks count first.
 */
export async function selfSeedIfEmpty(
  pelletStore: PelletStore,
  workspacePath: string,
  toolNames: string[],
): Promise<void> {
  const count = await pelletStore.count();
  if (count > 0) return; // Already has knowledge — skip

  log.engine.info("[SelfSeed] Empty store detected — seeding foundational pellets...");

  const pellets: Pellet[] = [];

  // ── 1. Identity pellets — one per OWL.md ─────────────────────
  const owlDirs: string[] = [];
  const builtinOwlsDir = new URL("../owls/defaults/", import.meta.url).pathname;
  const workspaceOwlsDir = join(workspacePath, "owls");

  for (const dir of [builtinOwlsDir, workspaceOwlsDir]) {
    if (!existsSync(dir)) continue;
    try {
      const entries = await readdir(dir, { withFileTypes: true });
      for (const e of entries) {
        if (e.isDirectory()) owlDirs.push(join(dir, e.name));
      }
    } catch { /* skip */ }
  }

  for (const owlDir of owlDirs) {
    const owlMd = join(owlDir, "OWL.md");
    if (!existsSync(owlMd)) continue;
    try {
      const raw = await readFile(owlMd, "utf-8");
      const { data, content } = matter(raw);
      const name = (data.name as string) ?? owlDir.split("/").pop() ?? "unknown";
      const type = (data.type as string) ?? "assistant";
      const specialties: string[] = Array.isArray(data.specialties) ? data.specialties : [];
      const traits: string[] = Array.isArray(data.traits) ? data.traits : [];

      const body = [
        `# ${name} — ${type}`,
        "",
        content.trim().slice(0, 2000),
        "",
        specialties.length > 0 ? `**Specialties:** ${specialties.join(", ")}` : "",
        traits.length > 0 ? `**Traits:** ${traits.join(", ")}` : "",
      ].filter((l) => l !== "").join("\n");

      pellets.push(makePellet(
        `seed_identity_${name.toLowerCase()}`,
        `Who I am: ${name} (${type})`,
        ["identity", "persona", "about-me", name.toLowerCase()],
        body,
      ));
    } catch { /* skip broken OWL.md */ }
  }

  // ── 2. Tools capability pellet ────────────────────────────────
  if (toolNames.length > 0) {
    const toolCategories: Record<string, string[]> = {
      "Web & Search": [],
      "Files & Code": [],
      "Memory & Knowledge": [],
      "Automation": [],
      "Other": [],
    };

    for (const t of toolNames) {
      if (/search|crawl|web|browse|fetch|scrape/i.test(t)) {
        toolCategories["Web & Search"].push(t);
      } else if (/read|write|edit|shell|file|code|sandbox/i.test(t)) {
        toolCategories["Files & Code"].push(t);
      } else if (/memory|recall|pellet|remember|fact/i.test(t)) {
        toolCategories["Memory & Knowledge"].push(t);
      } else if (/computer|mouse|keyboard|browser|cron|workflow/i.test(t)) {
        toolCategories["Automation"].push(t);
      } else {
        toolCategories["Other"].push(t);
      }
    }

    let toolContent = "# My Available Tools\n\nI can use these tools to accomplish tasks:\n\n";
    for (const [category, tools] of Object.entries(toolCategories)) {
      if (tools.length === 0) continue;
      toolContent += `## ${category}\n`;
      for (const t of tools) {
        toolContent += `- \`${t}\`\n`;
      }
      toolContent += "\n";
    }
    toolContent += `Total: ${toolNames.length} tools available.`;

    pellets.push(makePellet(
      "seed_tools_capability",
      "My Available Tools & Capabilities",
      ["tools", "capabilities", "self-knowledge"],
      toolContent,
    ));
  }

  // ── 3. Skills pellet ──────────────────────────────────────────
  const skillsDir = new URL("../skills/defaults/", import.meta.url).pathname;
  const skillNames: Array<{ name: string; description: string }> = [];

  if (existsSync(skillsDir)) {
    try {
      const entries = await readdir(skillsDir, { withFileTypes: true });
      for (const e of entries) {
        if (!e.isDirectory()) continue;
        const skillMd = join(skillsDir, e.name, "SKILL.md");
        if (!existsSync(skillMd)) continue;
        try {
          const raw = await readFile(skillMd, "utf-8");
          const { data } = matter(raw);
          skillNames.push({
            name: (data.name as string) ?? e.name,
            description: (data.description as string) ?? "",
          });
        } catch { /* skip */ }
      }
    } catch { /* skip */ }
  }

  if (skillNames.length > 0) {
    let skillContent = "# My Available Skills (Playbooks)\n\n";
    skillContent += "These are structured workflows I can execute:\n\n";
    for (const s of skillNames) {
      skillContent += `- **${s.name}**: ${s.description}\n`;
    }

    pellets.push(makePellet(
      "seed_skills",
      "My Available Skills & Playbooks",
      ["skills", "playbooks", "capabilities", "self-knowledge"],
      skillContent,
    ));
  }

  // ── 4. How to self-learn pellet ───────────────────────────────
  pellets.push(makePellet(
    "seed_self_learning",
    "How to Self-Learn and Discover My Capabilities",
    ["self-learning", "introspection", "capabilities"],
    `# How to Self-Learn

When asked to "go and learn what you are and what you can do", here is the approach:

1. **Read my own identity**: I am Noctua — an executive assistant owl. I have tools, skills, and a knowledge base (pellets).

2. **List available tools**: Use \`shell\` to run \`ls\` on my workspace, or check pellets with \`pellet_recall\` query="tools capabilities".

3. **Search the web for my knowledge gaps**: Use \`duckduckgo_search\` to research current events, technology updates, or topics relevant to the user.

4. **Create pellets from what I learn**: Use parliament sessions or direct pellet creation to store new knowledge permanently.

5. **Learn from conversations**: Every conversation is an opportunity to extract structured knowledge.

## Key Insight
After a reset, I start fresh but I still have:
- All my tools (${toolNames.length} available)
- All my skills/playbooks (${skillNames.length} available)
- My OWL.md persona definition
- The ability to learn through web research and tool use

I should immediately start using tools when asked to self-learn, rather than giving a text-only response.`,
  ));

  // ── Save all pellets ──────────────────────────────────────────
  let saved = 0;
  for (const pellet of pellets) {
    try {
      await pelletStore.save(pellet, { skipDedup: true });
      saved++;
    } catch (err) {
      log.engine.warn(`[SelfSeed] Failed to save "${pellet.id}": ${err instanceof Error ? err.message : err}`);
    }
  }

  log.engine.info(`[SelfSeed] Done — seeded ${saved}/${pellets.length} foundational pellets`);
}

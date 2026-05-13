/**
 * StackOwl — Owl Creation Wizard
 *
 * Interactive owl creation. Three paths: from-scratch, from-bmad, clone.
 * Writes a specialized_owl.md to workspace/owls/<Name>/ on completion.
 *
 * IMPORTANT: The YAML frontmatter layout must exactly match what
 * parseSpecializedOwl() in src/owls/specialized-parser.ts reads.
 * Fields are top-level (not nested): challengeLevel, verbosity, tone,
 * provider, model, keywords, domains, allowedTools, deniedTools,
 * capabilityConstraints, allowedSkills.
 */

import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { SpecializedOwlSpec } from "../../owls/specialized-types.js";
import { log } from "../../logger.js";

type WizardMode = "from-scratch" | "from-bmad" | "clone";

interface WizardParams {
  template?: SpecializedOwlSpec;
}

interface ChannelAdapter {
  ask(
    userId: string,
    prompt: { text: string; choices?: string[]; defaultChoice?: string },
  ): Promise<string>;
}

export async function runOwlCreationWizard(
  mode: WizardMode,
  params: WizardParams,
  workspacePath: string,
  userId: string,
  adapter: ChannelAdapter,
): Promise<string> {
  log.gateway.debug("owl-wizard.runOwlCreationWizard: entry", {
    mode,
    hasTemplate: !!params.template,
  });

  const t = params.template;

  // ── Step 1: Name ────────────────────────────────────────────────────────────
  const nameRaw = await adapter.ask(userId, {
    text:
      mode === "from-scratch"
        ? "What should this owl be named? (e.g., Alex, Sage)"
        : `Name for your new owl based on ${t?.name ?? "template"}? (press Enter to keep "${t?.name ?? ""}")`,
    defaultChoice: mode === "from-scratch" ? undefined : t?.name,
  });

  const name = nameRaw?.trim();
  if (!name) {
    log.gateway.debug("owl-wizard.runOwlCreationWizard: exit", { result: "cancelled — no name" });
    return "Owl creation cancelled — no name provided.";
  }
  log.gateway.debug("owl-wizard.runOwlCreationWizard: name collected", { name });

  // ── Step 2: Emoji ────────────────────────────────────────────────────────────
  const emojiRaw = await adapter.ask(userId, {
    text: `Choose an emoji for ${name}:`,
    defaultChoice: t?.emoji ?? "🦉",
  });
  const emoji = emojiRaw?.trim() || t?.emoji || "🦉";

  // ── Step 3: Role ─────────────────────────────────────────────────────────────
  const roleRaw = await adapter.ask(userId, {
    text: `What is ${name}'s role or specialty?`,
    defaultChoice: t?.role ?? "",
  });
  const role = roleRaw?.trim() || name;

  // ── Step 4: Expertise (domains) ──────────────────────────────────────────────
  const expertiseRaw = await adapter.ask(userId, {
    text: "List areas of expertise (comma-separated):",
    defaultChoice: t?.expertise.join(", ") ?? "",
  });
  const expertise = expertiseRaw
    .split(",")
    .map((e) => e.trim())
    .filter(Boolean);

  // ── Step 5: Persona / additional prompt ──────────────────────────────────────
  const personaRaw = await adapter.ask(userId, {
    text: `Describe ${name}'s personality and communication style:`,
    defaultChoice: t?.additionalPrompt ?? "",
  });
  const persona = personaRaw?.trim() ?? "";

  // ── Step 6: Challenge level ───────────────────────────────────────────────────
  const challengeRaw = await adapter.ask(userId, {
    text: "Challenge level:",
    choices: ["low", "medium", "high", "relentless"],
    defaultChoice: t?.personality.challengeLevel ?? "medium",
  });
  const challengeLevel =
    (["low", "medium", "high", "relentless"] as const).find((v) => v === challengeRaw) ??
    t?.personality.challengeLevel ??
    "medium";

  // ── Step 7: Routing keywords ──────────────────────────────────────────────────
  const keywordsRaw = await adapter.ask(userId, {
    text: `Routing keywords (comma-separated) — messages containing these words route to ${name}:`,
    defaultChoice: t?.routingRules.keywords.join(", ") ?? "",
  });
  const keywords = keywordsRaw
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);

  log.gateway.debug("owl-wizard.runOwlCreationWizard: all inputs collected", {
    name,
    emoji,
    role,
    expertiseCount: expertise.length,
    keywordsCount: keywords.length,
    challengeLevel,
  });

  // ── Build spec object ─────────────────────────────────────────────────────────
  const spec: SpecializedOwlSpec = {
    name,
    type: "specialist",
    role,
    emoji,
    personality: {
      challengeLevel,
      verbosity: t?.personality.verbosity ?? "balanced",
      tone: t?.personality.tone ?? "professional",
    },
    expertise,
    model: t?.model ?? { provider: "anthropic", model: "claude-sonnet-4-6" },
    permissions: t?.permissions ?? {
      allowedTools: [],
      deniedTools: [],
      capabilityConstraints: [],
    },
    routingRules: {
      keywords,
    },
    skills: t?.skills ?? { allowed: [] },
    additionalPrompt: persona,
    source: "custom",
  };

  log.gateway.debug("owl-wizard.runOwlCreationWizard: spec built", {
    name: spec.name,
    emoji: spec.emoji,
    source: spec.source,
  });

  // ── Write to disk ─────────────────────────────────────────────────────────────
  const owlDir = join(workspacePath, "owls", spec.name);
  await mkdir(owlDir, { recursive: true });
  const specPath = join(owlDir, "specialized_owl.md");
  await writeFile(specPath, buildSpecFile(spec), "utf-8");

  log.gateway.info("owl-wizard.runOwlCreationWizard: owl created", {
    name: spec.name,
    path: specPath,
  });

  const summary = [
    `✅ Created ${spec.emoji} **${spec.name}**!`,
    `Role: ${spec.role}`,
    `Expertise: ${spec.expertise.join(", ") || "—"}`,
    `Keywords: ${spec.routingRules.keywords.join(", ") || "—"}`,
    "",
    `Mention them with @${spec.name} in your next message.`,
    "Restart to fully load from disk, or use /owl reload if available.",
  ].join("\n");

  log.gateway.debug("owl-wizard.runOwlCreationWizard: exit", { name: spec.name });
  return summary;
}

/**
 * Serialize a SpecializedOwlSpec to the YAML frontmatter format that
 * parseSpecializedOwl() (src/owls/specialized-parser.ts) can read back.
 *
 * Keys are flat/top-level as the parser expects:
 *   challengeLevel, verbosity, tone  (not under `personality:`)
 *   provider, model                  (not under `model:`)
 *   keywords                         (not under `routingRules:`)
 *   domains                          (not under `expertise:`)
 *   allowedSkills                    (not under `skills:`)
 */
function buildSpecFile(spec: SpecializedOwlSpec): string {
  const domainsYaml =
    spec.expertise.length > 0
      ? spec.expertise.map((e) => `  - ${e}`).join("\n")
      : "  []";

  const keywordsYaml =
    spec.routingRules.keywords.length > 0
      ? spec.routingRules.keywords.map((k) => `  - ${k}`).join("\n")
      : "  []";

  const allowedSkillsYaml =
    spec.skills.allowed.length > 0
      ? spec.skills.allowed.map((s) => `  - ${s}`).join("\n")
      : "  []";

  const allowedToolsYaml =
    spec.permissions.allowedTools.length > 0
      ? spec.permissions.allowedTools.map((t) => `  - ${t}`).join("\n")
      : "  []";

  const deniedToolsYaml =
    spec.permissions.deniedTools.length > 0
      ? spec.permissions.deniedTools.map((t) => `  - ${t}`).join("\n")
      : "  []";

  const constraintsYaml =
    spec.permissions.capabilityConstraints.length > 0
      ? spec.permissions.capabilityConstraints.map((c) => `  - ${c}`).join("\n")
      : "  []";

  return `---
name: ${spec.name}
type: ${spec.type}
role: ${spec.role}
emoji: ${spec.emoji}
source: ${spec.source ?? "custom"}
challengeLevel: ${spec.personality.challengeLevel}
verbosity: ${spec.personality.verbosity}
tone: ${spec.personality.tone}
domains:
${domainsYaml}
provider: ${spec.model.provider}
model: ${spec.model.model}
allowedTools:
${allowedToolsYaml}
deniedTools:
${deniedToolsYaml}
capabilityConstraints:
${constraintsYaml}
keywords:
${keywordsYaml}
allowedSkills:
${allowedSkillsYaml}
---

${spec.additionalPrompt}
`;
}

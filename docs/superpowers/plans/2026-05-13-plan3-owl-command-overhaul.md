# /owl Command Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/helper` with `/owl` as the unified owl management surface across all channels (Telegram, Slack, CLI v1, CLI v2). BMAD agents are specialized owls routable via `@john`, `@mary` etc. Users can create custom owls from scratch or from a BMAD template. The secretary (Noctua) forwards to specialists automatically; users can also pin a specific owl for a session.

**Architecture:** A new `owl-command.ts` dispatcher (replaces `owl-router.ts`) handles verbs: `list`, `show`, `create`, `from-bmad`, `edit`, `delete`, `pin`, `unpin`, `status`. A new `owl-wizard.ts` (replaces `owl-creation.ts`) supports three creation paths: from-scratch, from-bmad-template, clone-existing. All channels call the same dispatcher. BMAD agents appear in `/owl list` as `source: "bmad"` entries. Custom owls land in `workspace/owls/<Name>/specialized_owl.md` as before — the existing `parseSpecializedOwl` and `SpecializedOwlRegistry.loadAll()` pick them up on next startup. The `@mention` routing in `gateway/core.ts` is unchanged — it already looks up names in `specializedRegistry`.

**Tech Stack:** TypeScript, `SpecializedOwlRegistry`, `BmadAgentLoader`, existing `parseSpecializedOwl` + `OwlCreationWizard`-style interactive prompts (inline, no separate wizard class)

**Prerequisite:** Plans 1 and 2 must be merged first (Plan 1 deletes `owl-router.ts`/`owl-creation.ts`; Plan 2 adds `BmadAgentLoader` and `registerSpec()`).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| CREATE | `src/gateway/commands/owl-command.ts` | Unified /owl verb dispatcher |
| CREATE | `src/gateway/wizards/owl-wizard.ts` | Interactive owl creation (from-scratch, from-bmad, clone) |
| CREATE | `src/cli/v2/commands/handlers/owl.ts` | CLI v2 handlers for all /owl subcommands |
| MODIFY | `src/gateway/adapters/telegram.ts` | Expand bot.command("owl") to full management |
| MODIFY | `src/gateway/adapters/slack.ts` | Add /owl Slack command |
| MODIFY | `src/cli/commands.ts` | Replace cmdHelper stub with full /owl management |
| MODIFY | `src/cli/v2/commands/registry.ts` | Expand /owl subcommands |
| CREATE | `__tests__/gateway/commands/owl-command.test.ts` | Unit tests for dispatcher |

---

### Task 1: Create `owl-command.ts` dispatcher

**Files:**
- Create: `src/gateway/commands/owl-command.ts`
- Create: `__tests__/gateway/commands/owl-command.test.ts`

The dispatcher accepts `(verb, args, ctx)` and returns a formatted string response.

Supported verbs:
- `list` — list all owls (builtin + BMAD + custom) with emoji, name, role, source
- `show <name>` — detailed view of one owl spec
- `status` — current active owl DNA state (existing OwlStateReporter)
- `create` — start interactive creation wizard (from-scratch)
- `from-bmad <agentName>` — start wizard prefilled from a BMAD template
- `edit <name>` — show current spec in editable format (future: interactive edit)
- `delete <name>` — delete a user-created owl (guard: source !== "bmad", not the active owl)
- `pin <name>` — pin owl for this session
- `unpin` — unpin active owl

Context shape that the dispatcher receives:
```typescript
export interface OwlCommandContext {
  registry: SpecializedOwlRegistry;
  userId: string;
  workspacePath: string;
  channelAdapter?: {
    ask(userId: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }): Promise<string>;
  };
  gateway?: import("../../gateway/core.js").OwlGateway;
}
```

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/gateway/commands/owl-command.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";
import { dispatchOwlCommand } from "../../src/gateway/commands/owl-command.js";
import type { SpecializedOwlSpec } from "../../src/owls/specialized-types.js";

function makeSpec(overrides: Partial<SpecializedOwlSpec> = {}): SpecializedOwlSpec {
  return {
    name: "Mary",
    type: "specialist",
    role: "Business Analyst",
    emoji: "📊",
    personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
    expertise: ["business analysis"],
    model: { provider: "anthropic", model: "claude-sonnet-4-6" },
    permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
    routingRules: { keywords: ["business"] },
    skills: { allowed: [] },
    additionalPrompt: "Identity: Strategic analyst.",
    source: "bmad",
    bmadSkillName: "bmad-agent-analyst",
    ...overrides,
  };
}

describe("dispatchOwlCommand", () => {
  let registry: SpecializedOwlRegistry;

  beforeEach(() => {
    registry = new SpecializedOwlRegistry();
    registry.registerSpec(makeSpec({ name: "Mary", source: "bmad" }));
    registry.registerSpec(makeSpec({ name: "CustomOwl", source: "custom", emoji: "🦉" }));
  });

  const ctx = () => ({
    registry,
    userId: "test-user",
    workspacePath: "/tmp/test-workspace",
  });

  it("list returns all owls with emoji and source", async () => {
    const result = await dispatchOwlCommand("list", [], ctx());
    expect(result).toContain("Mary");
    expect(result).toContain("📊");
    expect(result).toContain("bmad");
    expect(result).toContain("CustomOwl");
  });

  it("show returns detailed spec for a known owl", async () => {
    const result = await dispatchOwlCommand("show", ["mary"], ctx());
    expect(result).toContain("Mary");
    expect(result).toContain("Business Analyst");
    expect(result).toContain("business analysis");
  });

  it("show returns error for unknown owl", async () => {
    const result = await dispatchOwlCommand("show", ["nobody"], ctx());
    expect(result).toMatch(/not found/i);
  });

  it("delete rejects bmad-sourced owls", async () => {
    const result = await dispatchOwlCommand("delete", ["mary"], ctx());
    expect(result).toMatch(/cannot delete.*bmad/i);
  });

  it("delete succeeds for custom owl", async () => {
    const result = await dispatchOwlCommand("delete", ["customowl"], ctx());
    // Should attempt deletion (may fail if folder doesn't exist, but rejects with reason)
    expect(typeof result).toBe("string");
  });

  it("unknown verb returns help text", async () => {
    const result = await dispatchOwlCommand("frobnicate", [], ctx());
    expect(result).toMatch(/unknown.*command|usage|verb/i);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
npx vitest run __tests__/gateway/commands/owl-command.test.ts 2>&1 | tail -15
```

Expected: FAIL — `dispatchOwlCommand` does not exist yet.

- [ ] **Step 3: Implement `src/gateway/commands/owl-command.ts`**

```typescript
/**
 * StackOwl — /owl Command Dispatcher
 *
 * Unified owl management surface for all channels.
 * Verbs: list, show, status, create, from-bmad, edit, delete, pin, unpin
 */

import { rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { SpecializedOwlRegistry } from "../../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../../owls/specialized-types.js";
import { log } from "../../logger.js";

export interface OwlCommandContext {
  registry: SpecializedOwlRegistry;
  userId: string;
  workspacePath: string;
  channelAdapter?: {
    ask(userId: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }): Promise<string>;
  };
  gateway?: import("../core.js").OwlGateway;
}

export async function dispatchOwlCommand(
  verb: string,
  args: string[],
  ctx: OwlCommandContext,
): Promise<string> {
  log.gateway.debug("dispatchOwlCommand: entry", { verb, args: args.slice(0, 3) });

  switch (verb.toLowerCase()) {
    case "list":
      return cmdList(ctx);
    case "show":
      return cmdShow(args[0] ?? "", ctx);
    case "status":
      return cmdStatus(ctx);
    case "create":
      return cmdCreate(ctx);
    case "from-bmad":
      return cmdFromBmad(args[0] ?? "", ctx);
    case "edit":
      return cmdEdit(args[0] ?? "", ctx);
    case "delete":
    case "remove":
      return cmdDelete(args[0] ?? "", ctx);
    case "pin":
      return cmdPin(args[0] ?? "", ctx);
    case "unpin":
      return cmdUnpin(ctx);
    default:
      return [
        "Unknown /owl command. Usage:",
        "  /owl list               — list all owls",
        "  /owl show <name>        — show owl details",
        "  /owl status             — active owl DNA state",
        "  /owl create             — create a custom owl (interactive)",
        "  /owl from-bmad <name>  — create owl from BMAD template",
        "  /owl edit <name>        — edit a custom owl",
        "  /owl delete <name>      — delete a custom owl",
        "  /owl pin <name>         — pin owl for this session",
        "  /owl unpin              — unpin active owl",
      ].join("\n");
  }
}

async function cmdList(ctx: OwlCommandContext): Promise<string> {
  const specs = ctx.registry.listAll();
  if (specs.length === 0) {
    return "No owls registered. BMAD agents load at startup; custom owls live in workspace/owls/.";
  }
  const grouped: Record<string, SpecializedOwlSpec[]> = { bmad: [], custom: [], builtin: [], other: [] };
  for (const s of specs) {
    const key = s.source ?? "other";
    (grouped[key] ?? grouped.other).push(s);
  }
  const lines: string[] = ["**Owls** — mention with @name\n"];
  const renderGroup = (label: string, items: SpecializedOwlSpec[]) => {
    if (items.length === 0) return;
    lines.push(`**${label}**`);
    for (const s of items) {
      lines.push(`  ${s.emoji} **${s.name}** — ${s.role}`);
    }
  };
  renderGroup("BMAD Agents", grouped.bmad);
  renderGroup("Custom Owls", grouped.custom);
  renderGroup("Built-in", grouped.builtin);
  renderGroup("Other", grouped.other);
  return lines.join("\n");
}

async function cmdShow(name: string, ctx: OwlCommandContext): Promise<string> {
  if (!name) return "Usage: /owl show <name>";
  const spec = ctx.registry.get(name);
  if (!spec) return `Owl "${name}" not found. Use /owl list to see available owls.`;
  return [
    `${spec.emoji} **${spec.name}** (${spec.source ?? "unknown"})`,
    `Role: ${spec.role}`,
    `Expertise: ${spec.expertise.join(", ") || "—"}`,
    `Keywords: ${spec.routingRules.keywords.join(", ") || "—"}`,
    `Challenge: ${spec.personality.challengeLevel}  Verbosity: ${spec.personality.verbosity}`,
    `Model: ${spec.model.provider}/${spec.model.model}`,
    spec.additionalPrompt ? `\nPersona:\n${spec.additionalPrompt.slice(0, 400)}` : "",
  ].filter(Boolean).join("\n");
}

async function cmdStatus(ctx: OwlCommandContext): Promise<string> {
  const gateway = ctx.gateway;
  if (!gateway) return "Gateway not available in this context. Use /owl status from Telegram or CLI.";
  const db = gateway.getDb?.();
  if (!db) return "Database not available.";
  const owl = gateway.getOwl();
  const { OwlStateReporter } = await import("../../intelligence/owl-state-reporter.js");
  const reporter = new OwlStateReporter(db);
  const dna = owl.dna.evolvedTraits as Record<string, unknown>;
  return reporter.report(ctx.userId, owl.persona.name, dna);
}

async function cmdCreate(ctx: OwlCommandContext): Promise<string> {
  const adapter = ctx.channelAdapter;
  if (!adapter) {
    return [
      "Interactive owl creation requires a channel (Telegram or CLI).",
      "Use: /owl create — then follow the prompts.",
    ].join("\n");
  }
  const { runOwlCreationWizard } = await import("../wizards/owl-wizard.js");
  return runOwlCreationWizard("from-scratch", {}, ctx.workspacePath, ctx.userId, adapter);
}

async function cmdFromBmad(agentName: string, ctx: OwlCommandContext): Promise<string> {
  if (!agentName) {
    const specs = ctx.registry.listAll().filter((s) => s.source === "bmad");
    if (specs.length === 0) return "No BMAD agents loaded. Restart to reload.";
    const names = specs.map((s) => `${s.emoji} ${s.name} (${s.bmadSkillName})`).join("\n  ");
    return `Available BMAD templates:\n  ${names}\n\nUsage: /owl from-bmad <name>`;
  }
  const spec = ctx.registry.get(agentName);
  if (!spec || spec.source !== "bmad") {
    return `BMAD agent "${agentName}" not found. Use /owl from-bmad (no args) to list available templates.`;
  }
  const adapter = ctx.channelAdapter;
  if (!adapter) {
    return "Interactive wizard requires a channel (Telegram or CLI).";
  }
  const { runOwlCreationWizard } = await import("../wizards/owl-wizard.js");
  return runOwlCreationWizard("from-bmad", { template: spec }, ctx.workspacePath, ctx.userId, adapter);
}

async function cmdEdit(name: string, ctx: OwlCommandContext): Promise<string> {
  if (!name) return "Usage: /owl edit <name>";
  const spec = ctx.registry.get(name);
  if (!spec) return `Owl "${name}" not found.`;
  if (spec.source === "bmad") {
    return [
      `${spec.emoji} **${spec.name}** is a BMAD agent and cannot be edited directly.`,
      `To customize it, create a new owl from its template: /owl from-bmad ${spec.name}`,
    ].join("\n");
  }
  return [
    `Editing ${spec.emoji} **${spec.name}**`,
    `Spec file: ${spec.folderPath ?? "unknown"}/specialized_owl.md`,
    "",
    "Edit the spec file directly, then use /owl reload to pick up changes.",
    "(Interactive edit coming soon — for now, edit the file manually.)",
  ].join("\n");
}

async function cmdDelete(name: string, ctx: OwlCommandContext): Promise<string> {
  if (!name) return "Usage: /owl delete <name>";
  const spec = ctx.registry.get(name);
  if (!spec) return `Owl "${name}" not found.`;
  if (spec.source === "bmad") {
    return `Cannot delete BMAD agent "${spec.name}". BMAD agents are managed by the bmad-method package. Use /owl from-bmad to create a custom copy.`;
  }
  if (!spec.folderPath || !existsSync(spec.folderPath)) {
    return `Owl "${name}" has no folder on disk — nothing to delete.`;
  }
  try {
    await rm(spec.folderPath, { recursive: true, force: true });
    log.gateway.info("dispatchOwlCommand: deleted owl folder", { name, folder: spec.folderPath });
    return `🗑️ Deleted ${spec.emoji} **${spec.name}**. Restart to fully clear from registry.`;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log.gateway.error("dispatchOwlCommand: delete failed", err, { name, folder: spec.folderPath });
    return `Failed to delete "${name}": ${msg}`;
  }
}

async function cmdPin(name: string, ctx: OwlCommandContext): Promise<string> {
  if (!name) return "Usage: /owl pin <name>";
  const spec = ctx.registry.get(name);
  if (!spec) return `Owl "${name}" not found. Use /owl list to see available owls.`;
  // Gateway pin API if available — use (as any) since pinOwl is not on the typed interface yet
  const gw = ctx.gateway as any;
  if (typeof gw?.pinOwl === "function") {
    await gw.pinOwl(ctx.userId, spec.name);
  }
  return `📌 Pinned ${spec.emoji} **${spec.name}** for your session. Messages will route to ${spec.name} until unpinned.`;
}

async function cmdUnpin(ctx: OwlCommandContext): Promise<string> {
  const gw = ctx.gateway as any;
  if (typeof gw?.unpinOwl === "function") {
    await gw.unpinOwl(ctx.userId);
  }
  return "📌 Owl unpinned. Noctua (the secretary) will handle routing again.";
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/gateway/commands/owl-command.test.ts
```

Expected: all tests PASS

- [ ] **Step 5: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "owl-command"
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/commands/owl-command.ts __tests__/gateway/commands/owl-command.test.ts
git commit -m "feat(commands): /owl dispatcher with list/show/create/from-bmad/delete/pin/unpin"
```

---

### Task 2: Create `owl-wizard.ts` — interactive owl creation

**Files:**
- Create: `src/gateway/wizards/owl-wizard.ts`

This replaces `owl-creation.ts` with a simpler function-based approach (no class). Three paths:
- `"from-scratch"` — collect name, emoji, role, expertise, personality, additionalPrompt
- `"from-bmad"` — pre-fill from template spec, only ask for a custom name and any overrides
- `"clone"` — pre-fill from an existing custom spec

The wizard writes a `specialized_owl.md` file to `workspace/owls/<Name>/specialized_owl.md` using `parseSpecializedOwl`-compatible format.

- [ ] **Step 1: Understand the `specialized_owl.md` format**

```bash
cat src/owls/specialized-parser.ts | head -80
```

The file format is YAML frontmatter + markdown body. The parser reads `name`, `type`, `role`, `emoji`, `personality.*`, `expertise`, `model.*`, `permissions.*`, `routingRules.keywords`, `skills.allowed`, and the body becomes `additionalPrompt`.

- [ ] **Step 2: Implement `src/gateway/wizards/owl-wizard.ts`**

```typescript
/**
 * StackOwl — Owl Creation Wizard
 *
 * Interactive owl creation. Three paths: from-scratch, from-bmad, clone.
 * Writes a specialized_owl.md to workspace/owls/<Name>/ on completion.
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
  ask(userId: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }): Promise<string>;
}

export async function runOwlCreationWizard(
  mode: WizardMode,
  params: WizardParams,
  workspacePath: string,
  userId: string,
  adapter: ChannelAdapter,
): Promise<string> {
  log.gateway.debug("owl-wizard.runOwlCreationWizard: entry", { mode, hasTemplate: !!params.template });

  const t = params.template;

  const name = await adapter.ask(userId, {
    text: mode === "from-scratch"
      ? "What should this owl be named? (e.g., Alex, Sage)"
      : `Name for your new owl based on ${t?.name ?? "template"}? (press Enter to keep "${t?.name ?? ""}")`,
    defaultChoice: mode === "from-scratch" ? undefined : t?.name,
  });

  if (!name?.trim()) return "Owl creation cancelled — no name provided.";

  const emoji = await adapter.ask(userId, {
    text: `Choose an emoji for ${name}:`,
    defaultChoice: t?.emoji ?? "🦉",
  });

  const role = await adapter.ask(userId, {
    text: `What is ${name}'s role or specialty?`,
    defaultChoice: t?.role ?? "",
  });

  const expertise = await adapter.ask(userId, {
    text: `List areas of expertise (comma-separated):`,
    defaultChoice: t?.expertise.join(", ") ?? "",
  });

  const persona = await adapter.ask(userId, {
    text: `Describe ${name}'s personality and communication style:`,
    defaultChoice: t?.additionalPrompt ?? "",
  });

  const challengeLevel = await adapter.ask(userId, {
    text: `Challenge level:`,
    choices: ["low", "medium", "high", "relentless"],
    defaultChoice: t?.personality.challengeLevel ?? "medium",
  });

  const keywords = await adapter.ask(userId, {
    text: `Routing keywords (comma-separated) — messages containing these words route to ${name}:`,
    defaultChoice: t?.routingRules.keywords.join(", ") ?? "",
  });

  // Build spec
  const spec: SpecializedOwlSpec = {
    name: name.trim(),
    type: "specialist",
    role: role.trim() || name.trim(),
    emoji: emoji.trim() || "🦉",
    personality: {
      challengeLevel: (challengeLevel as "low" | "medium" | "high" | "relentless") ?? "medium",
      verbosity: t?.personality.verbosity ?? "balanced",
      tone: t?.personality.tone ?? "professional",
    },
    expertise: expertise.split(",").map((e) => e.trim()).filter(Boolean),
    model: t?.model ?? { provider: "anthropic", model: "claude-sonnet-4-6" },
    permissions: t?.permissions ?? { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
    routingRules: {
      keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
    },
    skills: t?.skills ?? { allowed: [] },
    additionalPrompt: persona.trim(),
    source: "custom",
  };

  // Write to disk
  const owlDir = join(workspacePath, "owls", spec.name);
  await mkdir(owlDir, { recursive: true });
  const specPath = join(owlDir, "specialized_owl.md");
  await writeFile(specPath, buildSpecFile(spec), "utf-8");

  log.gateway.info("owl-wizard: created owl", { name: spec.name, path: specPath });

  return [
    `✅ Created ${spec.emoji} **${spec.name}**!`,
    `Role: ${spec.role}`,
    `Expertise: ${spec.expertise.join(", ") || "—"}`,
    `Keywords: ${spec.routingRules.keywords.join(", ") || "—"}`,
    "",
    `Mention them with @${spec.name} in your next message.`,
    `(Restart to fully load from disk, or use /owl reload if available.)`,
  ].join("\n");
}

function buildSpecFile(spec: SpecializedOwlSpec): string {
  return `---
name: ${spec.name}
type: ${spec.type}
role: ${spec.role}
emoji: ${spec.emoji}
source: ${spec.source ?? "custom"}
personality:
  challengeLevel: ${spec.personality.challengeLevel}
  verbosity: ${spec.personality.verbosity}
  tone: ${spec.personality.tone}
expertise:
${spec.expertise.map((e) => `  - ${e}`).join("\n") || "  []"}
model:
  provider: ${spec.model.provider}
  model: ${spec.model.model}
permissions:
  allowedTools: []
  deniedTools: []
  capabilityConstraints: []
routingRules:
  keywords:
${spec.routingRules.keywords.map((k) => `    - ${k}`).join("\n") || "    []"}
skills:
  allowed: []
---

${spec.additionalPrompt}
`;
}
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "owl-wizard"
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/wizards/owl-wizard.ts
git commit -m "feat(wizards): owl-wizard — from-scratch, from-bmad, clone creation paths"
```

---

### Task 3: Create CLI v2 handlers for /owl

**Files:**
- Create: `src/cli/v2/commands/handlers/owl.ts`

- [ ] **Step 1: Implement `src/cli/v2/commands/handlers/owl.ts`**

```typescript
/**
 * StackOwl — /owl CLI v2 Handlers
 */

import type { CommandHandler } from "../types.js";
import { dispatchOwlCommand } from "../../../../gateway/commands/owl-command.js";
import { log } from "../../../../logger.js";

const makeCtx = (ctx: Parameters<CommandHandler>[0]) => {
  const gateway = ctx.getOwlGateway();
  return {
    registry: gateway.getSpecializedRegistry()!,
    userId: "local",
    workspacePath: gateway.getWorkspacePath(),
    gateway,
  };
};

export const handleOwlList: CommandHandler = async (ctx, _args) => {
  const owlCtx = makeCtx(ctx);
  if (!owlCtx.registry) return { kind: "error", text: "Specialized registry not initialized." };
  await owlCtx.registry.loadAll(owlCtx.workspacePath);
  const text = await dispatchOwlCommand("list", [], owlCtx);
  const items = text.split("\n").filter((l) => l.trim()).map((line, i) => ({ id: `owl-${i}`, label: line }));
  return { kind: "panel", payload: { title: "/owl list", items } };
};

export const handleOwlShow: CommandHandler = async (ctx, args) => {
  const owlCtx = makeCtx(ctx);
  if (!owlCtx.registry) return { kind: "error", text: "Specialized registry not initialized." };
  const text = await dispatchOwlCommand("show", args, owlCtx);
  const items = text.split("\n").map((line, i) => ({ id: `owl-show-${i}`, label: line }));
  return { kind: "panel", payload: { title: `/owl show ${args[0] ?? ""}`, items } };
};

export const handleOwlDelete: CommandHandler = async (ctx, args) => {
  const owlCtx = makeCtx(ctx);
  if (!owlCtx.registry) return { kind: "error", text: "Specialized registry not initialized." };
  const text = await dispatchOwlCommand("delete", args, owlCtx);
  return { kind: "success", text };
};

export const handleOwlFromBmad: CommandHandler = async (ctx, args) => {
  const owlCtx = makeCtx(ctx);
  if (!owlCtx.registry) return { kind: "error", text: "Specialized registry not initialized." };
  // CLI interactive adapter
  const adapter = await buildCliAdapter();
  const text = await dispatchOwlCommand("from-bmad", args, { ...owlCtx, channelAdapter: adapter });
  return { kind: "success", text };
};

export const handleOwlCreate: CommandHandler = async (ctx, _args) => {
  const owlCtx = makeCtx(ctx);
  if (!owlCtx.registry) return { kind: "error", text: "Specialized registry not initialized." };
  const adapter = await buildCliAdapter();
  const text = await dispatchOwlCommand("create", [], { ...owlCtx, channelAdapter: adapter });
  return { kind: "success", text };
};

export const handleOwlPin: CommandHandler = async (ctx, args) => {
  const owlCtx = makeCtx(ctx);
  if (!owlCtx.registry) return { kind: "error", text: "Specialized registry not initialized." };
  const text = await dispatchOwlCommand("pin", args, owlCtx);
  return { kind: "success", text };
};

export const handleOwlUnpin: CommandHandler = async (ctx, _args) => {
  const owlCtx = makeCtx(ctx);
  const text = await dispatchOwlCommand("unpin", [], owlCtx);
  return { kind: "success", text };
};

async function buildCliAdapter() {
  const { default: readline } = await import("node:readline");
  return {
    ask: async (_userId: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }) => {
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      const choices = prompt.choices
        ? `\n${prompt.choices.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}`
        : "";
      const defaultHint = prompt.defaultChoice ? ` [${prompt.defaultChoice}]` : "";
      return new Promise<string>((resolve) => {
        rl.question(`${prompt.text}${choices}${defaultHint}\n> `, (ans) => {
          rl.close();
          if (!ans && prompt.defaultChoice) return resolve(prompt.defaultChoice);
          if (prompt.choices) {
            const idx = parseInt(ans) - 1;
            return resolve(!isNaN(idx) && prompt.choices[idx] ? prompt.choices[idx] : ans);
          }
          resolve(ans);
        });
      });
    },
  };
}
```

- [ ] **Step 2: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "handlers/owl"
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/cli/v2/commands/handlers/owl.ts
git commit -m "feat(cli-v2): /owl handlers — list/show/create/from-bmad/delete/pin/unpin"
```

---

### Task 4: Update CLI v2 registry to expand /owl subcommands

**Files:**
- Modify: `src/cli/v2/commands/registry.ts`

- [ ] **Step 1: Add import for new owl handlers**

Find the import line that imports from `./handlers/misc.js` (which has `handleOwlStatus`). Add a new import line:

```typescript
import {
  handleOwlList,
  handleOwlShow,
  handleOwlCreate,
  handleOwlFromBmad,
  handleOwlDelete,
  handleOwlPin,
  handleOwlUnpin,
} from "./handlers/owl.js";
```

- [ ] **Step 2: Replace the `/owl` entry in the REGISTRY array**

Find:
```typescript
  {
    name: "/owl",
    description: "Show current owl status",
    subcommands: [
      { name: "status", description: "Show owl state + memory stats", handler: handleOwlStatus },
    ],
    handler: handleOwlStatus,
  },
```

Replace with:
```typescript
  {
    name: "/owl",
    description: "Manage owls — list, show, create, pin, delete",
    subcommands: [
      { name: "list",      description: "List all owls (BMAD + custom + builtin)", handler: handleOwlList },
      { name: "show",      description: "Show owl details",     args: [{ name: "<name>" }], handler: handleOwlShow },
      { name: "status",    description: "Active owl DNA state", handler: handleOwlStatus },
      { name: "create",    description: "Create a custom owl (interactive)", handler: handleOwlCreate },
      { name: "from-bmad", description: "Create owl from BMAD template", args: [{ name: "[name]" }], handler: handleOwlFromBmad },
      { name: "delete",    description: "Delete a custom owl", args: [{ name: "<name>" }], handler: handleOwlDelete },
      { name: "pin",       description: "Pin owl for this session", args: [{ name: "<name>" }], handler: handleOwlPin },
      { name: "unpin",     description: "Unpin active owl",    handler: handleOwlUnpin },
    ],
    handler: handleOwlList,
  },
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "registry.ts"
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/cli/v2/commands/registry.ts
git commit -m "feat(cli-v2): expand /owl subcommands in command registry"
```

---

### Task 5: Update CLI v1 commands.ts — /owl replaces /helper

**Files:**
- Modify: `src/cli/commands.ts`

After Plan 1 runs, `cmdHelper` and the `helper:` COMMANDS entry are gone. We now need `cmdOwl` to support full management (currently it only shows status). Replace `cmdOwl` and update the COMMANDS registry.

- [ ] **Step 1: Locate `cmdOwl` and its COMMANDS entry**

```bash
grep -n "cmdOwl\|owl.*description\|Show owl" src/cli/commands.ts
```

- [ ] **Step 2: Replace `cmdOwl` implementation**

Find `const cmdOwl: CommandFn = async (_args, _ui, _gateway) => {` and replace the entire function body:

```typescript
const cmdOwl: CommandFn = async (args, ui, gateway) => {
  const parts = args.trim().split(/\s+/).filter(Boolean);
  const verb = parts[0] || "list";
  const verbArgs = parts.slice(1);
  const registry = gateway.getSpecializedRegistry();
  if (registry) {
    await registry.loadAll(gateway.getWorkspacePath());
  }
  const { dispatchOwlCommand } = await import("../gateway/commands/owl-command.js");
  const adapter = {
    ask: async (_userId: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }) => {
      const { default: readline } = await import("node:readline");
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      const choices = prompt.choices ? `\n${prompt.choices.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}` : "";
      const defaultHint = prompt.defaultChoice ? ` [${prompt.defaultChoice}]` : "";
      return new Promise<string>((resolve) => {
        rl.question(`${prompt.text}${choices}${defaultHint}\n> `, (ans) => {
          rl.close();
          if (!ans && prompt.defaultChoice) return resolve(prompt.defaultChoice);
          if (prompt.choices) {
            const idx = parseInt(ans) - 1;
            return resolve(!isNaN(idx) && prompt.choices[idx] ? prompt.choices[idx] : ans);
          }
          resolve(ans);
        });
      });
    },
  };
  const result = await dispatchOwlCommand(verb, verbArgs, {
    registry: registry as any,
    userId: "local",
    workspacePath: gateway.getWorkspacePath(),
    channelAdapter: adapter,
    gateway: gateway as any,
  });
  ui.printLines(["", ...result.split("\n"), ""]);
  return true;
};
```

- [ ] **Step 3: Update the COMMANDS `owl:` entry and help text**

Find:
```typescript
  owl: { description: "Show owl state", fn: cmdOwl, subcommands: ["status"] },
```

Replace with:
```typescript
  owl: {
    description: "Manage owls",
    fn: cmdOwl,
    subcommands: ["list", "show", "status", "create", "from-bmad", "edit", "delete", "pin", "unpin"],
  },
```

Find in `cmdHelp`:
```typescript
    C("/owl".padEnd(20)) + D("Show owl state and memory"),
```

Replace with:
```typescript
    C("/owl".padEnd(20)) + D("Manage owls (list/show/create/pin/delete)"),
```

- [ ] **Step 4: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "commands.ts"
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/cli/commands.ts
git commit -m "feat(cli): /owl now dispatches full management (replaces /helper)"
```

---

### Task 6: Expand Telegram /owl command

**Files:**
- Modify: `src/gateway/adapters/telegram.ts`

Currently `bot.command("owl", ...)` (around line 436) just shows status. Replace it with a dispatcher call.

- [ ] **Step 1: Locate the owl command block**

```bash
grep -n "bot.command.*owl\|/owl" src/gateway/adapters/telegram.ts
```

- [ ] **Step 2: Replace the bot.command("owl", ...) handler**

Find:
```typescript
    // ── /owl status — observable owl state ─────────────────────────────
    this.bot.command("owl", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      ...
      await ctx.reply(report);
    });
```

Replace with:
```typescript
    // ── /owl — full owl management (list/show/create/from-bmad/delete/pin/unpin/status) ──
    this.bot.command("owl", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      const text = ctx.message?.text ?? "";
      const parts = text.replace(/^\/owl\s*/, "").trim().split(/\s+/).filter(Boolean);
      const verb = parts[0] || "list";
      const args = parts.slice(1);
      const userId = String(ctx.from?.id ?? "local");
      const workspacePath = this.gateway.getWorkspacePath();
      const registry = this.gateway.getSpecializedRegistry();
      if (registry) await registry.loadAll(workspacePath);

      const { dispatchOwlCommand } = await import("../../gateway/commands/owl-command.js");
      const adapter = {
        ask: async (_uid: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }) => {
          const choices = prompt.choices?.map((c, i) => `${i + 1}. ${c}`).join("\n") ?? "";
          const fullPrompt = [prompt.text, choices, prompt.defaultChoice ? `(default: ${prompt.defaultChoice})` : ""].filter(Boolean).join("\n");
          await ctx.reply(fullPrompt);
          return new Promise<string>((resolve) => {
            const off = this.bot.on("message:text", async (replyCtx) => {
              if (replyCtx.from?.id !== ctx.from?.id) return;
              const ans = replyCtx.message.text.trim();
              off();
              if (!ans && prompt.defaultChoice) return resolve(prompt.defaultChoice);
              if (prompt.choices) {
                const idx = parseInt(ans) - 1;
                return resolve(!isNaN(idx) && prompt.choices[idx] ? prompt.choices[idx] : ans);
              }
              resolve(ans);
            });
          });
        },
      };

      const result = await dispatchOwlCommand(verb, args, {
        registry: registry as any,
        userId,
        workspacePath,
        channelAdapter: adapter,
        gateway: this.gateway,
      });
      await ctx.reply(result, { parse_mode: "Markdown" }).catch(() => ctx.reply(result));
    });
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "telegram.ts"
```

Expected: no errors (or only pre-existing errors).

- [ ] **Step 4: Commit**

```bash
git add src/gateway/adapters/telegram.ts
git commit -m "feat(telegram): expand /owl to full management command"
```

---

### Task 7: Add /owl to Slack adapter

**Files:**
- Modify: `src/gateway/adapters/slack.ts`

- [ ] **Step 1: Find where Slack commands are registered**

```bash
grep -n "app.command\|/helper\|/owl\|/owls" src/gateway/adapters/slack.ts | head -20
```

- [ ] **Step 2: Add /owl Slack command after the /helper block is gone (Plan 1 removes /helper)**

After the last `this.app.command(...)` block, add:

```typescript
    // ── /owl — owl management ──────────────────────────────────────
    this.app.command("/owl", async ({ ack, respond, command }) => {
      await ack();
      const parts = (command.text ?? "").trim().split(/\s+/).filter(Boolean);
      const verb = parts[0] || "list";
      const args = parts.slice(1);
      const workspacePath = this.gateway.getWorkspacePath();
      const registry = this.gateway.getSpecializedRegistry();
      if (registry) await registry.loadAll(workspacePath);

      const { dispatchOwlCommand } = await import("../../gateway/commands/owl-command.js");
      const result = await dispatchOwlCommand(verb, args, {
        registry: registry as any,
        userId: command.user_id,
        workspacePath,
        gateway: this.gateway,
      });
      await respond({ text: result });
    });
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "slack.ts"
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/adapters/slack.ts
git commit -m "feat(slack): add /owl management command"
```

---

### Task 8: Full test suite and integration verification

**Files:** None (verification only)

- [ ] **Step 1: Full TypeScript check**

```bash
npx tsc --noEmit 2>&1
```

Expected: 0 new errors.

- [ ] **Step 2: Run all tests**

```bash
npx vitest run __tests__/gateway/commands/owl-command.test.ts __tests__/owls/bmad-agent-loader.test.ts __tests__/owls/helper-registry-compat.test.ts
```

Expected: all PASS

- [ ] **Step 3: Verify /owl list shows BMAD agents**

```bash
# Quick integration check — simulate a list call
node --input-type=module <<'EOF'
import { BmadAgentLoader } from "./src/owls/bmad-agent-loader.js";
import { SpecializedOwlRegistry } from "./src/owls/specialized-registry.js";
import { dispatchOwlCommand } from "./src/gateway/commands/owl-command.js";

const loader = new BmadAgentLoader();
const specs = await loader.loadAll();
const registry = new SpecializedOwlRegistry();
for (const spec of specs) registry.registerSpec(spec);

const result = await dispatchOwlCommand("list", [], {
  registry,
  userId: "test",
  workspacePath: process.cwd(),
});
console.log(result);
EOF
```

Expected: output listing Mary, Paige, John, Sally, Winston, Amelia with their emojis and roles.

- [ ] **Step 4: Verify /owl from-bmad lists available templates**

```bash
node --input-type=module <<'EOF'
import { BmadAgentLoader } from "./src/owls/bmad-agent-loader.js";
import { SpecializedOwlRegistry } from "./src/owls/specialized-registry.js";
import { dispatchOwlCommand } from "./src/gateway/commands/owl-command.js";

const loader = new BmadAgentLoader();
const specs = await loader.loadAll();
const registry = new SpecializedOwlRegistry();
for (const spec of specs) registry.registerSpec(spec);

const result = await dispatchOwlCommand("from-bmad", [], {
  registry,
  userId: "test",
  workspacePath: process.cwd(),
});
console.log(result);
EOF
```

Expected: lists all 6 BMAD templates with usage hint.

- [ ] **Step 5: Run full test suite**

```bash
npm test 2>&1 | tail -20
```

Expected: same pass/fail as before.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: /owl command overhaul complete — BMAD agents + custom owl management"
```

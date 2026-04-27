# Organizational Owl Structure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement organizational owl structure where each specialized owl has folder-based specs, credentials, permissions, model config, and skill whitelists.

**Architecture:** Layered registry approach:
- `SpecializedOwlRegistry` (new) loads specs from `workspace/owls/<Name>/specialized_owl.md`
- `OwlRegistry` (existing) continues loading base owls from `OWL.md`
- Credentials accessed via `credentials_get` tool (path-isolated)
- Permissions enforced via self-restriction in system prompt

**Tech Stack:** TypeScript, Node.js fs/promises, matter (gray-matter) for parsing markdown frontmatter

---

## File Structure

**New files:**
- `src/owls/specialized-registry.ts` — SpecializedOwlRegistry class
- `src/tools/credentials.ts` — credentials_get tool
- `src/owls/specialized-parser.ts` — Parser for specialized_owl.md format
- `src/owls/specialized-types.ts` — SpecializedOwlSpec interface

**Modified files:**
- `src/owls/persona.ts` — Add model config and permissions to OwlInstance
- `src/engine/runtime.ts` — Inject constraints into specialistPrompt
- `src/gateway/core.ts` — Load SpecializedOwlRegistry, multi-factor routing
- `src/index.ts` — Register credentials_get tool
- `src/cli/commands.ts` — Add /specialization create wizard

**No new TypeScript files beyond these 4.**

---

## Task 1: SpecializedOwlSpec Interface

**Files:**
- Create: `src/owls/specialized-types.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/owls/specialized-types.test.ts
import { SpecializedOwlSpec } from "../../src/owls/specialized-types.js";

describe("SpecializedOwlSpec", () => {
  it("should have correct interface shape", () => {
    const spec: SpecializedOwlSpec = {
      name: "TradingBot",
      role: "Stock trading assistant",
      emoji: "📈",
      personality: {
        challengeLevel: "high",
        verbosity: "balanced",
        tone: "casual but precise",
      },
      expertise: ["stock market", "portfolio"],
      model: {
        provider: "anthropic",
        model: "claude-sonnet-4-20250514",
        maxTokens: 4096,
      },
      permissions: {
        allowedTools: ["shell", "calculator"],
        deniedTools: ["write", "edit"],
        capabilityConstraints: ["Cannot execute trades directly"],
      },
      routingRules: {
        keywords: ["stock", "trading", "portfolio"],
      },
      skills: {
        allowed: ["trading-strategies"],
      },
      credentialsPath: "/path/to/credentials",
    };
    expect(spec.name).toBe("TradingBot");
    expect(spec.permissions.deniedTools).toContain("write");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/owls/specialized-types.test.ts`
Expected: FAIL with "cannot find module"

- [ ] **Step 3: Write minimal implementation**

```typescript
// src/owls/specialized-types.ts

export interface SpecializedPersonality {
  challengeLevel: "low" | "medium" | "high" | "relentless";
  verbosity: "concise" | "balanced" | "verbose";
  tone: string;
}

export interface SpecializedModel {
  provider: string;
  model: string;
  maxTokens?: number;
}

export interface SpecializedPermissions {
  allowedTools: string[];
  deniedTools: string[];
  capabilityConstraints: string[];
}

export interface SpecializedRoutingRules {
  keywords: string[];
}

export interface SpecializedSkills {
  allowed: string[];
}

export interface SpecializedOwlSpec {
  name: string;
  role: string;
  emoji: string;
  personality: SpecializedPersonality;
  expertise: string[];
  model: SpecializedModel;
  permissions: SpecializedPermissions;
  routingRules: SpecializedRoutingRules;
  skills: SpecializedSkills;
  credentialsPath?: string;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/owls/specialized-types.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/owls/specialized-types.ts __tests__/owls/specialized-types.test.ts
git commit -m "feat: add SpecializedOwlSpec interface"
```

---

## Task 2: Parser for specialized_owl.md

**Files:**
- Create: `src/owls/specialized-parser.ts`
- Test: `__tests__/owls/specialized-parser.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/owls/specialized-parser.test.ts
import { parseSpecializedOwl } from "../../src/owls/specialized-parser.js";
import { readFileSync } from "node:fs";

describe("parseSpecializedOwl", () => {
  it("should parse a valid specialized_owl.md", () => {
    const content = readFileSync("test-data/trading-owl.md", "utf-8");
    const spec = parseSpecializedOwl(content);
    expect(spec.name).toBe("TradingBot");
    expect(spec.permissions.deniedTools).toContain("write");
  });
});
```

- [ ] **Step 2: Create test data file**

```bash
mkdir -p __tests__/owls/test-data
```

```markdown
# TradingBot

## Identity
name: TradingBot
role: Stock trading assistant
emoji: 📈

## Personality
challengeLevel: high
verbosity: balanced
tone: casual but precise

## Expertise
domains:
  - stock market analysis
  - portfolio management
  - trading strategies

## Model Config
provider: anthropic
model: claude-sonnet-4-20250514
maxTokens: 4096

## Permissions
allowedTools:
  - shell
  - calculator
  - web_search
deniedTools:
  - write
  - edit
  - delete
capabilityConstraints:
  - "Cannot execute trades directly"
  - "Cannot access personal finances outside trading accounts"

## Routing Rules
keywords:
  - stock
  - trading
  - portfolio
  - shares
  - market

## Skills
allowed:
  - trading-strategies
  - market-analysis
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run __tests__/owls/specialized-parser.test.ts`
Expected: FAIL with "cannot find module"

- [ ] **Step 4: Write parser implementation**

```typescript
// src/owls/specialized-parser.ts
import matter from "gray-matter";
import type {
  SpecializedOwlSpec,
  SpecializedPersonality,
  SpecializedModel,
  SpecializedPermissions,
  SpecializedRoutingRules,
  SpecializedSkills,
} from "./specialized-types.js";

export function parseSpecializedOwl(content: string): SpecializedOwlSpec {
  const { data } = matter(content);

  const personality: SpecializedPersonality = {
    challengeLevel: (data.challengeLevel as SpecializedPersonality["challengeLevel"]) ?? "medium",
    verbosity: (data.verbosity as SpecializedPersonality["verbosity"]) ?? "balanced",
    tone: (data.tone as string) ?? "neutral",
  };

  const model: SpecializedModel = {
    provider: (data.provider as string) ?? "openai",
    model: (data.model as string) ?? "gpt-4",
    maxTokens: data.maxTokens as number | undefined,
  };

  const permissions: SpecializedPermissions = {
    allowedTools: Array.isArray(data.allowedTools) ? data.allowedTools : [],
    deniedTools: Array.isArray(data.deniedTools) ? data.deniedTools : [],
    capabilityConstraints: Array.isArray(data.capabilityConstraints)
      ? data.capabilityConstraints
      : [],
  };

  const routingRules: SpecializedRoutingRules = {
    keywords: Array.isArray(data.keywords) ? data.keywords : [],
  };

  const skills: SpecializedSkills = {
    allowed: Array.isArray(data.allowedSkills) ? data.allowedSkills : [],
  };

  return {
    name: (data.name as string) ?? "Unknown",
    role: (data.role as string) ?? "",
    emoji: (data.emoji as string) ?? "🦉",
    personality,
    expertise: Array.isArray(data.domains) ? data.domains : [],
    model,
    permissions,
    routingRules,
    skills,
  };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run __tests__/owls/specialized-parser.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/owls/specialized-parser.ts __tests__/owls/specialized-parser.test.ts __tests__/owls/test-data/
git commit -m "feat: add specialized_owl.md parser"
```

---

## Task 3: SpecializedOwlRegistry

**Files:**
- Create: `src/owls/specialized-registry.ts`
- Test: `__tests__/owls/specialized-registry.test.ts`
- Modify: `src/gateway/types.ts` — Add specializedRegistry to GatewayContext

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/owls/specialized-registry.test.ts
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";

describe("SpecializedOwlRegistry", () => {
  const testWorkspace = "test-workspace/owls";

  it("should load specialized owls from workspace", async () => {
    const registry = new SpecializedOwlRegistry();
    await registry.loadAll(testWorkspace);
    const owl = registry.get("TradingBot");
    expect(owl).toBeDefined();
    expect(owl?.role).toBe("Stock trading assistant");
  });

  it("should find owls by keyword", async () => {
    const registry = new SpecializedOwlRegistry();
    await registry.loadAll(testWorkspace);
    const owls = registry.getByKeyword("trading");
    expect(owls.length).toBeGreaterThan(0);
  });

  it("should find owls by expertise", async () => {
    const registry = new SpecializedOwlRegistry();
    await registry.loadAll(testWorkspace);
    const owls = registry.getByExpertise("stock market");
    expect(owls.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Create test workspace structure**

```bash
mkdir -p __tests__/owls/test-workspace/owls/TradingBot
```

```markdown
# TradingBot

## Identity
name: TradingBot
role: Stock trading assistant
emoji: 📈

## Personality
challengeLevel: high
verbosity: balanced
tone: casual but precise

## Expertise
domains:
  - stock market analysis
  - portfolio management

## Model Config
provider: anthropic
model: claude-sonnet-4-20250514
maxTokens: 4096

## Permissions
allowedTools:
  - shell
  - calculator
deniedTools:
  - write
  - edit

## Routing Rules
keywords:
  - stock
  - trading
  - portfolio

## Skills
allowed:
  - trading-strategies
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run __tests__/owls/specialized-registry.test.ts`
Expected: FAIL with "cannot find module"

- [ ] **Step 4: Write SpecializedOwlRegistry (follow OwlRegistry pattern)**

```typescript
// src/owls/specialized-registry.ts
import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { SpecializedOwlSpec } from "./specialized-types.js";
import { parseSpecializedOwl } from "./specialized-parser.js";
import { log } from "../logger.js";

export class SpecializedOwlRegistry {
  private specs: Map<string, SpecializedOwlSpec> = new Map();

  async loadAll(workspacePath: string): Promise<void> {
    const owlsDir = join(workspacePath, "owls");
    if (!existsSync(owlsDir)) {
      log.engine.info("[SpecializedOwlRegistry] No owls directory found");
      return;
    }

    let entries: string[];
    try {
      const dirEntries = await readdir(owlsDir, { withFileTypes: true });
      entries = dirEntries.filter((e) => e.isDirectory()).map((e) => e.name);
    } catch {
      return;
    }

    for (const entry of entries) {
      const specPath = join(owlsDir, entry, "specialized_owl.md");
      if (!existsSync(specPath)) continue;

      try {
        const raw = await readFile(specPath, "utf-8");
        const spec = parseSpecializedOwl(raw);
        // Set credentialsPath to the credentials folder
        spec.credentialsPath = join(owlsDir, entry, "credentials");
        this.specs.set(spec.name.toLowerCase(), spec);
        log.engine.info(`[SpecializedOwlRegistry] Loaded ${spec.name}`);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.engine.warn(`[SpecializedOwlRegistry] Failed to load ${entry}: ${msg}`);
      }
    }
  }

  get(name: string): SpecializedOwlSpec | undefined {
    return this.specs.get(name.toLowerCase());
  }

  listAll(): SpecializedOwlSpec[] {
    return Array.from(this.specs.values());
  }

  getByExpertise(domain: string): SpecializedOwlSpec[] {
    const lower = domain.toLowerCase();
    return this.listAll().filter((spec) =>
      spec.expertise.some((e) => e.toLowerCase().includes(lower)),
    );
  }

  getByKeyword(keyword: string): SpecializedOwlSpec[] {
    const lower = keyword.toLowerCase();
    return this.listAll().filter((spec) =>
      spec.routingRules.keywords.some((k) => k.toLowerCase().includes(lower)),
    );
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run __tests__/owls/specialized-registry.test.ts`
Expected: PASS

- [ ] **Step 6: Add specializedRegistry to GatewayContext (modify existing types.ts)**

Read `src/gateway/types.ts` first to find where to add.

```typescript
// In GatewayContext interface, add:
specializedRegistry?: SpecializedOwlRegistry;
```

- [ ] **Step 7: Commit**

```bash
git add src/owls/specialized-registry.ts src/gateway/types.ts
git add __tests__/owls/specialized-registry.test.ts __tests__/owls/test-workspace/
git commit -m "feat: add SpecializedOwlRegistry for folder-based owl specs"
```

---

## Task 4: credentials_get Tool

**Files:**
- Create: `src/tools/credentials.ts`
- Test: `__tests__/tools/credentials.test.ts`
- Modify: `src/index.ts` — Register the tool

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/tools/credentials.test.ts
import { CredentialsTool } from "../../src/tools/credentials.js";

describe("CredentialsTool", () => {
  it("should have correct definition shape", () => {
    expect(CredentialsTool.definition.name).toBe("credentials_get");
    expect(CredentialsTool.definition.description).toContain("credential");
  });

  it("should return credential value when key exists", async () => {
    const result = await CredentialsTool.execute(
      { key: "TEST_KEY", owlName: "TradingBot" },
      { cwd: "__tests__/tools/test-data" },
    );
    expect(result).toContain("test_value");
  });

  it("should return error when key not found", async () => {
    const result = await CredentialsTool.execute(
      { key: "NONEXISTENT", owlName: "TradingBot" },
      { cwd: "__tests__/tools/test-data" },
    );
    expect(result).toContain("not found");
  });
});
```

- [ ] **Step 2: Create test data**

```bash
mkdir -p __tests__/tools/test-data/TradingBot/credentials
```

```
# TradingBot Credentials
TEST_KEY=test_value
API_TOKEN=secret_token
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run __tests__/tools/credentials.test.ts`
Expected: FAIL with "cannot find module"

- [ ] **Step 4: Write credentials tool (follow ShellTool pattern)**

```typescript
// src/tools/credentials.ts
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

export const CredentialsTool: ToolImplementation = {
  definition: {
    name: "credentials_get",
    description:
      "Retrieve a credential value by key name. " +
      "Each specialized owl can only access credentials in its own folder. " +
      "Use this when you need an API key or token to perform an action.",
    parameters: {
      type: "object",
      properties: {
        key: {
          type: "string",
          description: "The credential key to retrieve (e.g., ALPHA_VANTAGE_KEY)",
        },
        owlName: {
          type: "string",
          description: "The owl name whose credentials to access",
        },
      },
      required: ["key", "owlName"],
    },
  },
  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const key = args.key as string;
    const owlName = args.owlName as string;

    if (!key || !owlName) {
      return JSON.stringify({ error: "Missing key or owlName parameter" });
    }

    // Path isolation: only allow access to specific owl's credentials folder
    const credentialsPath = join(context.cwd, "workspace", "owls", owlName, "credentials", "secrets.md");

    // Security: validate path is within workspace
    const resolvedPath = credentialsPath;
    if (!resolvedPath.startsWith(join(context.cwd, "workspace"))) {
      log.tool.error(`[CredentialsTool] Path traversal attempt: ${resolvedPath}`);
      return JSON.stringify({ error: "Access denied: invalid owl name" });
    }

    if (!existsSync(resolvedPath)) {
      return JSON.stringify({ error: `Credentials file not found for ${owlName}` });
    }

    try {
      const content = readFileSync(resolvedPath, "utf-8");
      const lines = content.split("\n");
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("#") || trimmed === "") continue;
        const [k, ...vParts] = trimmed.split("=");
        if (k.trim() === key) {
          return JSON.stringify({ key, value: vParts.join("=").trim() });
        }
      }
      return JSON.stringify({ error: `Key '${key}' not found in ${owlName} credentials` });
    } catch (error) {
      log.tool.error(`[CredentialsTool] Failed to read credentials: ${error}`);
      return JSON.stringify({ error: "Failed to read credentials" });
    }
  },
};
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run __tests__/tools/credentials.test.ts`
Expected: PASS

- [ ] **Step 6: Register tool in index.ts**

Read `src/index.ts` to find where toolRegistry.registerAll is called, add CredentialsTool to the list.

- [ ] **Step 7: Commit**

```bash
git add src/tools/credentials.ts __tests__/tools/credentials.test.ts __tests__/tools/test-data/
# Also add CredentialsTool to toolRegistry.registerAll in src/index.ts
git commit -m "feat: add credentials_get tool for secure credential access"
```

---

## Task 5: Constraints Injection into specialistPrompt

**Files:**
- Modify: `src/engine/runtime.ts` — Add constraints to specialistPrompt section
- Modify: `src/owls/persona.ts` — Add model config to OwlInstance

- [ ] **Step 1: Read current buildSystemPrompt to understand where to inject**

- [ ] **Step 2: Modify buildSystemPrompt to add ## Constraints section**

In `buildSystemPrompt()`, after the `## Specialist Context` section (around line 2635), add constraints:

```typescript
// After specialistPrompt injection, add:
if (owl.specialistPrompt?.trim()) {
  prompt += `\n## Specialist Context\n\n${owl.specialistPrompt.trim()}\n`;
}

// Add constraints section if specialist has permissions
if (owl.specialistRoutingRules || owl.specialistPermissions) {
  prompt += `\n## Your Constraints\n`;
  if (owl.specialistPermissions) {
    const perms = owl.specialistPermissions;
    if (perms.allowedTools?.length > 0) {
      prompt += `- You can ONLY use these tools: ${perms.allowedTools.join(", ")}\n`;
    }
    if (perms.deniedTools?.length > 0) {
      prompt += `- You must NEVER use these tools: ${perms.deniedTools.join(", ")}\n`;
    }
    if (perms.capabilityConstraints?.length > 0) {
      for (const constraint of perms.capabilityConstraints) {
        prompt += `- ${constraint}\n`;
      }
    }
  }
  prompt += "\n";
}
```

- [ ] **Step 3: Update OwlInstance to include specialistRoutingRules and specialistPermissions**

In `src/owls/persona.ts`:

```typescript
export interface OwlInstance {
  persona: OwlPersona;
  dna: OwlDNA;
  specialistPrompt?: string;
  specialistRoutingRules?: string[];
  specialistPermissions?: {
    allowedTools: string[];
    deniedTools: string[];
    capabilityConstraints: string[];
  };
  specialistModelConfig?: {
    provider: string;
    model: string;
    maxTokens?: number;
  };
}
```

- [ ] **Step 4: Build and verify**

Run: `npm run build`

- [ ] **Step 5: Commit**

```bash
git add src/engine/runtime.ts src/owls/persona.ts
git commit -m "feat: inject permissions constraints into specialist system prompt"
```

---

## Task 6: Gateway Wiring — Load SpecializedOwlRegistry

**Files:**
- Modify: `src/gateway/core.ts` — Initialize and load SpecializedOwlRegistry

- [ ] **Step 1: Read gateway core.ts to find initialization section**

- [ ] **Step 2: Add SpecializedOwlRegistry initialization**

After OwlRegistry is loaded, load SpecializedOwlRegistry:

```typescript
import { SpecializedOwlRegistry } from "./owls/specialized-registry.js";

// In gateway initialization (after owlRegistry.loadAll):
const specializedRegistry = new SpecializedOwlRegistry();
await specializedRegistry.loadAll(workspacePath);
this.ctx.specializedRegistry = specializedRegistry;
```

- [ ] **Step 3: Build and verify**

Run: `npm run build`

- [ ] **Step 4: Commit**

```bash
git add src/gateway/core.ts
git commit -m "feat: load SpecializedOwlRegistry on startup"
```

---

## Task 7: Multi-factor Routing Enhancement

**Files:**
- Modify: `src/gateway/core.ts` — Enhance routing to use SpecializedOwlRegistry
- Modify: `src/routing/secretary.ts` — Add multi-factor matching

- [ ] **Step 1: Read current SecretaryRouter.route() implementation**

- [ ] **Step 2: Modify gateway routing section to use SpecializedOwlRegistry**

When SecretaryRouter returns specialist, look up spec from SpecializedOwlRegistry and merge:

```typescript
// In gateway core.ts, after SecretaryRouter.route():
if (routingDecision.type === "specialist") {
  const specializedOwl = routingDecision.owl;
  const spec = this.ctx.specializedRegistry?.get(specializedOwl.name);
  
  // Get base owl
  const baseOwl = this.ctx.owlRegistry?.get(specializedOwl.name)
    ?? this.ctx.owlRegistry?.getDefault()
    ?? this.ctx.owl;
  
  // Merge with spec constraints
  engineCtx.owl = {
    ...baseOwl,
    specialistPrompt: specializedOwl.personalityPrompt,
    specialistRoutingRules: spec?.routingRules.keywords ?? specializedOwl.routingRules,
    specialistPermissions: spec?.permissions,
    specialistModelConfig: spec?.model,
  };
  activeOwlName = specializedOwl.name;
}
```

- [ ] **Step 3: Also handle explicit @OwlName mention**

Add at start of message handling:

```typescript
// Check for explicit @OwlName mention
const explicitMention = text.match(/^@(\w+)\s+(.+)/);
if (explicitMention && this.ctx.specializedRegistry) {
  const [, owlName, remainingMessage] = explicitMention;
  const spec = this.ctx.specializedRegistry.get(owlName);
  if (spec) {
    // Direct invoke
    const baseOwl = this.ctx.owlRegistry?.getDefault() ?? this.ctx.owl;
    engineCtx.owl = {
      ...baseOwl,
      specialistPrompt: `You are ${spec.name}, ${spec.role}`,
      specialistRoutingRules: spec.routingRules.keywords,
      specialistPermissions: spec.permissions,
      specialistModelConfig: spec.model,
    };
    activeOwlName = spec.name;
    // Use remainingMessage as the actual user message
  }
}
```

- [ ] **Step 4: Build and verify**

Run: `npm run build`

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts src/routing/secretary.ts
git commit -m "feat: multi-factor routing with SpecializedOwlRegistry"
```

---

## Task 8: Update CLI Create Wizard for Full Spec

**Files:**
- Modify: `src/cli/commands.ts` — Enhance /specialization create to ask all questions

- [ ] **Step 1: Read current /specialization create implementation**

- [ ] **Step 2: Replace with comprehensive wizard**

The wizard should ask:
1. Role: "What should this owl do?"
2. Personality: challenge level, verbosity, tone
3. Expertise: topics/keywords
4. Permissions (Tools): allowed and denied
5. Permissions (Capabilities): what it should NOT do
6. Model Config: provider, model, max tokens
7. Credentials: API keys (optional)
8. Skills: allowed skills

After all questions, generate and display `specialized_owl.md` preview, confirm, then create files.

**Note:** This is a CLI wizard implementation. The key is to follow existing CLI patterns in commands.ts (using `ui.printLines()`, `ui.printInfo()`, and reading from CLI input).

- [ ] **Step 3: Build and verify**

Run: `npm run build`

- [ ] **Step 4: Commit**

```bash
git add src/cli/commands.ts
git commit -m "feat: enhance /specialization create with full wizard"
```

---

## Implementation Order

1. Task 1: SpecializedOwlSpec Interface
2. Task 2: Parser for specialized_owl.md
3. Task 3: SpecializedOwlRegistry
4. Task 4: credentials_get Tool
5. Task 5: Constraints Injection
6. Task 6: Gateway Wiring
7. Task 7: Multi-factor Routing
8. Task 8: CLI Wizard Enhancement

---

## Testing Strategy

- Unit tests for parser, registry, tool (vitest)
- Integration test for full routing flow
- Manual test for CLI wizard

---

## Spec Coverage Check

| Spec Section | Task |
|--------------|------|
| Storage structure (folder per owl) | Task 3 |
| specialized_owl.md format | Task 2 |
| credentials/secrets.md | Task 4 |
| SpecializedOwlSpec interface | Task 1 |
| SpecializedOwlRegistry | Task 3 |
| credentials_get tool | Task 4 |
| Permission enforcement (self-restriction) | Task 5 |
| Multi-factor routing | Task 7 |
| Explicit mention (@OwlName) | Task 7 |
| Creation wizard | Task 8 |
| Model config override | Task 5 |

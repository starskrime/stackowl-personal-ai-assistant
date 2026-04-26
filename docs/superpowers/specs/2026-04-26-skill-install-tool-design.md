# Skill Install Tool — Implementation Design

**Goal:** Expose skill installation to the StackOwl LLM assistant as a callable tool, so users can say "install skill X from ClawHub" and the assistant installs it and hot-reloads the registry in the same session.

**Architecture:** One new tool file (`src/tools/skill-install.ts`) + one registration line in `src/index.ts`. Wraps existing `SkillInstaller` and `ClawHubClient` — no changes to those. Hot-reload via `SkillsRegistry.loadFromDirectory()` which is already available in `EngineContext.skillsRegistry`.

**Tech Stack:** TypeScript, existing `SkillInstaller`, `ClawHubClient`, `parseInstallSource`, `SkillsRegistry`

---

## Section 1 — Tool Definition

**File:** `src/tools/skill-install.ts`

Class: `SkillInstallTool` (constructor takes `workspacePath: string`).

LLM-visible name: `install_skill`

LLM-visible description:
> Install a skill from ClawHub, GitHub, or a local path. Sources: `clawhub:user/skill-name` or bare slug `user/skill-name` (defaults to ClawHub); `github:user/repo/path/to/skill` or `github:user/repo/path@branch`; `./relative/path` or `/absolute/path`. After install, the skill is immediately active — no restart needed.

Parameters:
```
source: string  (required) — the install source string
```

**Execute logic:**
1. Call `parseInstallSource(source)` → typed `InstallSource`
2. Determine `skillDir = join(workspacePath, "skills", skillName)`
3. Route by type:
   - `github` → `new SkillInstaller(workspacePath).fromGitHub(rawUrl, skillName)`
   - `local` → `new SkillInstaller(workspacePath).fromLocal(localPath)`
   - `clawhub` → `new ClawHubClient().install(slug, join(workspacePath, "skills"))`
4. Hot-reload: `await context.engineContext?.skillsRegistry?.loadFromDirectory(skillDir)`
5. Return `"✓ Installed <skillName> and loaded into this session."` on success, or a descriptive error string on failure (never throw — catch and return error as string so the LLM can relay it to the user).

**Fallback:** If `skillsRegistry` is absent from context, skip hot-reload and append `" Restart the assistant to activate."` to the success message.

---

## Section 2 — Registration

**File:** `src/index.ts`

Add import near the other tool imports:
```typescript
import { SkillInstallTool } from "./tools/skill-install.js";
```

Add to the `toolRegistry.registerAll([...])` block (first batch, around line 265):
```typescript
new SkillInstallTool(workspacePath),
```

Category: `"files"` (disk write operation, no network permission gate needed beyond what `fetch` already has).

---

## Out of Scope

- Uninstall via LLM tool (separate feature)
- Version pinning at install time
- Listing installed skills via tool (already handled by `/skills` Telegram command)
- Any changes to `ClawHubClient` or `SkillInstaller`

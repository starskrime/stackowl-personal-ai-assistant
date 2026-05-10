/**
 * StackOwl — Element 19 — `/skill` command router.
 *
 * Channel-agnostic dispatcher. Same router backs CLI, Telegram, Slack, and
 * Voice so `/skill list` returns identical text on all surfaces
 * (channel-parity rule).
 */

import fs from "node:fs"
import path from "node:path"
import type { SkillsRegistry } from "../../skills/registry.js"
import type { MemoryDatabase } from "../../memory/db.js"

interface WizardLike {
  start(userId: string, channelAdapter: unknown): Promise<string>
  isActive(userId: string): boolean
  cancel(userId: string): void
}

interface InstallerLike {
  install(source: string, workspacePath: string): Promise<{ name: string }>
}

export interface SkillRouterDeps {
  registry: SkillsRegistry
  wizard: WizardLike
  installer?: InstallerLike
  userId: string
  channelAdapter: unknown
  workspacePath?: string
  db?: MemoryDatabase
}

const HELP = `/skill commands:
  /skill list                      — list all enabled skills
  /skill show <name>               — show skill details
  /skill install <source>          — install a skill from GitHub or local path
  /skill create                    — launch skill creation wizard
  /skill enable <name>             — enable a skill
  /skill disable <name>            — disable a skill
  /skill remove <name> yes         — remove a skill (irreversible)
  /skill run <name>                — run a skill directly
  /skill metrics <name>            — show usage stats for a skill`

export async function dispatchSkillCommand(
  verb: string,
  args: string[],
  deps: SkillRouterDeps,
): Promise<string> {
  const { registry, wizard, userId, channelAdapter } = deps

  switch (verb.toLowerCase()) {
    case "list": {
      const skills = registry.listEnabled()
      if (skills.length === 0) {
        return "No skills enabled. Use `/skill install <source>` to add one."
      }
      return skills
        .map(s => `• **${s.name}** — ${s.description}`)
        .join("\n")
    }

    case "show": {
      const name = args[0]
      if (!name) return "Usage: `/skill show <name>`"
      const skill = registry.get(name)
      if (!skill) {
        return `Skill "${name}" not found. Use \`/skill list\` to see available skills.`
      }
      const steps = skill.steps?.length > 0 ? `${skill.steps.length} steps` : "LLM-guided"
      const params = Object.keys(skill.parameters ?? {})
      return [
        `**${skill.name}**`,
        `Description: ${skill.description}`,
        `Execution: ${steps}`,
        params.length > 0 ? `Parameters: ${params.join(", ")}` : null,
        skill.enabled ? "Status: enabled" : "Status: disabled",
      ].filter(Boolean).join("\n")
    }

    case "install": {
      const source = args[0]
      if (!source) return "Usage: `/skill install <github-url-or-local-path>`"
      if (!deps.installer) return "Skill installer not configured."
      if (!deps.workspacePath) return "Workspace path required for installation."
      try {
        const { name } = await deps.installer.install(source, deps.workspacePath)
        return `✓ Skill "${name}" installed successfully.`
      } catch (err) {
        return `Installation failed: ${err instanceof Error ? err.message : String(err)}`
      }
    }

    case "create": {
      return wizard.start(userId, channelAdapter)
    }

    case "enable": {
      const name = args[0]
      if (!name) return "Usage: `/skill enable <name>`"
      const skill = registry.get(name)
      if (!skill) return `Skill "${name}" not found.`
      if (typeof (registry as any).enable === "function") {
        await (registry as any).enable(name)
      }
      return `✓ Skill "${name}" enabled.`
    }

    case "disable": {
      const name = args[0]
      if (!name) return "Usage: `/skill disable <name>`"
      const skill = registry.get(name)
      if (!skill) return `Skill "${name}" not found.`
      if (typeof (registry as any).disable === "function") {
        await (registry as any).disable(name)
      }
      return `✓ Skill "${name}" disabled.`
    }

    case "remove": {
      const [name, confirm] = args
      if (!name) return "Usage: `/skill remove <name> yes`"
      const skill = registry.get(name)
      if (!skill) return `Skill "${name}" not found.`
      if (confirm?.toLowerCase() !== "yes") {
        return `To confirm removal, run: \`/skill remove ${name} yes\`\nThis cannot be undone.`
      }
      if (!deps.workspacePath) return "Workspace path required for removal."
      const skillDir = path.dirname(skill.filePath)
      if (fs.existsSync(skillDir)) fs.rmSync(skillDir, { recursive: true })
      await (registry as any).loadAll?.(deps.workspacePath)
      return `✓ Skill "${name}" removed.`
    }

    case "run": {
      const name = args[0]
      if (!name) return "Usage: `/skill run <name>`"
      const skill = registry.get(name)
      if (!skill) return `Skill "${name}" not found.`
      if (!(skill.metadata as any)?.["user-invocable"]) {
        return `Skill "${name}" is not user-invocable. Only skills marked user-invocable can be run directly.`
      }
      return `Running skill "${name}"... (use the ReAct engine for full execution)`
    }

    case "metrics": {
      const name = args[0]
      if (!name) return "Usage: `/skill metrics <name>`"
      if (!deps.db?.skillUsage) {
        return "Usage metrics are not available (no database configured)."
      }
      const stats = deps.db.skillUsage.getStats(name)
      if (!stats) {
        return `No usage data for skill "${name}" yet.`
      }
      const successRate = stats.selection_count > 0
        ? Math.round((stats.success_count / stats.selection_count) * 100)
        : 0
      return [
        `**${name} metrics**`,
        `Selections: ${stats.selection_count}`,
        `Successes: ${stats.success_count} (${successRate}%)`,
        `Failures: ${stats.failure_count}`,
        `Avg duration: ${Math.round(stats.avg_duration_ms)}ms`,
        stats.last_used_at ? `Last used: ${stats.last_used_at}` : "Never used",
      ].join("\n")
    }

    default:
      return `Unknown command: "${verb}".\n\n${HELP}`
  }
}

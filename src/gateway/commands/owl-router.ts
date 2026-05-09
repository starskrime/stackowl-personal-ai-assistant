/**
 * StackOwl — Element 17 — `/helper` command router.
 *
 * Channel-agnostic dispatcher. Same router backs CLI, Telegram, Slack, and
 * Voice so `/helper list` returns identical text on all surfaces
 * (channel-parity rule).
 */

import fs from "node:fs"
import path from "node:path"
import type { HelperRegistry } from "../../owls/specialized-registry.js"

interface WizardLike {
  start(userId: string, channelAdapter: unknown): Promise<string>
  isActive(userId: string): boolean
  cancel(userId: string): void
}

export interface OwlRouterDeps {
  registry: HelperRegistry
  wizard: WizardLike
  userId: string
  channelAdapter: unknown
  workspacePath?: string
}

const HELP = `/helper commands:
  /helper list                     — list all helpers
  /helper show <name>              — show helper details
  /helper create                   — launch creation wizard
  /helper design <name>            — edit helper design
  /helper capabilities <name>      — edit helper capabilities
  /helper rename <old-name> <new-name>
  /helper delete <name> yes        — delete a helper (irreversible)`

export async function dispatchOwlCommand(
  verb: string,
  args: string[],
  deps: OwlRouterDeps,
): Promise<string> {
  const { registry, wizard, userId, channelAdapter } = deps

  switch (verb.toLowerCase()) {
    case "list": {
      const helpers = registry.listAll()
      if (helpers.length === 0) {
        return "You have no helpers yet. Use `/helper create` to make one."
      }
      return helpers
        .map(h => `• ${h.emoji || "🦉"} **${h.name}** — ${h.role}`)
        .join("\n")
    }

    case "show": {
      const name = args[0]
      if (!name) return "Usage: `/helper show <name>`"
      const spec = registry.get(name)
      if (!spec) {
        return `Helper "${name}" not found. Use \`/helper list\` to see your helpers.`
      }
      const caps = spec.permissions.allowedTools.length > 0
        ? spec.permissions.allowedTools.join(", ")
        : "default"
      const restrictions = spec.permissions.deniedTools.length > 0
        ? spec.permissions.deniedTools.join(", ")
        : "none"
      return [
        `**${spec.emoji || "🦉"} ${spec.name}**`,
        `Role: ${spec.role}`,
        `Style: ${spec.personality.tone}, ${spec.personality.challengeLevel} challenge, ${spec.personality.verbosity} verbosity`,
        spec.expertise.length > 0 ? `Expertise: ${spec.expertise.join(", ")}` : null,
        `Can do: ${caps}`,
        `Restrictions: ${restrictions}`,
        spec.additionalPrompt ? `Notes: ${spec.additionalPrompt}` : null,
      ].filter(Boolean).join("\n")
    }

    case "create": {
      return wizard.start(userId, channelAdapter)
    }

    case "design": {
      const name = args[0]
      if (!name) return "Usage: `/helper design <name>`"
      const spec = registry.get(name)
      if (!spec) return `Helper "${name}" not found.`
      return `Design mode for ${name} is not yet available in this version.`
    }

    case "capabilities": {
      const name = args[0]
      if (!name) return "Usage: `/helper capabilities <name>`"
      const spec = registry.get(name)
      if (!spec) return `Helper "${name}" not found.`
      return `Capabilities update for ${name} is not yet available in this version.`
    }

    case "rename": {
      const [oldName, newName] = args
      if (!oldName || !newName) return "Usage: `/helper rename <old-name> <new-name>`"
      const spec = registry.get(oldName)
      if (!spec) return `Helper "${oldName}" not found.`
      if (!deps.workspacePath) {
        return `Rename requires workspace path configuration.`
      }
      const oldDir = path.join(deps.workspacePath, "owls", oldName)
      const newDir = path.join(deps.workspacePath, "owls", newName)
      if (!fs.existsSync(oldDir)) {
        return `Helper directory for "${oldName}" not found on disk.`
      }
      if (fs.existsSync(newDir)) {
        return `A helper named "${newName}" already exists.`
      }
      fs.renameSync(oldDir, newDir)
      await registry.loadAll(deps.workspacePath)
      return `✓ Renamed "${oldName}" to "${newName}".`
    }

    case "delete": {
      const [name, confirm] = args
      if (!name) return "Usage: `/helper delete <name> yes`"
      const spec = registry.get(name)
      if (!spec) return `Helper "${name}" not found.`
      if (confirm?.toLowerCase() !== "yes") {
        return `To confirm deletion, run: \`/helper delete ${name} yes\`\nThis cannot be undone.`
      }
      if (!deps.workspacePath) {
        return `Delete requires workspace path configuration.`
      }
      const dir = path.join(deps.workspacePath, "owls", name)
      if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true })
      await registry.loadAll(deps.workspacePath)
      return `✓ Helper "${name}" deleted.`
    }

    default:
      return `Unknown command: "${verb}". Available: list, show, create, design, capabilities, rename, delete`
  }
}

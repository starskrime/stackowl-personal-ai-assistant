// src/gateway/wizards/skill-creation.ts
import fs from "node:fs"
import path from "node:path"
import type { ChannelAdapterV2 } from "../adapter-v2.js"

interface WizardSession {
  userId: string
  startedAt: number
}

type WriteFn = (filePath: string, content: string) => void

const SESSION_TIMEOUT_MS = 30 * 60 * 1000 // 30 minutes

export class SkillCreationWizard {
  private sessions = new Map<string, WizardSession>()

  constructor(
    private workspacePath: string,
    _db?: unknown,
    private writeFn: WriteFn = (p, c) => {
      fs.mkdirSync(path.dirname(p), { recursive: true })
      fs.writeFileSync(p, c, "utf-8")
    },
  ) {}

  isActive(userId: string): boolean {
    const session = this.sessions.get(userId)
    if (!session) return false
    if (Date.now() - session.startedAt > SESSION_TIMEOUT_MS) {
      this.sessions.delete(userId)
      return false
    }
    return true
  }

  cancel(userId: string): void {
    this.sessions.delete(userId)
  }

  async start(userId: string, channelAdapter: ChannelAdapterV2): Promise<string> {
    this.sessions.set(userId, { userId, startedAt: Date.now() })
    try {
      return await this.runWizard(userId, channelAdapter)
    } finally {
      this.sessions.delete(userId)
    }
  }

  private async runWizard(userId: string, adapter: ChannelAdapterV2, depth = 0): Promise<string> {
    if (depth >= 10) return "Too many restarts. Please try `/skill create` again."

    // Step 1 — Name
    const name = await adapter.ask(userId, { text: "What should I call your new skill?" })
    if (!name || name.toLowerCase() === "cancel") return "Cancelled."

    // Step 2 — Role/Description
    const description = await adapter.ask(userId, { text: `What does ${name} do? (one sentence)` })

    // Step 3 — Personality/tone
    const personalityChoice = await adapter.ask(userId, {
      text: `Pick an execution style for ${name}:`,
      choices: ["Direct & efficient", "Thorough & detailed", "Interactive & clarifying", "Custom…"],
    })
    let personality = personalityChoice
    if (personalityChoice === "Custom…") {
      personality = await adapter.ask(userId, { text: `Describe the execution style in a few words:` })
    }

    // Step 4 — Capabilities
    const capsChoice = await adapter.ask(userId, {
      text: `What tools can ${name} use?`,
      choices: ["Search the web", "Read files", "Write files", "Run code", "All of the above"],
    })
    const caps = capsChoice === "All of the above"
      ? ["web_search", "read_file", "write_file", "run_shell_command"]
      : [capsChoice.toLowerCase().replace(/[^a-z]+/g, "_")]

    // Step 5 — Restrictions
    const restrictions = await adapter.ask(userId, {
      text: `Anything ${name} should never do?`,
      defaultChoice: "Nothing specific",
    })
    const deniedTools = (restrictions === "Nothing specific" || restrictions === "skip")
      ? []
      : [restrictions]

    // Step 6 — Confirm
    const summary = `${name}: ${description}. Style: ${personality}. Can use: ${capsChoice}.`
    const confirm = await adapter.ask(userId, {
      text: `Creating ${summary} Ready?`,
      choices: ["Yes, create it", "No, start over"],
    })
    if (confirm === "No, start over") {
      return this.runWizard(userId, adapter, depth + 1)
    }

    // Write SKILL.md
    const skillMd = this.buildSkillMd({ name, description, personality, caps, deniedTools })
    const skillPath = path.join(this.workspacePath, "skills", name, "SKILL.md")
    this.writeFn(skillPath, skillMd)

    return `Skill "${name}" created! Use \`/skill run ${name}\` or mention it naturally in conversation.`
  }

  private buildSkillMd(opts: {
    name: string
    description: string
    personality: string
    caps: string[]
    deniedTools: string[]
  }): string {
    const lines: (string | null)[] = [
      "---",
      `name: ${opts.name}`,
      `description: ${opts.description}`,
      `user-invocable: true`,
      `enabled: true`,
      `personality: ${opts.personality.toLowerCase().slice(0, 30)}`,
      `permissions:`,
      `  allowedTools: [${opts.caps.map(c => `"${c}"`).join(", ")}]`,
      `  deniedTools: [${opts.deniedTools.map(d => `"${d}"`).join(", ")}]`,
      `---`,
      ``,
      `You are ${opts.name}. ${opts.description}.`,
      ``,
      `## Instructions`,
      ``,
      `Approach every task with a ${opts.personality.toLowerCase()} style.`,
    ]

    return lines.filter((s): s is string => s !== null).join("\n") + "\n"
  }
}

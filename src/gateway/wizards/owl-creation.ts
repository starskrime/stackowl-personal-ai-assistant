// src/gateway/wizards/owl-creation.ts
import fs from "node:fs"
import path from "node:path"
import type { ChannelAdapterV2 } from "../adapter-v2.js"

interface WizardSession {
  userId: string
  startedAt: number
}

interface RecurringJobsRepo {
  insert(job: {
    id: string
    helper_name: string
    owner_id: string
    schedule: string
    task_description: string
    channel_id: string
  }): void
}

interface WizardDb {
  owlRecurringJobs?: RecurringJobsRepo
}

type WriteFn = (filePath: string, content: string) => void

const SESSION_TIMEOUT_MS = 30 * 60 * 1000 // 30 minutes

export class OwlCreationWizard {
  private sessions = new Map<string, WizardSession>()

  constructor(
    private workspacePath: string,
    private db: WizardDb | undefined,
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

  private async runWizard(userId: string, adapter: ChannelAdapterV2): Promise<string> {
    // Step 1 — Name
    const name = await adapter.ask(userId, { text: "What should I call your new helper?" })
    if (!name || name.toLowerCase() === "cancel") return "Cancelled."

    // Step 2 — Role
    const role = await adapter.ask(userId, { text: `What will ${name} help with?` })

    // Step 3 — Personality
    const personalityChoice = await adapter.ask(userId, {
      text: `Pick a style for ${name}:`,
      choices: ["Warm & patient", "Direct & efficient", "Curious & encouraging", "Formal & precise", "Custom…"],
    })
    let personality = personalityChoice
    if (personalityChoice === "Custom…") {
      personality = await adapter.ask(userId, { text: `Describe ${name}'s style in a few words:` })
    }

    // Step 4 — Capabilities
    const capsChoice = await adapter.ask(userId, {
      text: `What can ${name} do?`,
      choices: ["Search the web", "Read & write files", "Run code", "Manage tasks", "All of the above"],
    })
    const caps = capsChoice === "All of the above"
      ? ["web_search", "read_file", "write_file", "run_shell_command", "manage_tasks"]
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
    const summary = `${name}: ${role}. Style: ${personality}. Can: ${capsChoice}.`
    const confirm = await adapter.ask(userId, {
      text: `Creating ${summary} Ready?`,
      choices: ["Yes, create it", "No, start over"],
    })
    if (confirm === "No, start over") {
      return this.runWizard(userId, adapter)
    }

    // Step 7 — Recurring task (optional)
    const recurringTask = await adapter.ask(userId, {
      text: `Should ${name} work on anything automatically?\nFor example: "Check the news daily at 9am" (or skip)`,
      defaultChoice: "skip",
    })
    const hasRecurring = Boolean(
      recurringTask &&
      recurringTask.toLowerCase() !== "skip" &&
      recurringTask !== "Nothing specific",
    )

    // Write helper.md
    const helperMd = this.buildHelperMd({
      name,
      role,
      personality,
      caps,
      deniedTools,
      recurringTask: hasRecurring ? recurringTask : undefined,
    })
    const helperPath = path.join(this.workspacePath, "owls", name, "helper.md")
    this.writeFn(helperPath, helperMd)

    // Write owl_recurring_jobs row if recurring task provided
    if (hasRecurring && this.db?.owlRecurringJobs) {
      const schedule = this.parseSchedule(recurringTask)
      this.db.owlRecurringJobs.insert({
        id: crypto.randomUUID(),
        helper_name: name,
        owner_id: userId,
        schedule,
        task_description: recurringTask,
        channel_id: "default",
      })
    }

    const recurringNote = hasRecurring
      ? `\nI've also set up "${recurringTask}" for ${name} to handle automatically.`
      : ""
    return `${name} is ready! Say "${name}, ..." anytime to reach them.${recurringNote}`
  }

  private buildHelperMd(opts: {
    name: string
    role: string
    personality: string
    caps: string[]
    deniedTools: string[]
    recurringTask?: string
  }): string {
    const tone = opts.personality.includes("Warm") ? "warm"
      : opts.personality.includes("Direct") ? "professional"
      : opts.personality.includes("Formal") ? "formal"
      : opts.personality.includes("Curious") ? "encouraging"
      : opts.personality.toLowerCase().slice(0, 20)

    const lines: (string | null)[] = [
      "---",
      `name: ${opts.name}`,
      `type: specialist`,
      `role: ${opts.role}`,
      `emoji: 🦉`,
      `personality:`,
      `  challengeLevel: medium`,
      `  verbosity: balanced`,
      `  tone: ${tone}`,
      `expertise: []`,
      `model:`,
      `  provider: anthropic`,
      `  modelId: claude-haiku-4-5-20251001`,
      `permissions:`,
      `  allowedTools: [${opts.caps.map(c => `"${c}"`).join(", ")}]`,
      `  deniedTools: [${opts.deniedTools.map(d => `"${d}"`).join(", ")}]`,
      `  capabilityConstraints: []`,
      `routingRules:`,
      `  keywords: []`,
      `  domains: []`,
      `  priority: 5`,
      `skills:`,
      `  canLearn: false`,
      `  retainedKnowledge: []`,
      opts.recurringTask ? `recurring_task: "${opts.recurringTask}"` : null,
      `---`,
      ``,
      `You are ${opts.name}, a ${opts.role} helper. ${opts.personality}.`,
    ]

    return lines.filter((s): s is string => s !== null).join("\n") + "\n"
  }

  private parseSchedule(description: string): string {
    const hourMatch = description.match(/(\d{1,2})\s*(?:am|pm)/i)
    const hour = hourMatch
      ? parseInt(hourMatch[1]) + (hourMatch[0].toLowerCase().includes("pm") && parseInt(hourMatch[1]) !== 12 ? 12 : 0)
      : 9
    return `${hour.toString().padStart(2, "0")}:00 daily`
  }
}

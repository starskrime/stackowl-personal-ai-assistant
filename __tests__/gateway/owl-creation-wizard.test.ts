// __tests__/gateway/owl-creation-wizard.test.ts
import { describe, it, expect, vi } from "vitest"
import { OwlCreationWizard } from "../../src/gateway/wizards/owl-creation.js"

function makeAdapter(answers: string[]) {
  let i = 0
  return {
    ask: vi.fn().mockImplementation(async () => answers[i++] ?? "skip"),
  }
}

describe("OwlCreationWizard", () => {
  it("completes 6-step flow and writes helper.md", async () => {
    const writes: Array<{ path: string; content: string }> = []
    const wizard = new OwlCreationWizard("/tmp/test-workspace", undefined, (p, c) => writes.push({ path: p, content: c }))

    const adapter = makeAdapter(["Nora", "cooking recipes", "Warm & patient", "Search the web", "Nothing specific", "Yes, create it", "skip"])
    const result = await wizard.start("user1", adapter as any)

    expect(result).toContain("Nora")
    expect(result).toContain("ready")
    expect(writes.length).toBeGreaterThan(0)
    expect(writes[0].content).toContain("name: Nora")
  })

  it("per-userId isolation — isActive returns false for new users", async () => {
    const wizard = new OwlCreationWizard("/tmp/test-workspace2", undefined, () => {})
    expect(wizard.isActive("user1")).toBe(false)
    expect(wizard.isActive("user2")).toBe(false)
  })

  it("cancel clears session state", async () => {
    const wizard = new OwlCreationWizard("/tmp/test-workspace3", undefined, () => {})
    wizard.cancel("user1")
    expect(wizard.isActive("user1")).toBe(false)
  })

  it("'No, start over' restarts wizard", async () => {
    const writes: Array<{ path: string; content: string }> = []
    const wizard = new OwlCreationWizard("/tmp/test-workspace4", undefined, (p, c) => writes.push({ path: p, content: c }))

    // First attempt: answer no at confirm, then complete
    const adapter = makeAdapter([
      "Aria", "cooking", "Warm & patient", "Search the web", "Nothing", "No, start over",
      "Nora", "baking", "Direct & efficient", "All of the above", "No medical advice", "Yes, create it", "skip",
    ])
    const result = await wizard.start("user1", adapter as any)
    expect(result).toContain("Nora")
  })

  it("recurring task writes owl_recurring_jobs row", async () => {
    const jobs: any[] = []
    // Use the correct property name from db — owlRecurringJobs
    const db = { owlRecurringJobs: { insert: vi.fn((j) => jobs.push(j)) } }
    const wizard = new OwlCreationWizard("/tmp/test-workspace5", db as any, () => {})

    const adapter = makeAdapter([
      "Aria", "news", "Direct & efficient", "Search the web", "skip", "Yes, create it",
      "Check news daily at 9am",
    ])
    await wizard.start("user1", adapter as any)
    expect(db.owlRecurringJobs.insert).toHaveBeenCalled()
    expect(jobs[0].task_description).toContain("news")
  })
})

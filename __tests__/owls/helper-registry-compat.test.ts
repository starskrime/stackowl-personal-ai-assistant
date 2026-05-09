import { describe, it, expect, beforeEach, afterEach } from "vitest"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js"
import type { HelperSpec } from "../../src/owls/specialized-types.js"

function makeWorkspace() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "owl-test-"))
  const owlsDir = path.join(root, "owls", "Aria")
  fs.mkdirSync(owlsDir, { recursive: true })
  return { root, owlsDir }
}

describe("HelperRegistry backward compat", () => {
  let workspace = { root: "", owlsDir: "" }

  beforeEach(() => { workspace = makeWorkspace() })
  afterEach(() => { fs.rmSync(workspace.root, { recursive: true, force: true }) })

  it("loads helper.md when present", async () => {
    fs.writeFileSync(path.join(workspace.owlsDir, "helper.md"), `---
name: Aria
type: specialist
role: test helper
emoji: 🤖
challengeLevel: medium
verbosity: balanced
tone: warm
domains: []
provider: anthropic
model: claude-haiku-4-5-20251001
allowedTools: []
deniedTools: []
capabilityConstraints: []
keywords: []
allowedSkills: []
---
`)
    const registry = new SpecializedOwlRegistry()
    await registry.loadAll(workspace.root)
    expect(registry.get("Aria")).toBeDefined()
  })

  it("falls back to specialized_owl.md when helper.md absent", async () => {
    fs.writeFileSync(path.join(workspace.owlsDir, "specialized_owl.md"), `---
name: Aria
type: specialist
role: legacy helper
emoji: 🦉
challengeLevel: low
verbosity: concise
tone: formal
domains: []
provider: anthropic
model: claude-haiku-4-5-20251001
allowedTools: []
deniedTools: []
capabilityConstraints: []
keywords: []
allowedSkills: []
---
`)
    const registry = new SpecializedOwlRegistry()
    await registry.loadAll(workspace.root)
    expect(registry.get("Aria")).toBeDefined()
  })

  it("HelperSpec type alias resolves to SpecializedOwlSpec shape", () => {
    const spec: HelperSpec = {
      name: "Aria",
      type: "specialist",
      role: "test",
      emoji: "🤖",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" },
      expertise: [],
      model: { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: [] },
      skills: { allowed: [] },
      additionalPrompt: "",
    }
    expect(spec.name).toBe("Aria")
  })
})

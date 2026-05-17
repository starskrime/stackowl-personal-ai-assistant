// __tests__/skills/always-injection.test.ts
import { describe, it, expect, vi } from 'vitest'
import { SkillContextInjector } from '../../src/skills/injector.js'
import type { Skill } from '../../src/skills/types.js'

function makeSkill(name: string, always = false): Skill {
  return {
    name,
    description: `desc for ${name}`,
    instructions: `# ${name}`,
    sourcePath: `/workspace/skills/${name}/SKILL.md`,
    enabled: true,
    conditions: [],
    parameters: {},
    steps: [],
    metadata: { name, description: `desc for ${name}`, stackowl: { always } },
    usage: undefined,
  } as unknown as Skill
}

describe('D4: always:true injection', () => {
  it('always-skill appears in getRelevantMatches even when IntentRouter returns nothing', async () => {
    const registry = {
      listEnabled: () => [makeSkill('always-reminder', true), makeSkill('code-helper')],
      get: (name: string) => registry.listEnabled().find(s => s.name === name) ?? null,
    } as any

    const mockRouter = { route: vi.fn().mockResolvedValue([]) }
    const tracker = { recordSelection: vi.fn(), getUsageMultiplier: vi.fn().mockReturnValue(1.0) } as any

    const injector = new SkillContextInjector(registry, {}, undefined, tracker)
    // Replace the internal router with a mock that returns nothing
    ;(injector as any).router = mockRouter

    const matches = await injector.getRelevantMatches('hi there')
    const names = matches.map(m => m.skill.name)

    expect(names).toContain('always-reminder')
    expect(mockRouter.route).toHaveBeenCalled()
  })

  it('always-skill is deduplicated when IntentRouter also returns it', async () => {
    const alwaysSkill = makeSkill('always-reminder', true)
    const registry = {
      listEnabled: () => [alwaysSkill, makeSkill('code-helper')],
      get: (name: string) => registry.listEnabled().find(s => s.name === name) ?? null,
    } as any

    const routerMatch = { skill: alwaysSkill, score: 0.7, method: 'bm25' as const }
    const mockRouter = { route: vi.fn().mockResolvedValue([routerMatch]) }
    const tracker = { recordSelection: vi.fn(), getUsageMultiplier: vi.fn().mockReturnValue(1.0) } as any

    const injector = new SkillContextInjector(registry, {}, undefined, tracker)
    ;(injector as any).router = mockRouter

    const matches = await injector.getRelevantMatches('reminder please')
    const alwaysMatches = matches.filter(m => m.skill.name === 'always-reminder')

    expect(alwaysMatches).toHaveLength(1)
  })

  it('non-always skill is NOT force-included when router returns nothing', async () => {
    const registry = {
      listEnabled: () => [makeSkill('always-reminder', true), makeSkill('code-helper', false)],
      get: (name: string) => registry.listEnabled().find(s => s.name === name) ?? null,
    } as any

    const mockRouter = { route: vi.fn().mockResolvedValue([]) }
    const tracker = { recordSelection: vi.fn(), getUsageMultiplier: vi.fn().mockReturnValue(1.0) } as any

    const injector = new SkillContextInjector(registry, {}, undefined, tracker)
    ;(injector as any).router = mockRouter

    const matches = await injector.getRelevantMatches('hi there')
    const names = matches.map(m => m.skill.name)

    expect(names).not.toContain('code-helper')
  })
})

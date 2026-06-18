// __tests__/skills/invoke-skill-executor.test.ts
import { describe, it, expect } from 'vitest'
import { SkillContextInjector } from '../../src/skills/injector.js'
import type { Skill } from '../../src/skills/types.js'

function makeSkill(name: string, hasSteps = false): Skill {
  return {
    name,
    description: 'test skill',
    instructions: 'Do this carefully.',
    filePath: '/skills/test/SKILL.md',
    enabled: true,
    conditions: [],
    parameters: {},
    steps: hasSteps ? [{ id: 's1', tool: 'echo', args: {}, dependsOn: [] }] : [],
    metadata: { name, description: 'test skill' },
    usage: undefined,
  } as unknown as Skill
}

describe('D5: executeByName', () => {
  it('throws when skill not found', async () => {
    const registry = { listEnabled: () => [], get: () => null } as any
    const injector = new SkillContextInjector(registry, {})
    await expect(injector.executeByName('nonexistent', {})).rejects.toThrow(/not found/i)
  })

  it('returns instructions for unstructured skill (no steps)', async () => {
    const skill = makeSkill('guide', false)
    const registry = { listEnabled: () => [skill], get: () => skill } as any
    const injector = new SkillContextInjector(registry, {})

    const result = await injector.executeByName('guide', {})
    expect(result).toContain('Do this carefully.')
  })
})

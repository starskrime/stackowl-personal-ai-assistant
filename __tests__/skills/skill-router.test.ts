// __tests__/skills/skill-router.test.ts
import { describe, it, expect, vi } from 'vitest'
import { dispatchSkillCommand } from '../../src/gateway/commands/skill-router.js'
import type { SkillRouterDeps } from '../../src/gateway/commands/skill-router.js'
import type { Skill } from '../../src/skills/types.js'

function makeSkill(name: string): Skill {
  return {
    name,
    description: `${name} description`,
    instructions: `# ${name}`,
    filePath: `/skills/${name}/SKILL.md`,
    enabled: true,
    conditions: [],
    parameters: {},
    steps: [],
    metadata: { name, description: `${name} description` },
    usage: undefined,
  } as unknown as Skill
}

function makeDeps(overrides: Partial<SkillRouterDeps> = {}): SkillRouterDeps {
  return {
    registry: {
      listEnabled: () => [makeSkill('web-research'), makeSkill('code-review')],
      get: (name: string) => name === 'web-research' ? makeSkill('web-research') : null,
      enable: vi.fn(),
      disable: vi.fn(),
      remove: vi.fn(),
    } as any,
    wizard: { start: vi.fn().mockResolvedValue('Wizard started'), isActive: vi.fn().mockReturnValue(false), cancel: vi.fn() } as any,
    userId: 'user-1',
    channelAdapter: {} as any,
    workspacePath: '/workspace',
    db: undefined,
    ...overrides,
  }
}

describe('SkillManagementRouter', () => {
  it('list returns skill names', async () => {
    const result = await dispatchSkillCommand('list', [], makeDeps())
    expect(result).toContain('web-research')
    expect(result).toContain('code-review')
  })

  it('show returns skill details', async () => {
    const result = await dispatchSkillCommand('show', ['web-research'], makeDeps())
    expect(result).toContain('web-research')
  })

  it('show returns not found for unknown skill', async () => {
    const result = await dispatchSkillCommand('show', ['nonexistent'], makeDeps())
    expect(result).toContain('not found')
  })

  it('create delegates to wizard', async () => {
    const result = await dispatchSkillCommand('create', [], makeDeps())
    expect(result).toBe('Wizard started')
  })

  it('unknown verb returns help text', async () => {
    const result = await dispatchSkillCommand('bogusverb', [], makeDeps())
    expect(result).toContain('Unknown')
  })
})

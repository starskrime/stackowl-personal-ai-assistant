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

  it('metrics returns usage stats when db has data', async () => {
    const mockStats = {
      selection_count: 10,
      success_count: 8,
      failure_count: 2,
      avg_duration_ms: 345.6,
      last_used_at: '2026-05-09T12:00:00',
    }
    const deps = makeDeps({
      db: {
        skillUsage: {
          getStats: (name: string) => name === 'web-research' ? mockStats : null,
        },
      } as any,
    })
    const result = await dispatchSkillCommand('metrics', ['web-research'], deps)
    expect(result).toContain('web-research metrics')
    expect(result).toContain('Selections: 10')
    expect(result).toContain('Successes: 8 (80%)')
    expect(result).toContain('Failures: 2')
    expect(result).toContain('Avg duration: 346ms')
    expect(result).toContain('Last used: 2026-05-09T12:00:00')
  })

  it('metrics returns no-data message when no stats exist', async () => {
    const deps = makeDeps({
      db: {
        skillUsage: {
          getStats: () => null,
        },
      } as any,
    })
    const result = await dispatchSkillCommand('metrics', ['unknown-skill'], deps)
    expect(result).toContain('No usage data')
  })

  it('metrics returns not-available when db is missing', async () => {
    const result = await dispatchSkillCommand('metrics', ['web-research'], makeDeps({ db: undefined }))
    expect(result).toContain('not available')
  })
})

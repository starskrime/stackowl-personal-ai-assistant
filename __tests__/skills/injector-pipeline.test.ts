// __tests__/skills/injector-pipeline.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SkillContextInjector } from '../../src/skills/injector.js'
import { SkillsRegistry } from '../../src/skills/registry.js'
import type { Skill } from '../../src/skills/types.js'

function makeSkill(name: string, description = `${name} description`): Skill {
  return {
    name,
    description,
    instructions: `Do ${name} tasks carefully.`,
    sourcePath: `/skills/${name}/SKILL.md`,
    enabled: true,
    conditions: [],
    parameters: {},
    steps: [],
    metadata: { name, description },
    usage: undefined,
  } as unknown as Skill
}

function makeRegistry(...skills: Skill[]): SkillsRegistry {
  const reg = new SkillsRegistry()
  for (const s of skills) {
    reg.register(s)
  }
  return reg
}

describe('SkillContextInjector pipeline (G2 integration)', () => {
  it('injectIntoContext returns XML string when skills match', async () => {
    const skill = makeSkill('web-research')
    const registry = makeRegistry(skill)

    const injector = new SkillContextInjector(registry)
    // Override router to return our skill with a known match
    ;(injector as any).router = {
      route: vi.fn().mockResolvedValue([{ skill, score: 0.8, method: 'bm25' }]),
      clearCache: vi.fn(),
      reindex: vi.fn(),
    }

    const result = await injector.injectIntoContext('search for latest AI news')

    // Result must be a string
    expect(typeof result).toBe('string')
    // When a skill matches, XML should contain skill name and instructions
    expect(result).toContain('web-research')
    expect(result).toContain('<context_skills>')
    expect(result).toContain('</context_skills>')
    expect(result).toContain('<skill name="web-research">')
    expect(result).toContain('Do web-research tasks carefully.')
  })

  it('injectIntoContext returns empty string when no skills match', async () => {
    const registry = makeRegistry()

    const injector = new SkillContextInjector(registry)
    ;(injector as any).router = {
      route: vi.fn().mockResolvedValue([]),
      clearCache: vi.fn(),
      reindex: vi.fn(),
    }

    const result = await injector.injectIntoContext('hi there')

    expect(result).toBe('')
  })

  it('injectIntoContext result is suitable to pass as skillsContext to ContextBuilder', async () => {
    // Tests the D2+injector integration: return type must be a string
    // that can flow into ContextBuilder.build(session, callbacks, skillsContext)
    const registry = makeRegistry()
    const injector = new SkillContextInjector(registry)
    ;(injector as any).router = {
      route: vi.fn().mockResolvedValue([]),
      clearCache: vi.fn(),
      reindex: vi.fn(),
    }

    const xml = await injector.injectIntoContext('test message')

    // ContextBuilder accepts string — verify no undefined/null returned
    expect(xml).not.toBeNull()
    expect(xml).not.toBeUndefined()
    expect(typeof xml).toBe('string')
  })

  it('injectIntoContext includes instructions from all matched skills', async () => {
    const skill1 = makeSkill('skill-alpha')
    const skill2 = makeSkill('skill-beta')
    const registry = makeRegistry(skill1, skill2)

    const injector = new SkillContextInjector(registry)
    ;(injector as any).router = {
      route: vi.fn().mockResolvedValue([
        { skill: skill1, score: 0.9, method: 'bm25' },
        { skill: skill2, score: 0.7, method: 'bm25' },
      ]),
      clearCache: vi.fn(),
      reindex: vi.fn(),
    }

    const result = await injector.injectIntoContext('do alpha and beta things')

    expect(result).toContain('skill-alpha')
    expect(result).toContain('skill-beta')
    expect(result).toContain('Do skill-alpha tasks carefully.')
    expect(result).toContain('Do skill-beta tasks carefully.')
  })

  it('always-flagged skills are included regardless of router match', async () => {
    const alwaysSkill = makeSkill('always-present')
    // Mark as always:true
    alwaysSkill.metadata = {
      name: 'always-present',
      description: 'always present description',
      openclaw: { always: true },
    }
    const registry = makeRegistry(alwaysSkill)

    // Router returns empty (no BM25 match) — always-skill should still appear
    const injector = new SkillContextInjector(registry)
    ;(injector as any).router = {
      route: vi.fn().mockResolvedValue([]),
      clearCache: vi.fn(),
      reindex: vi.fn(),
    }

    const result = await injector.injectIntoContext('unrelated query')

    // always-present should still be injected
    expect(result).toContain('always-present')
  })

  it('getRelevantSkills returns skill objects from matches', async () => {
    const skill = makeSkill('code-analyzer')
    const registry = makeRegistry(skill)

    const injector = new SkillContextInjector(registry)
    ;(injector as any).router = {
      route: vi.fn().mockResolvedValue([{ skill, score: 0.75, method: 'bm25' }]),
      clearCache: vi.fn(),
      reindex: vi.fn(),
    }

    const skills = await injector.getRelevantSkills('analyze this code')

    expect(skills).toHaveLength(1)
    expect(skills[0].name).toBe('code-analyzer')
  })
})

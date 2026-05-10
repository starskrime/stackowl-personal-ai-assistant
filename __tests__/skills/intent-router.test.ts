// __tests__/skills/intent-router.test.ts
import { describe, it, expect } from 'vitest'
import { IntentRouter } from '../../src/skills/intent-router.js'
import { SkillsRegistry } from '../../src/skills/registry.js'
import type { Skill } from '../../src/skills/types.js'

function makeSkill(name: string, description: string): Skill {
  return {
    name,
    description,
    instructions: `# ${name}\n\nDo ${name} tasks.`,
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

describe('IntentRouter', () => {
  it('returns empty array when no skills available', async () => {
    const registry = makeRegistry()
    const router = new IntentRouter(registry)
    const results = await router.route('search for news today', 5)
    expect(results).toEqual([])
  })

  it('returns IntentMatch objects with required shape', async () => {
    const skills = [
      makeSkill('web-search', 'search the internet for information'),
      makeSkill('code-review', 'review code for bugs and quality'),
    ]
    const registry = makeRegistry(...skills)
    const router = new IntentRouter(registry)
    const results = await router.route('search for news today', 5)

    // Tier 1 BM25 stub returns equal-scored candidates — all qualify
    expect(results.length).toBeGreaterThan(0)
    for (const r of results) {
      expect(r).toHaveProperty('skill')
      expect(r).toHaveProperty('score')
      expect(r).toHaveProperty('method')
      expect(typeof r.score).toBe('number')
      expect(typeof r.method).toBe('string')
    }
  })

  it('respects maxResults limit', async () => {
    const skills = Array.from({ length: 10 }, (_, i) =>
      makeSkill(`skill-${i}`, `skill number ${i} does things`),
    )
    const registry = makeRegistry(...skills)
    const router = new IntentRouter(registry)
    const results = await router.route('do something', 3)
    expect(results.length).toBeLessThanOrEqual(3)
  })

  it('returns cached results on repeated identical queries', async () => {
    const skills = [
      makeSkill('web-search', 'search the web for information'),
    ]
    const registry = makeRegistry(...skills)
    const router = new IntentRouter(registry)

    const first = await router.route('search for news', 5)
    const second = await router.route('search for news', 5)
    // Cache hit: same reference or same shape
    expect(second).toEqual(first)
  })

  it('skill entries in result reference registered skill objects', async () => {
    const skill = makeSkill('summarizer', 'summarize text content')
    const registry = makeRegistry(skill)
    const router = new IntentRouter(registry)

    const results = await router.route('summarize this document', 5)
    expect(results.length).toBeGreaterThan(0)
    expect(results[0].skill.name).toBe('summarizer')
  })

  it('score is a number between 0 and 1 (inclusive)', async () => {
    const skill = makeSkill('calculator', 'perform mathematical calculations')
    const registry = makeRegistry(skill)
    const router = new IntentRouter(registry)

    const results = await router.route('calculate 2+2', 5)
    for (const r of results) {
      expect(r.score).toBeGreaterThanOrEqual(0)
      expect(r.score).toBeLessThanOrEqual(1)
    }
  })
})

// __tests__/skills/skill-creation-wizard.test.ts
import { describe, it, expect, vi } from 'vitest'
import { SkillCreationWizard } from '../../src/gateway/wizards/skill-creation.js'

function makeAdapter(answers: string[]) {
  let i = 0
  return {
    ask: vi.fn().mockImplementation(async () => answers[i++] ?? 'skip'),
  } as any
}

describe('SkillCreationWizard D9', () => {
  it('creates SKILL.md with provided details', async () => {
    const written: Array<[string, string]> = []
    const wizard = new SkillCreationWizard(
      '/workspace',
      undefined,
      (p, c) => written.push([p, c])
    )

    const adapter = makeAdapter([
      'web-summarizer',   // name
      'Summarize web pages', // role
      'Direct & efficient',  // personality
      'Read files',          // capabilities
      'Nothing specific',    // restrictions
      'Yes, create it',      // confirm
    ])

    const result = await wizard.start('user1', adapter)

    expect(result).toContain('web-summarizer')
    expect(written.length).toBe(1)
    expect(written[0][0]).toContain('web-summarizer')
    expect(written[0][1]).toContain('web-summarizer')
  })

  it('returns cancelled when user says cancel', async () => {
    const wizard = new SkillCreationWizard('/workspace')
    const adapter = makeAdapter(['cancel'])
    const result = await wizard.start('user1', adapter)
    expect(result.toLowerCase()).toContain('cancel')
  })

  it('isActive returns true for active session', () => {
    const wizard = new SkillCreationWizard('/workspace')
    ;(wizard as any).sessions.set('user1', { userId: 'user1', startedAt: Date.now() })
    expect(wizard.isActive('user1')).toBe(true)
  })
})

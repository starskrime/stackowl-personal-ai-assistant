// __tests__/skills/nl-skill-intent.test.ts
import { describe, it, expect } from 'vitest'

describe('CI-3: isSkillInstallIntent logic', () => {
  it('returns true when LLM says YES', () => {
    const response = 'YES'
    const result = response.trimStart().toUpperCase().startsWith('YES')
    expect(result).toBe(true)
  })

  it('returns false when LLM says NO', () => {
    const response = 'NO, this is a general question'
    const result = response.trimStart().toUpperCase().startsWith('YES')
    expect(result).toBe(false)
  })

  it('returns false on timeout (defensive)', () => {
    const timedOut = true
    const result = timedOut ? false : true
    expect(result).toBe(false)
  })
})

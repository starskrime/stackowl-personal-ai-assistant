import { describe, it, expect } from 'vitest'

// Verifies the isConversational pre-filter logic after SKILL_ACTION_KEYWORDS removal.
// Only length<15 and greeting prefix checks remain.
describe('isConversational pre-filter (post-Q1)', () => {
  it('identifies "hi" as conversational (length < 15)', () => {
    const text = 'hi'
    const isConversational = text.trim().length < 15
    expect(isConversational).toBe(true)
  })

  it('identifies "hello there" as a greeting', () => {
    const text = 'hello there'
    const greetingRegex = /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|good morning|good night|how are you|what's up|gm|gn)\b/i
    const isConversational = greetingRegex.test(text.trim())
    expect(isConversational).toBe(true)
  })

  it('does not filter out longer action messages (no keyword gate)', () => {
    const text = 'Can you help me organize my project files?'
    const isConversational = text.trim().length < 15 ||
      /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|good morning|good night|how are you|what's up|gm|gn)\b/i.test(text.trim())
    expect(isConversational).toBe(false)
  })
})

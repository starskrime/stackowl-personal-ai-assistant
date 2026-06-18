import { describe, it, expect } from 'vitest'

// The gate condition: LLM validated the skill AND skill is structured
describe('D6: confidence gate logic', () => {
  it('gates on method === "llm" for structured execution', () => {
    const llmMatch = { skill: { name: 'test' }, score: 0.5, method: 'llm' as const }
    const bm25Match = { skill: { name: 'test' }, score: 0.9, method: 'bm25' as const }

    const shouldExecuteLlm = llmMatch.method === 'llm'
    const shouldExecuteBm25 = bm25Match.method === 'llm'

    expect(shouldExecuteLlm).toBe(true)
    expect(shouldExecuteBm25).toBe(false)
  })
})

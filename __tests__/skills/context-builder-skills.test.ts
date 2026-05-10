// __tests__/skills/context-builder-skills.test.ts
import { describe, it, expect } from 'vitest'
import { ContextBuilder } from '../../src/gateway/handlers/context-builder.js'

describe('ContextBuilder D2: skillsContext passthrough', () => {
  it('passes skillsContext to EngineContext when pipeline is absent', async () => {
    const ctx = { contextPipeline: null } as any
    const builder = new ContextBuilder(ctx, null, null)
    const session = { id: 's1', messages: [] } as any
    const callbacks = {} as any
    const xml = '<context_skills><skill name="test">do it</skill></context_skills>'

    const result = await builder.build(session, callbacks, xml)

    expect(result.skillsContext).toBe(xml)
  })

  it('sets skillsContext to undefined when empty string is passed', async () => {
    const ctx = { contextPipeline: null } as any
    const builder = new ContextBuilder(ctx, null, null)
    const session = { id: 's1', messages: [] } as any
    const callbacks = {} as any

    const result = await builder.build(session, callbacks, '')

    expect(result.skillsContext).toBeUndefined()
  })
})

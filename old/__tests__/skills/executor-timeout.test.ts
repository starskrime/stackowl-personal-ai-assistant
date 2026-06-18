import { describe, it, expect } from 'vitest'

describe('Q4: withTimeout with AbortController', () => {
  it('rejects after timeoutMs', async () => {
    async function slowFn(signal: AbortSignal): Promise<string> {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => resolve('done'), 500)
        signal.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(new Error('aborted'))
        })
      })
    }

    const controller = new AbortController()
    const timeoutTimer = setTimeout(() => controller.abort(), 50)

    await expect(
      slowFn(controller.signal).finally(() => clearTimeout(timeoutTimer))
    ).rejects.toThrow()
  })

  it('resolves when function completes before timeout', async () => {
    async function fastFn(_signal: AbortSignal): Promise<string> {
      return 'result'
    }

    const controller = new AbortController()
    const timeoutTimer = setTimeout(() => controller.abort(), 500)

    const result = await fastFn(controller.signal).finally(() => clearTimeout(timeoutTimer))
    expect(result).toBe('result')
  })
})

import { describe, it, expect, vi } from "vitest"
import { StreamSession } from "../../src/gateway/stream-session.js"

describe("StreamSession", () => {
  it("accumulates appended deltas and delivers all to onComplete", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 0, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete,
    })
    session.append("hello ")
    session.append("world")
    await session.complete()
    expect(onComplete).toHaveBeenCalledWith("hello world")
  })

  it("complete() calls onComplete exactly once", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 100, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete,
    })
    session.append("abc")
    await session.complete()
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith("abc")
  })

  it("complete() cancels pending flush timer — no race condition", async () => {
    const order: string[] = []
    const onFlush = vi.fn().mockImplementation(async () => { order.push("flush") })
    const onComplete = vi.fn().mockImplementation(async () => { order.push("complete") })
    const session = new StreamSession({ throttleMs: 200, maxLength: Infinity, onFlush, onComplete })
    session.append("hello")
    // complete() fires before the 200ms throttle timer
    await session.complete()
    await new Promise(r => setTimeout(r, 300))  // wait past the timer
    // flush should NOT run after complete()
    expect(order).toEqual(["complete"])
  })

  it("abort() delivers accumulated text via onComplete before stopping", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 1000, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete,
    })
    session.append("partial text")
    await session.abort(new Error("network error"))
    expect(onComplete).toHaveBeenCalledWith("partial text")
  })

  it("append after complete() is a no-op — buffer stays empty", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 0, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete,
    })
    await session.complete()
    session.append("late delta — should be ignored")
    expect(session.text).toBe("")
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it("throttles — does not call onFlush on every append when throttleMs > 0", async () => {
    const onFlush = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 200, maxLength: Infinity,
      onFlush,
      onComplete: vi.fn().mockResolvedValue(undefined),
    })
    session.append("a")
    session.append("b")
    session.append("c")
    await new Promise(r => setTimeout(r, 50))
    // Only one flush may be scheduled (not 3 separate calls)
    expect(onFlush.mock.calls.length).toBeLessThanOrEqual(1)
    await session.complete()
  })

  it("text getter returns accumulated buffer", () => {
    const session = new StreamSession({
      throttleMs: 0, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete: vi.fn().mockResolvedValue(undefined),
    })
    session.append("foo")
    session.append("bar")
    expect(session.text).toBe("foobar")
  })
})

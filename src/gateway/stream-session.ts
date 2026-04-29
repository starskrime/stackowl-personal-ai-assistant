export interface StreamSessionOptions {
  throttleMs: number
  maxLength: number
  onFlush: (text: string) => Promise<void>
  onComplete: (text: string) => Promise<void>
}

export class StreamSession {
  private buffer = ""
  private lastFlush = 0
  private flushTimer: ReturnType<typeof setTimeout> | null = null
  private completed = false

  constructor(private opts: StreamSessionOptions) {}

  append(delta: string): void {
    if (this.completed) return
    this.buffer += delta
    if (this.opts.throttleMs === 0) {
      void this.opts.onFlush(this.buffer)
      return
    }
    const elapsed = Date.now() - this.lastFlush
    if (elapsed >= this.opts.throttleMs) {
      this.scheduleFlush(0)
    } else if (!this.flushTimer) {
      this.scheduleFlush(this.opts.throttleMs - elapsed)
    }
  }

  private scheduleFlush(delayMs: number): void {
    if (this.flushTimer) clearTimeout(this.flushTimer)
    this.flushTimer = setTimeout(async () => {
      if (this.completed) return
      this.flushTimer = null
      this.lastFlush = Date.now()
      try { await this.opts.onFlush(this.buffer) } catch { /* swallow */ }
    }, delayMs)
  }

  async complete(): Promise<void> {
    this.completed = true
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null }
    try {
      await this.opts.onComplete(this.buffer)
    } catch (e) {
      console.error("[StreamSession] onComplete failed:", e)
    }
  }

  async abort(err: Error): Promise<void> {
    this.completed = true
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null }
    console.error("[StreamSession] stream aborted:", err.message)
    if (this.buffer) {
      try { await this.opts.onComplete(this.buffer) } catch { /* best effort */ }
    }
  }

  get text(): string { return this.buffer }
}

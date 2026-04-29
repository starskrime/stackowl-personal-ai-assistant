export interface ChannelCapabilities {
  channelId: string
  displayName: string
  streaming: boolean
  async: boolean
  multiUser: boolean
  maxMessageLength: number
  formatting: ChannelFormat
  supportsButtons: boolean
  supportsFiles: boolean
  supportsVoice: boolean
  supportsImages: boolean
  supportsThreads: boolean
  supportsReactions: boolean
  supportsInterrupt: boolean
  quietHours?: { start: number; end: number }
}

export type ChannelFormat = "html" | "mrkdwn" | "ansi" | "plain" | "markdown"

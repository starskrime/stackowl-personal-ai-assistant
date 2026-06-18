export interface RichContent {
  text: string
  markdown?: string
  voiceText?: string
  actions?: RichAction[]
  files?: RichFile[]
  structured?: unknown
  streamable: boolean
  stream?: AsyncIterable<string>
}

export interface RichAction {
  id: string
  label: string
  style: "primary" | "danger" | "default"
  value: string
}

export interface RichFile {
  name: string
  path: string
  mimeType: string
}

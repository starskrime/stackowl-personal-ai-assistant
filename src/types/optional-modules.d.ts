declare module "naudiodon";
declare module "nodejs-whisper" {
  export function nodewhisper(filePath: string, options?: Record<string, unknown>): Promise<string>;
}

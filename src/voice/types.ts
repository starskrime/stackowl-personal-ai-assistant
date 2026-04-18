export type VoiceStyle =
  | "warm"
  | "professional"
  | "energetic"
  | "calm"
  | "playful"
  | "serious";
export type SpeechSpeed = "slow" | "normal" | "fast";
export type VoicePitch = "low" | "medium" | "high";

export interface VoiceProfile {
  style: VoiceStyle;
  speed: SpeechSpeed;
  pitch: VoicePitch;
  emphasis: number;
  pauseLength: number;
  emotionRange: number;
}

export interface VoiceConfig {
  enabled: boolean;
  provider: "system" | "openai" | "elevenlabs";
  systemVoice?: string;
  openaiVoice?: string;
  elevenlabsVoiceId?: string;
  elevenlabsApiKey?: string;
  outputDir?: string;
}

/** Config for the offline voice channel (stackowl voice command). */
export interface VoiceChannelConfig {
  /**
   * Whisper model for offline STT.
   * Smaller = faster; larger = more accurate.
   * First run auto-downloads the model.
   */
  model?: "tiny.en" | "base.en" | "small.en" | "medium" | "large";
  /** macOS voice name passed to `say -v`. Default: "Samantha". */
  systemVoice?: string;
  /** Words-per-minute for TTS. Default: 200. */
  speakRate?: number;
  /**
   * RMS energy threshold for VAD silence detection (0–32767).
   * Raise this in noisy environments. Default: 500.
   */
  silenceThreshold?: number;
  /** Milliseconds of silence that trigger end-of-speech. Default: 1500. */
  silenceDurationMs?: number;
}

export interface OwlDna {
  challengeLevel: "low" | "medium" | "high" | "relentless";
  verbosity: "verbose" | "balanced" | "concise";
  humor: number;
  formality: number;
}

export interface DnaToVoiceMapping {
  challengeLevel: Record<string, Partial<VoiceProfile>>;
  verbosity: Record<string, Partial<VoiceProfile>>;
  humorRange: { low: Partial<VoiceProfile>; high: Partial<VoiceProfile> };
  formalityRange: { low: Partial<VoiceProfile>; high: Partial<VoiceProfile> };
}

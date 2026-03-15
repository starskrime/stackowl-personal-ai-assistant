export type VoiceStyle = 'warm' | 'professional' | 'energetic' | 'calm' | 'playful' | 'serious';
export type SpeechSpeed = 'slow' | 'normal' | 'fast';
export type VoicePitch = 'low' | 'medium' | 'high';

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
  provider: 'system' | 'openai' | 'elevenlabs';
  systemVoice?: string;
  openaiVoice?: string;
  elevenlabsVoiceId?: string;
  elevenlabsApiKey?: string;
  outputDir?: string;
}

export interface OwlDna {
  challengeLevel: 'low' | 'medium' | 'high' | 'relentless';
  verbosity: 'verbose' | 'balanced' | 'concise';
  humor: number;
  formality: number;
}

export interface DnaToVoiceMapping {
  challengeLevel: Record<string, Partial<VoiceProfile>>;
  verbosity: Record<string, Partial<VoiceProfile>>;
  humorRange: { low: Partial<VoiceProfile>; high: Partial<VoiceProfile> };
  formalityRange: { low: Partial<VoiceProfile>; high: Partial<VoiceProfile> };
}

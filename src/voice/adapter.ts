import { execSync } from 'node:child_process';
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { Logger } from '../logger.js';
import { VoicePersona } from './persona.js';
import type { VoiceConfig, VoiceProfile } from './types.js';

const log = new Logger('VOICE');

const DEFAULT_CONFIG: VoiceConfig = {
  enabled: false,
  provider: 'system',
  outputDir: './workspace/voice',
};

export class VoiceAdapter {
  private config: VoiceConfig;
  private persona: VoicePersona;

  constructor(config?: Partial<VoiceConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.persona = new VoicePersona(this.config);
  }

  async speak(text: string, profile: VoiceProfile): Promise<string | null> {
    if (!this.config.enabled) {
      log.debug('Voice output disabled');
      return null;
    }

    switch (this.config.provider) {
      case 'system':
        return this.speakSystem(text, profile);
      case 'openai':
        return this.speakOpenAI(text, profile);
      case 'elevenlabs':
        return this.speakElevenLabs(text, profile);
      default:
        log.warn(`Unknown voice provider: ${this.config.provider}`);
        return null;
    }
  }

  isAvailable(): boolean {
    if (!this.config.enabled) return false;

    switch (this.config.provider) {
      case 'system':
        return process.platform === 'darwin';
      case 'openai':
        return !!this.config.openaiVoice;
      case 'elevenlabs':
        return !!this.config.elevenlabsApiKey && !!this.config.elevenlabsVoiceId;
      default:
        return false;
    }
  }

  private async speakSystem(text: string, profile: VoiceProfile): Promise<null> {
    if (process.platform !== 'darwin') {
      log.warn('System voice is only available on macOS');
      return null;
    }

    try {
      const args = this.persona.toSayArgs(profile);
      const escaped = text.replace(/"/g, '\\"');
      execSync(`say ${args.map(a => `"${a}"`).join(' ')} "${escaped}"`, {
        timeout: 30_000,
        stdio: 'ignore',
      });
    } catch (err) {
      log.error(`System TTS failed: ${err}`);
    }

    return null;
  }

  private async speakOpenAI(text: string, profile: VoiceProfile): Promise<string> {
    const outputDir = this.config.outputDir ?? './workspace/voice';
    if (!existsSync(outputDir)) mkdirSync(outputDir, { recursive: true });

    const filePath = join(outputDir, `${randomUUID()}.mp3`);
    const voice = this.config.openaiVoice ?? this.mapProfileToOpenAIVoice(profile);
    const speed = profile.speed === 'slow' ? 0.8 : profile.speed === 'fast' ? 1.2 : 1.0;

    try {
      const response = await fetch('https://api.openai.com/v1/audio/speech', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${process.env.OPENAI_API_KEY}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: 'tts-1',
          input: text,
          voice,
          speed,
          response_format: 'mp3',
        }),
      });

      if (!response.ok) {
        throw new Error(`OpenAI TTS returned ${response.status}: ${await response.text()}`);
      }

      const buffer = Buffer.from(await response.arrayBuffer());
      writeFileSync(filePath, buffer);
      log.info(`Generated audio: ${filePath}`);
      return filePath;
    } catch (err) {
      log.error(`OpenAI TTS failed: ${err}`);
      throw err;
    }
  }

  private async speakElevenLabs(text: string, profile: VoiceProfile): Promise<string> {
    const outputDir = this.config.outputDir ?? './workspace/voice';
    if (!existsSync(outputDir)) mkdirSync(outputDir, { recursive: true });

    const filePath = join(outputDir, `${randomUUID()}.mp3`);
    const voiceId = this.config.elevenlabsVoiceId;

    if (!voiceId || !this.config.elevenlabsApiKey) {
      throw new Error('ElevenLabs voice ID and API key required');
    }

    const stabilityMap = { warm: 0.7, calm: 0.8, professional: 0.6, serious: 0.5, energetic: 0.3, playful: 0.4 };
    const stability = stabilityMap[profile.style] ?? 0.5;

    try {
      const response = await fetch(
        `https://api.elevenlabs.io/v1/text-to-speech/${voiceId}`,
        {
          method: 'POST',
          headers: {
            'xi-api-key': this.config.elevenlabsApiKey,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            text,
            model_id: 'eleven_monolingual_v1',
            voice_settings: {
              stability,
              similarity_boost: 0.75,
            },
          }),
        },
      );

      if (!response.ok) {
        throw new Error(`ElevenLabs TTS returned ${response.status}`);
      }

      const buffer = Buffer.from(await response.arrayBuffer());
      writeFileSync(filePath, buffer);
      log.info(`Generated audio: ${filePath}`);
      return filePath;
    } catch (err) {
      log.error(`ElevenLabs TTS failed: ${err}`);
      throw err;
    }
  }

  private mapProfileToOpenAIVoice(profile: VoiceProfile): string {
    // Map voice style + pitch to OpenAI voice names
    if (profile.style === 'warm' || profile.style === 'calm') return 'nova';
    if (profile.style === 'energetic' || profile.style === 'playful') return 'shimmer';
    if (profile.pitch === 'low') return 'onyx';
    if (profile.pitch === 'high') return 'alloy';
    return 'nova';
  }
}

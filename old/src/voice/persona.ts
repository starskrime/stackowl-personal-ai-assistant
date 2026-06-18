import type {
  VoiceProfile,
  VoiceConfig,
  OwlDna,
  DnaToVoiceMapping,
  VoiceStyle,
  SpeechSpeed,
  VoicePitch,
} from "./types.js";

const DEFAULT_PROFILE: VoiceProfile = {
  style: "professional",
  speed: "normal",
  pitch: "medium",
  emphasis: 0.5,
  pauseLength: 0.4,
  emotionRange: 0.5,
};

export class VoicePersona {
  constructor(private config?: Partial<VoiceConfig>) {}

  computeProfile(dna: OwlDna): VoiceProfile {
    const mapping = VoicePersona.getDefaultMapping();
    const layers: Partial<VoiceProfile>[] = [];

    // Challenge level
    const challengeOverride = mapping.challengeLevel[dna.challengeLevel];
    if (challengeOverride) layers.push(challengeOverride);

    // Verbosity
    const verbosityOverride = mapping.verbosity[dna.verbosity];
    if (verbosityOverride) layers.push(verbosityOverride);

    // Humor (0-1 range)
    if (dna.humor <= 0.3) {
      layers.push(mapping.humorRange.low);
    } else if (dna.humor >= 0.7) {
      layers.push(mapping.humorRange.high);
    }

    // Formality (0-1 range)
    if (dna.formality <= 0.3) {
      layers.push(mapping.formalityRange.low);
    } else if (dna.formality >= 0.7) {
      layers.push(mapping.formalityRange.high);
    }

    // Merge all layers onto default
    const result = { ...DEFAULT_PROFILE };
    for (const layer of layers) {
      if (layer.style) result.style = layer.style;
      if (layer.speed) result.speed = layer.speed;
      if (layer.pitch) result.pitch = layer.pitch;
      if (layer.emphasis !== undefined) result.emphasis = layer.emphasis;
      if (layer.pauseLength !== undefined)
        result.pauseLength = layer.pauseLength;
      if (layer.emotionRange !== undefined)
        result.emotionRange = layer.emotionRange;
    }

    return result;
  }

  toSSML(text: string, profile: VoiceProfile): string {
    const rateMap: Record<SpeechSpeed, string> = {
      slow: "80%",
      normal: "100%",
      fast: "120%",
    };
    const pitchMap: Record<VoicePitch, string> = {
      low: "-10%",
      medium: "0%",
      high: "+10%",
    };

    const rate = rateMap[profile.speed];
    const pitch = pitchMap[profile.pitch];
    const pauseMs = Math.round(profile.pauseLength * 800);

    const sentences = text.split(/(?<=[.!?])\s+/);
    const processedSentences = sentences.map((sentence) => {
      // Add emphasis to exclamations and questions
      if (
        profile.emphasis > 0.6 &&
        (sentence.endsWith("!") || sentence.endsWith("?"))
      ) {
        const level = profile.emphasis > 0.8 ? "strong" : "moderate";
        return `<emphasis level="${level}">${sentence}</emphasis>`;
      }
      return sentence;
    });

    const body = processedSentences.join(`<break time="${pauseMs}ms"/> `);

    return [
      "<speak>",
      `  <prosody rate="${rate}" pitch="${pitch}">`,
      `    ${body}`,
      "  </prosody>",
      "</speak>",
    ].join("\n");
  }

  toSayArgs(profile: VoiceProfile): string[] {
    const rateMap: Record<SpeechSpeed, string> = {
      slow: "150",
      normal: "200",
      fast: "250",
    };
    const voiceMap: Record<VoicePitch, string> = {
      low: "Alex",
      medium: "Samantha",
      high: "Karen",
    };

    const voice = this.config?.systemVoice ?? voiceMap[profile.pitch];
    const rate = rateMap[profile.speed];

    return ["-v", voice, "-r", rate];
  }

  static getDefaultMapping(): DnaToVoiceMapping {
    return {
      challengeLevel: {
        low: { style: "warm" as VoiceStyle, emphasis: 0.3 },
        medium: { style: "professional" as VoiceStyle, emphasis: 0.5 },
        high: { style: "serious" as VoiceStyle, emphasis: 0.7 },
        relentless: { style: "energetic" as VoiceStyle, emphasis: 0.9 },
      },
      verbosity: {
        verbose: { speed: "normal" as SpeechSpeed, pauseLength: 0.6 },
        balanced: { speed: "normal" as SpeechSpeed, pauseLength: 0.4 },
        concise: { speed: "fast" as SpeechSpeed, pauseLength: 0.2 },
      },
      humorRange: {
        low: { emotionRange: 0.3 },
        high: { emotionRange: 0.8, style: "playful" as VoiceStyle },
      },
      formalityRange: {
        low: { pitch: "high" as VoicePitch, style: "playful" as VoiceStyle },
        high: {
          pitch: "low" as VoicePitch,
          style: "professional" as VoiceStyle,
        },
      },
    };
  }
}

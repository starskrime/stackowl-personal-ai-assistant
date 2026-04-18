/**
 * StackOwl — OGG Opus → WAV Converter (pure Node.js, no system deps)
 *
 * Uses ogg-opus-decoder (WASM-based) to decode Telegram voice messages.
 * Telegram sends voice as OGG Opus (48kHz). We write a 16kHz mono WAV
 * because whisper.cpp expects it. Downsampling is a simple linear
 * interpolation — good enough for speech transcription.
 */

import { writeFileSync, unlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

// ─── WAV builder ──────────────────────────────────────────────────

function buildWav(pcm16: Int16Array, sampleRate: number): Buffer {
  const dataLen = pcm16.length * 2;
  const hdr = Buffer.alloc(44);
  hdr.write("RIFF", 0);
  hdr.writeUInt32LE(36 + dataLen, 4);
  hdr.write("WAVE", 8);
  hdr.write("fmt ", 12);
  hdr.writeUInt32LE(16, 16);
  hdr.writeUInt16LE(1, 20);       // PCM
  hdr.writeUInt16LE(1, 22);       // mono
  hdr.writeUInt32LE(sampleRate, 24);
  hdr.writeUInt32LE(sampleRate * 2, 28);
  hdr.writeUInt16LE(2, 32);       // block align
  hdr.writeUInt16LE(16, 34);      // bits per sample
  hdr.write("data", 36);
  hdr.writeUInt32LE(dataLen, 40);
  const pcmBuf = Buffer.from(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength);
  return Buffer.concat([hdr, pcmBuf]);
}

/** Linear downsample Float32 from srcRate → dstRate (mono). */
function downsample(samples: Float32Array, srcRate: number, dstRate: number): Float32Array {
  if (srcRate === dstRate) return samples;
  const ratio = srcRate / dstRate;
  const outLen = Math.floor(samples.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const srcIdx = i * ratio;
    const lo = Math.floor(srcIdx);
    const hi = Math.min(lo + 1, samples.length - 1);
    const t = srcIdx - lo;
    out[i] = samples[lo] * (1 - t) + samples[hi] * t;
  }
  return out;
}

/** Clamp Float32 → Int16 PCM. */
function float32ToInt16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

/** Mix stereo Float32 channels down to mono by averaging. */
function mixToMono(channels: Float32Array[]): Float32Array {
  if (channels.length === 1) return channels[0];
  const len = channels[0].length;
  const mono = new Float32Array(len);
  for (let i = 0; i < len; i++) {
    let sum = 0;
    for (const ch of channels) sum += ch[i];
    mono[i] = sum / channels.length;
  }
  return mono;
}

// ─── Converter ────────────────────────────────────────────────────

const TARGET_RATE = 16000; // whisper.cpp expects 16kHz

export class OggConverter {
  /**
   * Convert an OGG Opus buffer (from Telegram) to a 16kHz mono WAV file.
   * Returns the path to the WAV file in the OS temp directory.
   * Caller is responsible for deleting the file after use.
   */
  async convert(oggBuffer: Buffer): Promise<string> {
    // Lazy import — WASM loads once and is cached
    const { OggOpusDecoder } = await import("ogg-opus-decoder").catch(() => {
      throw new Error(
        "ogg-opus-decoder not available. Run: npm install ogg-opus-decoder",
      );
    });

    const decoder = new OggOpusDecoder();
    await decoder.ready;

    let result: { channelData: Float32Array[]; samplesDecoded: number; sampleRate: number };
    try {
      result = await decoder.decodeFile(new Uint8Array(oggBuffer));
    } finally {
      decoder.free();
    }

    if (!result.samplesDecoded || result.channelData.length === 0) {
      throw new Error("OGG decoding produced no audio samples");
    }

    // Mix to mono, downsample to 16kHz, quantize to Int16
    const mono    = mixToMono(result.channelData);
    const src     = result.sampleRate || 48000;
    const resampled = downsample(mono, src, TARGET_RATE);
    const pcm16   = float32ToInt16(resampled);
    const wav     = buildWav(pcm16, TARGET_RATE);

    const wavPath = join(tmpdir(), `stackowl-tg-${randomUUID()}.wav`);
    writeFileSync(wavPath, wav);
    return wavPath;
  }

  static cleanup(path: string): void {
    try {
      if (existsSync(path)) unlinkSync(path);
    } catch { /* best-effort */ }
  }
}

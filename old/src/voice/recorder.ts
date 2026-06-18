/**
 * StackOwl — Microphone Recorder with Energy-based VAD
 *
 * Uses naudiodon (PortAudio) to capture PCM from the default mic.
 * Energy-based Voice Activity Detection stops recording automatically
 * after a configurable silence period.
 *
 * Output: a 16kHz mono 16-bit WAV file path (in OS temp dir).
 */

import { writeFileSync, unlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { Logger } from "../logger.js";

const log = new Logger("RECORDER");

// ─── Options ─────────────────────────────────────────────────────

export interface RecorderOptions {
  /** Audio sample rate in Hz. Whisper.cpp expects 16000. */
  sampleRate?: number;
  /** Number of channels — 1 = mono (required by Whisper). */
  channels?: number;
  /**
   * RMS energy level (0–32767) below which a chunk is considered silence.
   * Raise this in noisy environments. Default: 500.
   */
  silenceThreshold?: number;
  /**
   * How many consecutive milliseconds of silence trigger end-of-speech.
   * Default: 1500ms (1.5 seconds).
   */
  silenceDurationMs?: number;
  /**
   * Safety cap: max recording length in ms regardless of VAD.
   * Default: 30000ms (30 seconds).
   */
  maxDurationMs?: number;
}

const DEFAULTS: Required<RecorderOptions> = {
  sampleRate: 16000,
  channels: 1,
  silenceThreshold: 500,
  silenceDurationMs: 1500,
  maxDurationMs: 30000,
};

// ─── Helpers ─────────────────────────────────────────────────────

/** Compute root-mean-square of a 16-bit LE PCM buffer. */
function rms(buf: Buffer): number {
  if (buf.length < 2) return 0;
  let sum = 0;
  const samples = Math.floor(buf.length / 2);
  for (let i = 0; i < samples; i++) {
    const s = buf.readInt16LE(i * 2);
    sum += s * s;
  }
  return Math.sqrt(sum / samples);
}

/** Build a minimal WAV file from raw 16-bit LE PCM data. */
function buildWav(pcm: Buffer, sampleRate: number, channels: number): Buffer {
  const dataLen = pcm.length;
  const hdr = Buffer.alloc(44);

  hdr.write("RIFF", 0);
  hdr.writeUInt32LE(36 + dataLen, 4);
  hdr.write("WAVE", 8);
  hdr.write("fmt ", 12);
  hdr.writeUInt32LE(16, 16);                           // fmt chunk size
  hdr.writeUInt16LE(1, 20);                            // PCM = 1
  hdr.writeUInt16LE(channels, 22);
  hdr.writeUInt32LE(sampleRate, 24);
  hdr.writeUInt32LE(sampleRate * channels * 2, 28);    // byte rate
  hdr.writeUInt16LE(channels * 2, 32);                 // block align
  hdr.writeUInt16LE(16, 34);                           // bits per sample
  hdr.write("data", 36);
  hdr.writeUInt32LE(dataLen, 40);

  return Buffer.concat([hdr, pcm]);
}

// ─── Recorder ────────────────────────────────────────────────────

export class MicrophoneRecorder {
  private opts: Required<RecorderOptions>;

  constructor(opts: RecorderOptions = {}) {
    this.opts = { ...DEFAULTS, ...opts };
  }

  /**
   * Open the default microphone, record until silence is detected,
   * and return the path to a WAV file in the OS temp directory.
   *
   * Throws if no speech is detected before the max duration expires.
   */
  async record(): Promise<string> {
    // Lazy import — avoids hard crash if naudiodon isn't installed/compiled
    let naudiodon: typeof import("naudiodon");
    try {
      naudiodon = await import("naudiodon");
    } catch {
      throw new Error(
        "naudiodon is not available. Run: npm install naudiodon",
      );
    }

    const { sampleRate, channels, silenceThreshold, silenceDurationMs, maxDurationMs } =
      this.opts;

    const chunks: Buffer[] = [];
    let hasSpeech = false;
    let silenceMs = 0;
    let lastChunkTime = Date.now();

    return new Promise<string>((resolve, reject) => {
      // AudioIO is a factory function, not a class
      const ai = naudiodon.AudioIO({
        inOptions: {
          channelCount: channels,
          sampleFormat: naudiodon.SampleFormat16Bit,
          sampleRate,
          deviceId: -1,         // system default input device
          closeOnError: true,
        },
      });

      const stop = () => {
        try {
          ai.quit();
        } catch {
          // already closed
        }
      };

      const maxTimer = setTimeout(() => {
        log.warn(`Max recording duration (${maxDurationMs}ms) reached`);
        stop();
      }, maxDurationMs);

      ai.on("data", (chunk: Buffer) => {
        const now = Date.now();
        const chunkMs = now - lastChunkTime;
        lastChunkTime = now;

        const level = rms(chunk);

        if (level > silenceThreshold) {
          hasSpeech = true;
          silenceMs = 0;
          chunks.push(chunk);
        } else {
          if (hasSpeech) {
            silenceMs += chunkMs;
            chunks.push(chunk); // keep trailing silence for natural speech end
            if (silenceMs >= silenceDurationMs) {
              stop();
            }
          }
          // Pre-speech silence: discard (avoids large leading silence in file)
        }
      });

      ai.on("close", () => {
        clearTimeout(maxTimer);

        if (!hasSpeech || chunks.length === 0) {
          reject(new Error("No speech detected"));
          return;
        }

        const pcm = Buffer.concat(chunks);
        const wav = buildWav(pcm, sampleRate, channels);
        const wavPath = join(tmpdir(), `stackowl-${randomUUID()}.wav`);

        try {
          writeFileSync(wavPath, wav);
          log.debug(`WAV written: ${wavPath} (${(wav.length / 1024).toFixed(1)} KB)`);
          resolve(wavPath);
        } catch (err) {
          reject(err);
        }
      });

      ai.on("error", (err: Error) => {
        clearTimeout(maxTimer);
        reject(new Error(`Microphone error: ${err.message}`));
      });

      ai.start();
    });
  }

  /** Delete a WAV file produced by record(). Safe to call even if file is gone. */
  static cleanup(wavPath: string): void {
    try {
      if (existsSync(wavPath)) unlinkSync(wavPath);
    } catch {
      // best-effort
    }
  }
}

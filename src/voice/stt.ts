/**
 * StackOwl — Offline Speech-to-Text via Whisper.cpp
 *
 * Wraps nodejs-whisper which bundles whisper.cpp as a native addon.
 * Call ensureReady() once at startup to build whisper.cpp and download
 * the model with visible progress — takes 2-5 min on first run, then cached.
 *
 * Supported models (offline, no API key required):
 *   tiny.en  (~39 MB)  — fastest, decent accuracy for English
 *   base.en  (~75 MB)  — good balance of speed and accuracy
 *   small.en (~244 MB) — higher accuracy, slower  ← recommended
 *   medium   (~769 MB) — near cloud-quality, CPU-intensive
 */

import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";
import { Logger } from "../logger.js";

const log = new Logger("STT");

// ─── Config ──────────────────────────────────────────────────────

export type WhisperModel =
  | "tiny.en"
  | "base.en"
  | "small.en"
  | "medium"
  | "large";

export interface STTOptions {
  /** Whisper model to use. Default: "base.en". */
  model?: WhisperModel;
  /** BCP-47 language code. Default: "en". */
  language?: string;
  /**
   * Delete the WAV file after transcription.
   * Defaults to true — caller should not reuse the file.
   */
  removeWavAfter?: boolean;
}

const DEFAULTS: Required<STTOptions> = {
  model: "base.en",
  language: "en",
  removeWavAfter: true,
};

// Model filename map (matches nodejs-whisper constants)
const MODEL_FILE: Record<string, string> = {
  "tiny.en":  "ggml-tiny.en.bin",
  "base.en":  "ggml-base.en.bin",
  "small.en": "ggml-small.en.bin",
  "medium":   "ggml-medium.bin",
  "large":    "ggml-large.bin",
};

// ─── Paths ────────────────────────────────────────────────────────

function whisperCppRoot(): string {
  // nodejs-whisper ships whisper.cpp source at:
  // node_modules/nodejs-whisper/cpp/whisper.cpp
  const req = createRequire(import.meta.url);
  const pkgPath = req.resolve("nodejs-whisper/package.json");
  return join(pkgPath, "..", "cpp", "whisper.cpp");
}

function cliBinaryPath(cppRoot: string): string {
  return join(cppRoot, "build", "bin", "whisper-cli");
}

function modelPath(cppRoot: string, model: string): string {
  return join(cppRoot, "models", MODEL_FILE[model] ?? `ggml-${model}.bin`);
}

// ─── STT Engine ──────────────────────────────────────────────────

export class WhisperSTT {
  private opts: Required<STTOptions>;

  constructor(opts: STTOptions = {}) {
    this.opts = { ...DEFAULTS, ...opts };
  }

  /**
   * Pre-warm: build whisper.cpp and download the model if not already done.
   * Call this once at startup so the first transcription is instant.
   *
   * Prints progress to stdout — cmake build takes 2-5 min on first run.
   */
  async ensureReady(): Promise<void> {
    const cppRoot = whisperCppRoot();
    const binary  = cliBinaryPath(cppRoot);
    const model   = modelPath(cppRoot, this.opts.model);

    // ── Step 1: Build whisper.cpp if binary missing ───────────────
    if (!existsSync(binary)) {
      console.log(`\n📦 Building whisper.cpp (first run — takes 2-5 min)...`);
      console.log(`   Source: ${cppRoot}\n`);

      try {
        // CMake configure
        execSync(`cmake -B build -DCMAKE_BUILD_TYPE=Release`, {
          cwd: cppRoot,
          stdio: "inherit",
        });
        // CMake build — target whisper-cli, parallel jobs
        const jobs = Math.max(2, 4); // safe default
        execSync(`cmake --build build --target whisper-cli -j${jobs}`, {
          cwd: cppRoot,
          stdio: "inherit",
        });
      } catch (err) {
        throw new Error(
          `whisper.cpp build failed: ${(err as Error).message}\n` +
          `Make sure cmake and Xcode Command Line Tools are installed:\n` +
          `  xcode-select --install`,
        );
      }

      if (!existsSync(binary)) {
        throw new Error(`whisper-cli binary not found after build at ${binary}`);
      }
      console.log(`\n✓ whisper.cpp built successfully\n`);
    } else {
      log.debug(`whisper-cli already built at ${binary}`);
    }

    // ── Step 2: Download model if missing ─────────────────────────
    if (!existsSync(model)) {
      console.log(`📥 Downloading Whisper model "${this.opts.model}"...`);
      console.log(`   This is a one-time download. It will be cached locally.\n`);

      const modelsDir = join(cppRoot, "models");
      const dlScript  = join(modelsDir, "download-ggml-model.sh");

      try {
        execSync(`chmod +x "${dlScript}" && bash "${dlScript}" ${this.opts.model}`, {
          cwd: modelsDir,
          stdio: "inherit",
        });
      } catch (err) {
        throw new Error(
          `Model download failed: ${(err as Error).message}\n` +
          `Check your internet connection and try again.`,
        );
      }

      if (!existsSync(model)) {
        throw new Error(`Model file not found after download: ${model}`);
      }
      console.log(`\n✓ Model "${this.opts.model}" ready\n`);
    } else {
      log.debug(`Model already cached: ${model}`);
    }
  }

  /**
   * Transcribe a WAV file to text.
   *
   * The WAV must be 16kHz, 16-bit, mono — exactly what MicrophoneRecorder produces.
   * Call ensureReady() first to avoid a long pause on first transcription.
   *
   * Returns the transcript string (may be empty if nothing was heard).
   */
  async transcribe(wavPath: string): Promise<string> {
    let nodewhisper: typeof import("nodejs-whisper").nodewhisper;

    try {
      const mod = await import("nodejs-whisper");
      nodewhisper = mod.nodewhisper ?? (mod as unknown as { default: typeof mod }).default?.nodewhisper;
      if (!nodewhisper) {
        throw new Error("nodewhisper export not found in nodejs-whisper");
      }
    } catch (err) {
      throw new Error(
        `nodejs-whisper is not available: ${(err as Error).message}. Run: npm install nodejs-whisper`,
      );
    }

    log.debug(`Transcribing ${wavPath} with model "${this.opts.model}"`);

    try {
      const raw = await nodewhisper(wavPath, {
        modelName: this.opts.model,
        autoDownloadModelName: this.opts.model,
        removeWavFileAfterTranscription: this.opts.removeWavAfter,
        withCuda: false,
        whisperOptions: {
          outputInText: true,
          language: this.opts.language,
          wordTimestamps: false,
        },
      });

      const transcript = this.clean(raw);
      log.debug(`Transcript: "${transcript}"`);
      return transcript;
    } catch (err) {
      throw new Error(`Transcription failed: ${(err as Error).message}`);
    }
  }

  /**
   * Strip Whisper's timestamp markers and normalize whitespace.
   * Raw output often contains lines like: "[00:00:00.000 --> 00:00:02.000]  Hello world"
   */
  private clean(raw: string): string {
    return raw
      .replace(/\[\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}\]/g, "")
      .replace(/\[BLANK_AUDIO\]/gi, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  get modelName(): WhisperModel {
    return this.opts.model;
  }
}

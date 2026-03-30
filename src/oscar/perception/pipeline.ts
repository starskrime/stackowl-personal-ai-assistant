import type { ScreenBuffer, BoundingBox } from "../types.js";
import { exec } from "child_process";
import { promisify } from "util";
import * as fs from "fs";

const execAsync = promisify(exec);

export class TripleBufferPipeline {
  private buffers: [ScreenBuffer, ScreenBuffer, ScreenBuffer] | null = null;
  private writeIdx = 0;
  private readIdx = 2;
  private captureInProgress = false;
  private encodeInProgress = false;
  private onBufferReady?: (buffer: ScreenBuffer) => void;
  private intervalMs: number;
  private running = false;
  private intervalId?: ReturnType<typeof setInterval>;

  constructor(intervalMs = 16) {
    this.intervalMs = intervalMs;
  }

  start(onBufferReady?: (buffer: ScreenBuffer) => void): void {
    this.onBufferReady = onBufferReady;
    this.running = true;
    this.initializeBuffers();
    this.captureNext();
    this.intervalId = setInterval(() => this.tick(), this.intervalMs);
  }

  stop(): void {
    this.running = false;
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = undefined;
    }
  }

  getLatest(): ScreenBuffer | null {
    if (!this.buffers) return null;
    return this.buffers[this.readIdx];
  }

  getLatestAsync(): Promise<ScreenBuffer | null> {
    return new Promise((resolve) => {
      const check = () => {
        const buf = this.getLatest();
        if (buf) {
          resolve(buf);
        } else {
          setTimeout(check, 1);
        }
      };
      check();
    });
  }

  private initializeBuffers(): void {
    this.buffers = [
      this.createEmptyBuffer(0),
      this.createEmptyBuffer(1),
      this.createEmptyBuffer(2),
    ];
  }

  private createEmptyBuffer(id: number): ScreenBuffer {
    return {
      id,
      imageData: Buffer.alloc(0),
      width: 0,
      height: 0,
      timestamp: 0,
    };
  }

  private async tick(): Promise<void> {
    if (!this.running) return;

    if (!this.captureInProgress && !this.encodeInProgress) {
      await this.captureNext();
    }
  }

  private async captureNext(): Promise<void> {
    if (!this.running || !this.buffers) return;

    this.captureInProgress = true;
    const captureIdx = this.writeIdx;

    try {
      const screenshot = await this.captureScreen();
      this.buffers[captureIdx] = {
        ...screenshot,
        id: captureIdx,
        timestamp: Date.now(),
      };

      this.encodeInProgress = true;
      const encodeIdx = captureIdx;

      setImmediate(() => this.encodeBuffer(encodeIdx));
    } catch (error) {
      console.error("[TripleBuffer] Capture failed:", error);
    } finally {
      this.captureInProgress = false;
    }
  }

  private async encodeBuffer(idx: number): Promise<void> {
    if (!this.buffers || !this.running) {
      this.encodeInProgress = false;
      return;
    }

    try {
      const buf = this.buffers[idx];
      const jpegData = await this.encodeToJPEG(buf.imageData);

      this.buffers[idx] = {
        ...buf,
        imageData: jpegData,
      };

      this.readIdx = idx;

      if (this.onBufferReady) {
        this.onBufferReady(this.buffers[this.readIdx]);
      }

    } catch (error) {
      console.error("[TripleBuffer] Encode failed:", error);
    } finally {
      this.encodeInProgress = false;
    }
  }

  private async captureScreen(): Promise<Omit<ScreenBuffer, "id" | "timestamp">> {
    const tmpFile = `/tmp/oscar_screenshot_${Date.now()}.png`;

    try {
      await execAsync(`screencapture -x ${tmpFile}`);

      const imageData = fs.readFileSync(tmpFile);
      const size = await this.getImageSize();

      return {
        imageData,
        width: size.width,
        height: size.height,
        bounds: { x: 0, y: 0, width: size.width, height: size.height },
      };
    } finally {
      try {
        fs.unlinkSync(tmpFile);
      } catch {}
    }
  }

  private async getImageSize(): Promise<{ width: number; height: number }> {
    try {
      const { stdout } = await execAsync("screencapture -g -R0,0,1,1 /tmp/oscar_dummy.png && sips -g pixelWidth -g pixelHeight /tmp/oscar_dummy.png && rm /tmp/oscar_dummy.png");
      const widthMatch = stdout.match(/pixelWidth:\s*(\d+)/);
      const heightMatch = stdout.match(/pixelHeight:\s*(\d+)/);
      return {
        width: widthMatch ? parseInt(widthMatch[1]) : 1920,
        height: heightMatch ? parseInt(heightMatch[1]) : 1080,
      };
    } catch {
      return { width: 1920, height: 1080 };
    }
  }

  private async encodeToJPEG(imageData: Buffer): Promise<Buffer> {
    return new Promise((resolve, reject) => {
      const tmpInput = `/tmp/oscar_enc_${Date.now()}_in.png`;
      const tmpOutput = `/tmp/oscar_enc_${Date.now()}_out.jpg`;

      fs.writeFileSync(tmpInput, imageData);

      exec(`sips -s format jpeg ${tmpInput} --out ${tmpOutput}`, (error) => {
        if (error) {
          try { fs.unlinkSync(tmpInput); } catch {}
          reject(error);
          return;
        }

        const jpegData = fs.readFileSync(tmpOutput);
        try {
          fs.unlinkSync(tmpInput);
          fs.unlinkSync(tmpOutput);
        } catch {}

        resolve(jpegData);
      });
    });
  }

  async captureRegion(bounds: BoundingBox): Promise<ScreenBuffer> {
    const tmpFile = `/tmp/oscar_screenshot_${Date.now()}.png`;
    const boundsStr = `-x ${bounds.x} -y ${bounds.y} -w ${bounds.width} -h ${bounds.height}`;

    try {
      await execAsync(`screencapture ${boundsStr} ${tmpFile}`);
      const imageData = fs.readFileSync(tmpFile);

      return {
        id: Date.now(),
        imageData,
        width: bounds.width,
        height: bounds.height,
        timestamp: Date.now(),
        bounds,
      };
    } finally {
      try {
        fs.unlinkSync(tmpFile);
      } catch {}
    }
  }
}

export const globalScreenPipeline = new TripleBufferPipeline(16);

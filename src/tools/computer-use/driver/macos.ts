/**
 * StackOwl — macOS Driver (Persistent JXA Worker)
 *
 * Speed: ~5-15ms per action (vs 600-800ms with per-spawn osascript).
 *
 * Architecture:
 *   - On init(): write JXA worker script to /tmp, launch it once
 *   - Commands: newline-delimited JSON via stdin → stdout
 *   - Worker runs a blocking stdin read loop (NSFileHandle.availableData)
 *   - Screenshot uses native `screencapture` directly (fast, no encoding)
 *   - Auto-restarts worker if it crashes
 */

import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { writeFile, unlink } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import type {
  IOSDriver,
  Point,
  Region,
  ScreenDimensions,
  MouseButton,
  ScrollDirection,
} from "./interface.js";

const execFileAsync = promisify(execFile);

// ─── JXA Worker Script ───────────────────────────────────────────
// This script runs as a persistent osascript process.
// It reads JSON commands from stdin (one per line) and writes
// JSON responses to stdout (one per line).

const JXA_WORKER = `
ObjC.import('CoreGraphics');
ObjC.import('AppKit');
ObjC.import('Foundation');

var SE = Application('System Events');

// Virtual key code map (QWERTY, macOS)
var KC = {
  enter:36,return:36,tab:48,space:49,backspace:51,delete:117,
  escape:53,esc:53,up:126,down:125,left:123,right:124,
  home:115,end:119,pageup:116,pagedown:121,
  f1:122,f2:120,f3:99,f4:118,f5:96,f6:97,f7:98,f8:100,f9:101,f10:109,f11:103,f12:111,
  minus:27,equals:24,leftbracket:33,rightbracket:30,semicolon:41,
  quote:39,comma:43,period:47,slash:44,backslash:42,grave:50
};
var codes=[0,11,8,2,14,3,5,4,34,38,40,37,46,45,31,35,12,15,1,17,32,9,13,7,16,6];
for(var i=0;i<26;i++) KC[String.fromCharCode(97+i)]=codes[i];
var dc=[29,18,19,20,21,23,22,26,28,25];
for(var i=0;i<=9;i++) KC[String(i)]=dc[i];

var MM = {
  cmd:'command down',command:'command down',meta:'command down',
  shift:'shift down',alt:'option down',option:'option down',
  ctrl:'control down',control:'control down'
};

var stdinFH  = $.NSFileHandle.fileHandleWithStandardInput;
var stdoutFH = $.NSFileHandle.fileHandleWithStandardOutput;
var buf = '';

function writeLine(s) {
  var d = ObjC.wrap(s + '\\n').dataUsingEncoding($.NSUTF8StringEncoding);
  stdoutFH.writeData(d);
}

function dispatch(cmd) {
  switch(cmd.a) {

    // ── Mouse Move ──────────────────────────────────────────
    case 'mm': {
      var e = $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, {x:cmd.x, y:cmd.y}, 0);
      $.CGEventPost($.kCGHIDEventTap, e);
      return 'ok';
    }

    // ── Mouse Click ─────────────────────────────────────────
    case 'mc': {
      var pt = {x:cmd.x, y:cmd.y};
      var btn = cmd.b==='right' ? 1 : (cmd.b==='middle' ? 2 : 0);
      var dType = cmd.b==='right' ? $.kCGEventRightMouseDown : $.kCGEventLeftMouseDown;
      var uType = cmd.b==='right' ? $.kCGEventRightMouseUp  : $.kCGEventLeftMouseUp;
      var mv = $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, pt, 0);
      $.CGEventPost($.kCGHIDEventTap, mv);
      var n = cmd.n || 1;
      for(var i=0;i<n;i++) {
        var down = $.CGEventCreateMouseEvent(null, dType, pt, btn);
        $.CGEventSetIntegerValueField(down, $.kCGMouseEventClickState, i+1);
        $.CGEventPost($.kCGHIDEventTap, down);
        var up = $.CGEventCreateMouseEvent(null, uType, pt, btn);
        $.CGEventSetIntegerValueField(up, $.kCGMouseEventClickState, i+1);
        $.CGEventPost($.kCGHIDEventTap, up);
      }
      return 'ok';
    }

    // ── Mouse Drag ──────────────────────────────────────────
    case 'md': {
      var steps = cmd.s || 20;
      var delay = (cmd.ms || 400) / steps;
      var fx=cmd.fx, fy=cmd.fy, tx=cmd.tx, ty=cmd.ty;
      var downE = $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseDown, {x:fx,y:fy}, 0);
      $.CGEventPost($.kCGHIDEventTap, downE);
      for(var i=0;i<=steps;i++) {
        var t=i/steps, ease=t<0.5?2*t*t:1-Math.pow(-2*t+2,2)/2;
        var x=Math.round(fx+(tx-fx)*ease), y=Math.round(fy+(ty-fy)*ease);
        var drag = $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseDragged, {x:x,y:y}, 0);
        $.CGEventPost($.kCGHIDEventTap, drag);
        var d = $.NSDate.dateWithTimeIntervalSinceNow(delay/1000);
        $.NSRunLoop.currentRunLoop.runUntilDate(d);
      }
      var up = $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseUp, {x:tx,y:ty}, 0);
      $.CGEventPost($.kCGHIDEventTap, up);
      return 'ok';
    }

    // ── Scroll ──────────────────────────────────────────────
    case 'sc': {
      var w1=0,w2=0;
      if(cmd.d==='up')    w1= cmd.n;
      else if(cmd.d==='down')  w1=-cmd.n;
      else if(cmd.d==='left')  w2= cmd.n;
      else                      w2=-cmd.n;
      var e = $.CGEventCreateScrollWheelEvent(null, 0, 2, w1, w2);
      $.CGEventPost($.kCGHIDEventTap, e);
      return 'ok';
    }

    // ── Type Text (full string at once) ──────────────────────
    case 'tt': {
      SE.keystroke(cmd.t);
      return 'ok';
    }

    // ── Type Single Char ────────────────────────────────────
    case 'tc': {
      if(cmd.c === '\\x08' || cmd.c === '\\u0008') {
        SE.keyCode(51); // backspace
      } else {
        SE.keystroke(cmd.c);
      }
      return 'ok';
    }

    // ── Press Key ───────────────────────────────────────────
    case 'pk': {
      var k = cmd.k.toLowerCase();
      var kc = KC[k];
      var mods = (cmd.m||[]).map(function(m){ return MM[m.toLowerCase()]; }).filter(Boolean);
      if(mods.length > 0) {
        var using = mods.length===1 ? mods[0] : mods;
        if(kc !== undefined) SE.keyCode(kc, {using:using});
        else SE.keystroke(cmd.k, {using:using});
      } else {
        if(kc !== undefined) SE.keyCode(kc);
        else SE.keystroke(cmd.k.length===1 ? cmd.k : k);
      }
      return 'ok';
    }

    // ── Sleep ────────────────────────────────────────────────
    case 'sl': {
      var d = $.NSDate.dateWithTimeIntervalSinceNow(cmd.ms/1000);
      $.NSRunLoop.currentRunLoop.runUntilDate(d);
      return 'ok';
    }

    // ── Screen Size ──────────────────────────────────────────
    case 'ss': {
      var s = $.NSScreen.mainScreen;
      return {w: Number(s.frame.size.width), h: Number(s.frame.size.height), sf: Number(s.backingScaleFactor)};
    }

    // ── Cursor Position ──────────────────────────────────────
    case 'cp': {
      var e = $.CGEventCreate(null);
      var p = $.CGEventGetLocation(e);
      return {x: Number(p.x), y: Number(p.y)};
    }

    // ── Open App ─────────────────────────────────────────────
    case 'oa': {
      Application(cmd.name).activate();
      return 'ok';
    }

    // ── Open URL ─────────────────────────────────────────────
    case 'ou': {
      var app = Application.currentApplication();
      app.includeStandardAdditions = true;
      app.openLocation(cmd.url);
      return 'ok';
    }

    // ── Front App ────────────────────────────────────────────
    case 'fa': {
      var front = SE.processes.whose({frontmost:true})[0];
      return String(front.name());
    }

    default:
      throw new Error('Unknown action: ' + cmd.a);
  }
}

// ── Main loop ────────────────────────────────────────────────────
while(true) {
  var data = stdinFH.availableData;
  if(!data || data.length === 0) break; // EOF
  var chunk = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding));
  if(chunk === null) continue;
  buf += chunk;

  var lines = buf.split('\\n');
  buf = lines.pop() || '';

  for(var li=0; li<lines.length; li++) {
    var line = lines[li].trim();
    if(!line) continue;
    var cmd;
    try {
      cmd = JSON.parse(line);
    } catch(e) {
      writeLine(JSON.stringify({id:null, ok:false, e:'JSON parse error: '+e}));
      continue;
    }
    try {
      var res = dispatch(cmd);
      writeLine(JSON.stringify({id:cmd.id, ok:true, r:res}));
    } catch(e) {
      writeLine(JSON.stringify({id:cmd.id, ok:false, e:String(e)}));
    }
  }
}
`;

// ─── MacOSDriver ─────────────────────────────────────────────────

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
}

export class MacOSDriver implements IOSDriver {
  readonly platform = "darwin" as const;

  private worker: ReturnType<typeof spawn> | null = null;
  private scriptPath: string = "";
  private pending: Map<string, PendingCall> = new Map();
  private lineBuffer = "";
  private ready = false;
  private idCounter = 0;

  isReady(): boolean {
    return this.ready && this.worker !== null && !this.worker.killed;
  }

  async init(): Promise<void> {
    if (this.ready) return;

    // Write worker script to a temp file
    const id = randomBytes(4).toString("hex");
    this.scriptPath = join(tmpdir(), `stackowl-cu-${id}.js`);
    await writeFile(this.scriptPath, JXA_WORKER, "utf8");

    await this.startWorker();
  }

  private async startWorker(): Promise<void> {
    return new Promise((resolve, reject) => {
      const proc = spawn("osascript", ["-l", "JavaScript", this.scriptPath], {
        stdio: ["pipe", "pipe", "pipe"],
      });

      this.worker = proc;
      this.lineBuffer = "";

      proc.stdout!.setEncoding("utf8");
      proc.stdout!.on("data", (chunk: string) => {
        this.lineBuffer += chunk;
        const lines = this.lineBuffer.split("\n");
        this.lineBuffer = lines.pop() ?? "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const msg = JSON.parse(trimmed) as {
              id: string;
              ok: boolean;
              r?: unknown;
              e?: string;
            };
            const pending = this.pending.get(msg.id);
            if (pending) {
              this.pending.delete(msg.id);
              if (msg.ok) pending.resolve(msg.r ?? null);
              else pending.reject(new Error(msg.e ?? "Unknown error"));
            }
          } catch {
            // Non-JSON output from osascript (e.g. warnings) — ignore
          }
        }
      });

      proc.stderr!.setEncoding("utf8");
      proc.stderr!.on("data", (chunk: string) => {
        const trimmed = chunk.trim();
        if (trimmed && !trimmed.includes("WARNING:")) {
          // Surface real errors but ignore JXA startup noise
        }
      });

      proc.on("close", () => {
        this.ready = false;
        // Reject all pending calls
        for (const [, pending] of this.pending) {
          pending.reject(new Error("Worker process exited unexpectedly"));
        }
        this.pending.clear();
      });

      proc.on("error", (err) => {
        reject(err);
      });

      // Give the JXA runtime ~800ms to start up, then mark ready
      setTimeout(() => {
        this.ready = true;
        resolve();
      }, 800);
    });
  }

  private send(cmd: Record<string, unknown>): Promise<unknown> {
    if (!this.isReady()) {
      return Promise.reject(
        new Error("MacOS driver not ready. Call init() first."),
      );
    }

    const id = String(this.idCounter++);
    const line = JSON.stringify({ ...cmd, id }) + "\n";

    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });

      // Timeout safety net: 10s per action
      const timer = setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`Timeout waiting for action: ${JSON.stringify(cmd)}`));
        }
      }, 10_000);

      this.pending.get(id)!.resolve = (v) => {
        clearTimeout(timer);
        resolve(v);
      };
      this.pending.get(id)!.reject = (e) => {
        clearTimeout(timer);
        reject(e);
      };

      this.worker!.stdin!.write(line, "utf8");
    });
  }

  async dispose(): Promise<void> {
    this.ready = false;
    if (this.worker && !this.worker.killed) {
      this.worker.stdin?.end();
      this.worker.kill();
    }
    this.worker = null;
    if (this.scriptPath && existsSync(this.scriptPath)) {
      await unlink(this.scriptPath).catch(() => {});
    }
  }

  // ─── IOSDriver implementation ─────────────────────────────────

  async getScreenSize(): Promise<ScreenDimensions> {
    const r = (await this.send({ a: "ss" })) as {
      w: number;
      h: number;
      sf: number;
    };
    return { width: r.w, height: r.h, scaleFactor: r.sf };
  }

  async getCursorPosition(): Promise<Point> {
    const r = (await this.send({ a: "cp" })) as { x: number; y: number };
    return { x: r.x, y: r.y };
  }

  async screenshot(outputPath: string, region?: Region): Promise<void> {
    // Use native screencapture — fast file write, no encoding roundtrip
    const regionFlag = region
      ? ["-R", `${region.x},${region.y},${region.width},${region.height}`]
      : [];
    await execFileAsync("screencapture", ["-x", ...regionFlag, outputPath], {
      timeout: 10_000,
    });
  }

  async mouseMove(x: number, y: number): Promise<void> {
    await this.send({ a: "mm", x, y });
  }

  async mouseClick(
    x: number,
    y: number,
    button: MouseButton,
    count: number,
  ): Promise<void> {
    await this.send({ a: "mc", x, y, b: button, n: count });
  }

  async mouseDrag(
    fromX: number,
    fromY: number,
    toX: number,
    toY: number,
  ): Promise<void> {
    await this.send({ a: "md", fx: fromX, fy: fromY, tx: toX, ty: toY });
  }

  async scroll(direction: ScrollDirection, amount: number): Promise<void> {
    await this.send({ a: "sc", d: direction, n: amount });
  }

  async typeText(text: string): Promise<void> {
    // Split into safe chunks (System Events can drop long strings)
    const CHUNK = 200;
    for (let i = 0; i < text.length; i += CHUNK) {
      await this.send({ a: "tt", t: text.slice(i, i + CHUNK) });
    }
  }

  async typeChar(char: string): Promise<void> {
    await this.send({ a: "tc", c: char });
  }

  async pressKey(key: string, modifiers: string[] = []): Promise<void> {
    await this.send({ a: "pk", k: key, m: modifiers });
  }

  async openApp(name: string): Promise<void> {
    await this.send({ a: "oa", name });
  }

  async openUrl(url: string): Promise<void> {
    await this.send({ a: "ou", url });
  }

  async getFrontApp(): Promise<string> {
    return (await this.send({ a: "fa" })) as string;
  }

  async sleep(ms: number): Promise<void> {
    await this.send({ a: "sl", ms });
  }
}

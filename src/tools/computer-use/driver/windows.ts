/**
 * StackOwl — Windows Driver
 *
 * Desktop automation via PowerShell + inline C# (Win32 SendInput API).
 * No external tools required — everything is built into Windows.
 *
 * Performance: ~30-80ms per action (PowerShell startup amortized over batch).
 *
 * Architecture:
 *   - Mouse/keyboard: C# PInvoke to user32.dll SendInput (most reliable)
 *   - Screenshot: PowerShell Graphics + System.Drawing
 *   - App launch: Start-Process / Shell.Application
 *   - Screen info: System.Windows.Forms.Screen
 */

import { spawn } from "node:child_process";
import type {
  IOSDriver,
  Point,
  Region,
  ScreenDimensions,
  MouseButton,
  ScrollDirection,
} from "./interface.js";

// ─── Persistent PowerShell bridge ────────────────────────────────

/**
 * C# helper class injected once into the PowerShell session.
 * Uses SendInput (most reliable) for mouse + keyboard events.
 */
const CS_HELPER = String.raw`
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Drawing;
using System.Windows.Forms;

public class StackOwlInput {
    [DllImport("user32.dll", SetLastError=true)]
    static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    static extern bool GetCursorPos(out POINT lpPoint);

    [DllImport("user32.dll")]
    static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    static extern short GetSystemMetrics(int nIndex);

    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    struct INPUT {
        public uint type;
        public INPUTUNION u;
    }

    [StructLayout(LayoutKind.Explicit)]
    struct INPUTUNION {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct MOUSEINPUT {
        public int dx, dy;
        public uint mouseData;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct KEYBDINPUT {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    const uint INPUT_MOUSE = 0, INPUT_KEYBOARD = 1;
    const uint MOUSEEVENTF_MOVE=0x1, MOUSEEVENTF_LEFTDOWN=0x2, MOUSEEVENTF_LEFTUP=0x4;
    const uint MOUSEEVENTF_RIGHTDOWN=0x8, MOUSEEVENTF_RIGHTUP=0x10;
    const uint MOUSEEVENTF_MIDDLEDOWN=0x20, MOUSEEVENTF_MIDDLEUP=0x40;
    const uint MOUSEEVENTF_ABSOLUTE=0x8000, MOUSEEVENTF_WHEEL=0x800;
    const uint KEYEVENTF_KEYUP=0x2, KEYEVENTF_UNICODE=0x4;

    static int ScreenW { get { return GetSystemMetrics(0); } }
    static int ScreenH { get { return GetSystemMetrics(1); } }

    public static void MouseMove(int x, int y) {
        SetCursorPos(x, y);
    }

    static void SendMouse(int x, int y, uint flags, uint data=0) {
        int ax = (int)((x * 65535.0) / ScreenW);
        int ay = (int)((y * 65535.0) / ScreenH);
        INPUT[] inp = new INPUT[1];
        inp[0].type = INPUT_MOUSE;
        inp[0].u.mi.dx = ax; inp[0].u.mi.dy = ay;
        inp[0].u.mi.mouseData = data;
        inp[0].u.mi.dwFlags = flags | MOUSEEVENTF_ABSOLUTE;
        SendInput(1, inp, Marshal.SizeOf(typeof(INPUT)));
    }

    public static void Click(int x, int y, string button, int count) {
        SetCursorPos(x, y);
        System.Threading.Thread.Sleep(30);
        uint dn = button=="right" ? MOUSEEVENTF_RIGHTDOWN : (button=="middle" ? MOUSEEVENTF_MIDDLEDOWN : MOUSEEVENTF_LEFTDOWN);
        uint up = button=="right" ? MOUSEEVENTF_RIGHTUP   : (button=="middle" ? MOUSEEVENTF_MIDDLEUP   : MOUSEEVENTF_LEFTUP);
        for(int i=0; i<count; i++) { SendMouse(x,y,dn); SendMouse(x,y,up); if(i<count-1) System.Threading.Thread.Sleep(60); }
    }

    public static void Scroll(string dir, int amount) {
        int delta = (dir=="up"||dir=="left") ? amount*120 : -amount*120;
        uint flag = (dir=="left"||dir=="right") ? 0x01000u : MOUSEEVENTF_WHEEL;
        // Use mouse_event for wheel since SendInput wheel needs foreground window
        INPUT[] inp = new INPUT[1];
        inp[0].type = INPUT_MOUSE;
        inp[0].u.mi.mouseData = (uint)delta;
        inp[0].u.mi.dwFlags = flag;
        SendInput(1, inp, Marshal.SizeOf(typeof(INPUT)));
    }

    public static void TypeText(string text) {
        foreach(char c in text) {
            INPUT[] inp = new INPUT[2];
            inp[0].type = INPUT_KEYBOARD;
            inp[0].u.ki.wVk = 0; inp[0].u.ki.wScan = (ushort)c;
            inp[0].u.ki.dwFlags = KEYEVENTF_UNICODE;
            inp[1] = inp[0]; inp[1].u.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP;
            SendInput(2, inp, Marshal.SizeOf(typeof(INPUT)));
        }
    }

    public static void PressKey(ushort vk, bool shift, bool ctrl, bool alt) {
        INPUT[] inp = new INPUT[8];
        int n=0;
        if(alt)   { inp[n].type=INPUT_KEYBOARD; inp[n].u.ki.wVk=0x12; n++; }
        if(ctrl)  { inp[n].type=INPUT_KEYBOARD; inp[n].u.ki.wVk=0x11; n++; }
        if(shift) { inp[n].type=INPUT_KEYBOARD; inp[n].u.ki.wVk=0x10; n++; }
        inp[n].type=INPUT_KEYBOARD; inp[n].u.ki.wVk=vk; n++;
        inp[n].type=INPUT_KEYBOARD; inp[n].u.ki.wVk=vk; inp[n].u.ki.dwFlags=KEYEVENTF_KEYUP; n++;
        if(shift) { inp[n].type=INPUT_KEYBOARD; inp[n].u.ki.wVk=0x10; inp[n].u.ki.dwFlags=KEYEVENTF_KEYUP; n++; }
        if(ctrl)  { inp[n].type=INPUT_KEYBOARD; inp[n].u.ki.wVk=0x11; inp[n].u.ki.dwFlags=KEYEVENTF_KEYUP; n++; }
        if(alt)   { inp[n].type=INPUT_KEYBOARD; inp[n].u.ki.wVk=0x12; inp[n].u.ki.dwFlags=KEYEVENTF_KEYUP; n++; }
        INPUT[] final = new INPUT[n];
        Array.Copy(inp, final, n);
        SendInput((uint)n, final, Marshal.SizeOf(typeof(INPUT)));
    }

    public static string GetCursorPosStr() {
        POINT p; GetCursorPos(out p); return p.X+","+p.Y;
    }

    public static string GetScreenSize() {
        return ScreenW+","+ScreenH;
    }
}
"@ -ReferencedAssemblies "System.Windows.Forms","System.Drawing" -ErrorAction SilentlyContinue
`;

// VK code map for common keys
const VK: Record<string, number> = {
  enter: 0x0d,
  return: 0x0d,
  tab: 0x09,
  backspace: 0x08,
  delete: 0x2e,
  escape: 0x1b,
  esc: 0x1b,
  space: 0x20,
  up: 0x26,
  down: 0x28,
  left: 0x25,
  right: 0x27,
  home: 0x24,
  end: 0x23,
  pageup: 0x21,
  pagedown: 0x22,
  f1: 0x70,
  f2: 0x71,
  f3: 0x72,
  f4: 0x73,
  f5: 0x74,
  f6: 0x75,
  f7: 0x76,
  f8: 0x77,
  f9: 0x78,
  f10: 0x79,
  f11: 0x7a,
  f12: 0x7b,
};

// Add A-Z and 0-9
for (let i = 0; i < 26; i++) VK[String.fromCharCode(97 + i)] = 65 + i;
for (let i = 0; i <= 9; i++) VK[String(i)] = 48 + i;

export class WindowsDriver implements IOSDriver {
  readonly platform = "win32" as const;

  private psProc: ReturnType<typeof spawn> | null = null;
  private pending: Map<
    string,
    { resolve: (v: string) => void; reject: (e: Error) => void }
  > = new Map();
  private lineBuffer = "";
  private idCounter = 0;
  private ready = false;

  isReady(): boolean {
    return this.ready && this.psProc !== null && !this.psProc.killed;
  }

  async init(): Promise<void> {
    if (this.ready) return;

    await new Promise<void>((resolve, reject) => {
      // Launch persistent PowerShell session
      const proc = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", "-"], {
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });

      this.psProc = proc;

      proc.stdout!.setEncoding("utf8");
      proc.stdout!.on("data", (chunk: string) => {
        this.lineBuffer += chunk;
        const lines = this.lineBuffer.split("\n");
        this.lineBuffer = lines.pop() ?? "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          // Lines are: "ID|result" or "ID|ERROR|message"
          const sep = trimmed.indexOf("|");
          if (sep === -1) continue;
          const id = trimmed.slice(0, sep);
          const rest = trimmed.slice(sep + 1);
          const pending = this.pending.get(id);
          if (!pending) continue;
          this.pending.delete(id);
          if (rest.startsWith("ERROR|")) {
            pending.reject(new Error(rest.slice(6)));
          } else {
            pending.resolve(rest);
          }
        }
      });

      proc.on("error", reject);
      proc.on("close", () => {
        this.ready = false;
        for (const [, p] of this.pending) p.reject(new Error("PowerShell exited"));
        this.pending.clear();
      });

      // Inject C# helper, then signal ready
      const initScript =
        CS_HELPER +
        `\nWrite-Host "READY"\n`;

      proc.stdin!.write(initScript, "utf8");

      // Wait for ready signal on stderr or a dedicated echo
      proc.stdout!.once("data", () => {
        setTimeout(() => {
          this.ready = true;
          resolve();
        }, 200);
      });

      setTimeout(() => {
        this.ready = true;
        resolve();
      }, 3000);
    });
  }

  private sendPS(id: string, script: string): Promise<string> {
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });

      const wrapped =
        `try { $r = (${script}); Write-Host "${id}|$r" } ` +
        `catch { Write-Host "${id}|ERROR|$($_.Exception.Message)" }\n`;

      this.psProc!.stdin!.write(wrapped, "utf8");

      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`Timeout for PS command: ${script.slice(0, 60)}`));
        }
      }, 8000);
    });
  }

  private async ps(script: string): Promise<string> {
    const id = String(this.idCounter++);
    return this.sendPS(id, script);
  }

  async dispose(): Promise<void> {
    this.ready = false;
    this.psProc?.stdin?.end();
    this.psProc?.kill();
    this.psProc = null;
  }

  async getScreenSize(): Promise<ScreenDimensions> {
    await this.init();
    const r = await this.ps("[StackOwlInput]::GetScreenSize()");
    const [w, h] = r.split(",").map(Number);
    return { width: w, height: h, scaleFactor: 1 };
  }

  async getCursorPosition(): Promise<Point> {
    await this.init();
    const r = await this.ps("[StackOwlInput]::GetCursorPosStr()");
    const [x, y] = r.split(",").map(Number);
    return { x, y };
  }

  async screenshot(outputPath: string, region?: Region): Promise<void> {
    await this.init();
    const script = region
      ? `$bmp=New-Object System.Drawing.Bitmap(${region.width},${region.height});$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen(${region.x},${region.y},0,0,[System.Drawing.Size]::new(${region.width},${region.height}));$bmp.Save('${outputPath.replace(/\\/g, "\\\\")}');$g.Dispose();$bmp.Dispose();'ok'`
      : `$ss=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;$bmp=New-Object System.Drawing.Bitmap($ss.Width,$ss.Height);$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen($ss.Location,[System.Drawing.Point]::Empty,$ss.Size);$bmp.Save('${outputPath.replace(/\\/g, "\\\\")}');$g.Dispose();$bmp.Dispose();'ok'`;
    await this.ps(script);
  }

  async mouseMove(x: number, y: number): Promise<void> {
    await this.ps(`[StackOwlInput]::MouseMove(${x},${y}); 'ok'`);
  }

  async mouseClick(x: number, y: number, button: MouseButton, count: number): Promise<void> {
    await this.ps(`[StackOwlInput]::Click(${x},${y},'${button}',${count}); 'ok'`);
  }

  async mouseDrag(fromX: number, fromY: number, toX: number, toY: number): Promise<void> {
    // Simple drag via SetCursorPos + mouse events
    const steps = 20;
    const script = [
      `[StackOwlInput]::MouseMove(${fromX},${fromY})`,
      `Start-Sleep -Milliseconds 50`,
      // Simulate drag in steps
      ...Array.from({ length: steps + 1 }, (_, i) => {
        const t = i / steps;
        const x = Math.round(fromX + (toX - fromX) * t);
        const y = Math.round(fromY + (toY - fromY) * t);
        return `[StackOwlInput]::MouseMove(${x},${y}); Start-Sleep -Milliseconds 15`;
      }),
      `'ok'`,
    ].join(";");
    await this.ps(script);
  }

  async scroll(direction: ScrollDirection, amount: number): Promise<void> {
    await this.ps(`[StackOwlInput]::Scroll('${direction}',${amount}); 'ok'`);
  }

  async typeText(text: string): Promise<void> {
    // Escape single quotes in PS string
    const escaped = text.replace(/'/g, "''");
    await this.ps(`[StackOwlInput]::TypeText('${escaped}'); 'ok'`);
  }

  async typeChar(char: string): Promise<void> {
    const escaped = char.replace(/'/g, "''");
    await this.ps(`[StackOwlInput]::TypeText('${escaped}'); 'ok'`);
  }

  async pressKey(key: string, modifiers: string[] = []): Promise<void> {
    const vk = VK[key.toLowerCase()] ?? key.charCodeAt(0);
    const shift = modifiers.some((m) => m.toLowerCase() === "shift");
    const ctrl = modifiers.some((m) => ["ctrl", "control"].includes(m.toLowerCase()));
    const alt = modifiers.some((m) => ["alt", "option"].includes(m.toLowerCase()));
    await this.ps(
      `[StackOwlInput]::PressKey(${vk},$${shift},$${ctrl},$${alt}); 'ok'`,
    );
  }

  async openApp(name: string): Promise<void> {
    const escaped = name.replace(/'/g, "''");
    await this.ps(`Start-Process '${escaped}'; 'ok'`);
  }

  async openUrl(url: string): Promise<void> {
    const escaped = url.replace(/'/g, "''");
    await this.ps(`Start-Process '${escaped}'; 'ok'`);
  }

  async getFrontApp(): Promise<string> {
    return this.ps(
      `(Get-Process | Where-Object {$_.MainWindowHandle -eq [user32.dll]::GetForegroundWindow()} | Select-Object -First 1 -ExpandProperty Name) ?? 'unknown'`,
    );
  }

  async sleep(ms: number): Promise<void> {
    await this.ps(`Start-Sleep -Milliseconds ${ms}; 'ok'`);
  }
}

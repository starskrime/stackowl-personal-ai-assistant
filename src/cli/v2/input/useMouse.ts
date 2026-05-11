import { useEffect, useRef } from "react";

export interface MouseEvent {
  row: number;    // 1-indexed terminal row
  col: number;    // 1-indexed terminal column
  button: 0 | 1 | 2;  // 0=left, 1=middle, 2=right
  type: "press" | "release";
}

/**
 * Enables xterm SGR mouse tracking while mounted, disables on unmount.
 * Parses \x1B[<btn;col;rowM (press) and \x1B[<btn;col;rowm (release).
 * Works in Ink raw mode — Ink ignores these sequences, we parse them.
 */
export function useMouse(handler: (e: MouseEvent) => void): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    // \x1B[?1000h = basic X10 click mode
    // \x1B[?1006h = SGR extended (needed for cols > 223 and clean parsing)
    process.stdout.write("\x1B[?1000h\x1B[?1006h");

    const onData = (chunk: Buffer | string) => {
      const str = Buffer.isBuffer(chunk) ? chunk.toString() : chunk;
      const re = /\x1B\[<(\d+);(\d+);(\d+)([Mm])/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(str)) !== null) {
        const btnRaw = parseInt(m[1]!, 10);
        const col    = parseInt(m[2]!, 10);
        const row    = parseInt(m[3]!, 10);
        const type   = m[4] === "M" ? "press" as const : "release" as const;
        const button = (btnRaw & 3) as 0 | 1 | 2;
        handlerRef.current({ row, col, button, type });
      }
    };

    process.stdin.on("data", onData);

    return () => {
      process.stdin.off("data", onData);
      process.stdout.write("\x1B[?1000l\x1B[?1006l");
    };
  }, []); // mount/unmount only; handler is always current via ref
}

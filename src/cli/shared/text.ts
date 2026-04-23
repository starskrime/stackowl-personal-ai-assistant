export function stripAnsi(s: string): string {
  return s.replace(/\x1B\[[0-9;]*[A-Za-z]/g, "");
}

export function visLen(s: string): number {
  const plain = stripAnsi(s);
  let len = 0;
  for (const ch of plain) {
    const cp = ch.codePointAt(0) ?? 0;
    len += cp > 0xffff ? 2 : 1;
  }
  return len;
}

export function padR(s: string, w: number): string {
  return s + " ".repeat(Math.max(0, w - visLen(s)));
}

export function trunc(s: string, max: number): string {
  const plain = stripAnsi(s);
  return plain.length > max ? plain.slice(0, max) + "…" : plain;
}

export function wrapText(text: string, maxCols: number): string[] {
  const result: string[] = [];
  for (const para of text.split("\n")) {
    if (!para) { result.push(""); continue; }
    let rem = para;
    while (rem.length > maxCols) {
      let bp = rem.lastIndexOf(" ", maxCols);
      if (bp < 0) bp = maxCols;
      result.push(rem.slice(0, bp));
      rem = rem.slice(bp).trimStart();
    }
    if (rem) result.push(rem);
  }
  return result;
}

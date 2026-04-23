export const ESC = "\x1B";

export const ansi = {
  altIn:  `${ESC}[?1049h`,
  altOut: `${ESC}[?1049l`,
  hide:   `${ESC}[?25l`,
  show:   `${ESC}[?25h`,
  clear:  `${ESC}[2J\x1B[1;1H`,
  el:     `${ESC}[2K`,
  pos:    (r: number, c = 1) => `${ESC}[${r};${c}H`,
};

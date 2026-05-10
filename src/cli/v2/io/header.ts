/**
 * Writes the persistent StackOwl header to stdout once, before Ink starts.
 * Ink renders below this content and never touches it — it stays at the top
 * of the terminal scroll buffer for the lifetime of the session.
 */

const LOGO_LINES: Array<{ text: string; bright: boolean }> = [
  { text: "███████╗████████╗ █████╗  ██████╗██╗  ██╗ ██████╗ ██╗    ██╗██╗     ", bright: true  },
  { text: "██╔════╝╚══██╔══╝██╔══██╗██╔════╝██║ ██╔╝██╔═══██╗██║    ██║██║     ", bright: true  },
  { text: "███████╗   ██║   ███████║██║     █████╔╝ ██║   ██║██║ █╗ ██║██║     ", bright: true  },
  { text: "╚════██║   ██║   ██╔══██║██║     ██╔═██╗ ██║   ██║██║███╗██║██║     ", bright: false },
  { text: "███████║   ██║   ██║  ██║╚██████╗██║  ██╗╚██████╔╝╚███╔███╔╝███████╗", bright: false },
  { text: "╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝╚══════╝", bright: false },
];

const GREEN  = "\x1b[32m";
const AMBER  = "\x1b[1m\x1b[33m";   // bold yellow — top half of logo
const DIM    = "\x1b[2m\x1b[33m";   // dim yellow  — bottom half (renders warm red/brown)
const BOLD   = "\x1b[1m";
const DIMTXT = "\x1b[2m";
const RESET  = "\x1b[0m";

export function writeHeader(out: NodeJS.WriteStream): void {
  const cols = out.columns ?? 80;
  const rule = GREEN + "─".repeat(cols) + RESET + "\n";

  out.write(rule);
  for (const { text, bright } of LOGO_LINES) {
    out.write((bright ? AMBER : DIM) + text + RESET + "\n");
  }
  out.write(" " + BOLD + "Personal AI Assistant" + RESET + DIMTXT + " • Challenge Everything" + RESET + "\n");
  out.write(rule);
}

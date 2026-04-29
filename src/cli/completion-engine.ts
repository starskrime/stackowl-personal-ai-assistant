export interface CompletionProvider {
  topLevelNames(): string[];
  subcommands(commandName: string): string[];
}

export interface CompletionResult {
  items: string[];
  mode: "command" | "subcommand";
}

export class CompletionEngine {
  constructor(private provider: CompletionProvider) {}

  complete(buf: string): CompletionResult {
    if (!buf.startsWith("/")) return { items: [], mode: "command" };

    const inner = buf.slice(1);
    const spaceIdx = inner.indexOf(" ");

    if (spaceIdx === -1) {
      const filter = inner.toLowerCase();
      const items = filter
        ? this.provider.topLevelNames().filter((n) => n.toLowerCase().startsWith(filter))
        : this.provider.topLevelNames();
      return { items, mode: "command" };
    }

    const cmdName = inner.slice(0, spaceIdx).toLowerCase();
    const partial = inner.slice(spaceIdx + 1).toLowerCase();
    const subs = this.provider.subcommands(cmdName);
    const items = partial ? subs.filter((s) => s.toLowerCase().startsWith(partial)) : subs;
    return { items, mode: "subcommand" };
  }
}

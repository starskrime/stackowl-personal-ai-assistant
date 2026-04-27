import type { UnclarityItem } from './types.js';

export class UnclaritySurfacer {
  private unclarities: UnclarityItem[] = [];
  private surfacingPatterns: RegExp[] = [
    /which\s+(?:file|folder|project|item|one)\b/i,
    /which\s+(?:one|items?)\s+(?:did\s+you|were\s+you|is\s+it)\s+(?:mean|talking|referring)/i,
    /\b(?:unsure|uncertain|unclear|confused?|not sure)\b\s+(?:about|if|whether)/i,
    /\b(?:don't|do not|doesn't|does not)\s+(?:know|understand)\s+(?:which|what|where)/i,
    /need\s+more\s+(?:info|information|details|context)/i,
    /\b(?:could you|can you|please)\s+(?:specify|clarify|explain)/i,
  ];

  detectUnclarity(message: string, priorContext: string[] = []): UnclarityItem | null {
    const messageLower = message.toLowerCase();

    for (const pattern of this.surfacingPatterns) {
      if (pattern.test(messageLower)) {
        const existing = this.unclarities.find(u => u.sourceMessage === message);
        if (existing) return null;

        const alreadyAddressed = this.unclarities.some(u =>
          u.addressed &&
          priorContext.join(' ').toLowerCase().includes(u.description.toLowerCase().slice(0, 30))
        );
        if (alreadyAddressed) {
          return null;
        }

        const unclarity = this.extractUnclarity(message, priorContext);
        if (unclarity) {
          this.unclarities.push(unclarity);
          return unclarity;
        }
      }
    }

    return null;
  }

  private extractUnclarity(message: string, priorContext: string[]): UnclarityItem | null {
    const whichMatch = message.match(/which\s+(\w+(?:\s+\w+)?)/i);
    const unsureMatch = message.match(/(?:unsure|uncertain|unclear|confused?)\s+(?:about|if|whether)\s+([^,.]+)/i);
    const needMoreMatch = message.match(/need\s+more\s+(?:info|information|details|context)\s+(?:about|on|regarding)\s+([^,.]+)/i);

    let description: string;

    if (unsureMatch) {
      description = `unclear about: ${unsureMatch[1].trim()}`;
    } else if (whichMatch) {
      description = `Which ${whichMatch[1]} should I focus on?`;
    } else if (needMoreMatch) {
      description = `Need more information about: ${needMoreMatch[1].trim()}`;
    } else {
      description = `Unclear aspect detected in your message`;
    }

    return {
      id: `unclarity_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      description,
      sourceMessage: message,
      detectedAt: new Date().toISOString(),
      addressed: false,
    };
  }

  surfaceUnclarity(unclarity: UnclarityItem): string {
    return `I'm unclear about ${unclarity.description} from your last message. Could you clarify?`;
  }

  getActiveUnclarities(): UnclarityItem[] {
    return this.unclarities.filter(u => !u.addressed);
  }

  addressUnclarity(unclarityId: string): void {
    const unclarity = this.unclarities.find(u => u.id === unclarityId);
    if (unclarity) {
      unclarity.addressed = true;
      unclarity.addressedAt = new Date().toISOString();
    }
  }

  getUnclaritySummary(): string {
    const active = this.getActiveUnclarities();
    if (active.length === 0) return '';

    if (active.length === 1) {
      return this.surfaceUnclarity(active[0]);
    }

    return `I have ${active.length} unclear items:\n${active.map((u, i) => `${i + 1}. ${u.description}`).join('\n')}`;
  }

  shouldSurfaceProactively(priorMessage: string, _currentContext: string[]): boolean {
    const explicitConfusion = [
      /I'm not sure (?:which|what|where|who|how)/i,
      /I don't know which/i,
      /could you (?:please )?clarify (?:which|what)/i,
    ];

    return explicitConfusion.some(trigger => trigger.test(priorMessage));
  }

  clear(): void {
    this.unclarities = this.unclarities.filter(u => !u.addressed);
  }

  clearAll(): void {
    this.unclarities = [];
  }
}

export const unclaritySurfacer = new UnclaritySurfacer();

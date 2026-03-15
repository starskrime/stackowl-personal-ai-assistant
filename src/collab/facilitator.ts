import { Logger } from '../logger.js';
import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { SharedSession, CollabMessage } from './types.js';

const log = new Logger('FACILITATOR');

export class CollabFacilitator {
  constructor(private provider: ModelProvider) {}

  async detectDisagreement(messages: CollabMessage[]): Promise<{
    hasDisagreement: boolean;
    participants: string[];
    topic: string;
  } | null> {
    const recent = messages.slice(-10);
    if (recent.length < 4) return null;

    const userMessages = recent.filter(m => m.role === 'user');
    const uniqueUsers = new Set(userMessages.map(m => m.userId));
    if (uniqueUsers.size < 2) return null;

    const conversationText = userMessages
      .map(m => `[${m.displayName}]: ${m.content}`)
      .join('\n');

    try {
      const response = await this.provider.chat(
        [
          {
            role: 'system',
            content: 'Analyze if participants disagree. Respond with JSON only: {"hasDisagreement": bool, "participants": ["name1", "name2"], "topic": "what they disagree on"}'
          },
          {
            role: 'user',
            content: `Conversation:\n${conversationText}`,
          },
        ],
        undefined,
        { temperature: 0, maxTokens: 200 },
      );

      const match = response.content.match(/\{[\s\S]*\}/);
      if (!match) return null;
      return JSON.parse(match[0]);
    } catch (err) {
      log.debug(`Disagreement detection failed: ${err}`);
      return null;
    }
  }

  async summarize(session: SharedSession): Promise<string> {
    const conversationText = session.messages
      .map(m => `[${m.displayName} (${m.role})]: ${m.content}`)
      .join('\n');

    try {
      const response = await this.provider.chat(
        [
          {
            role: 'system',
            content: 'Summarize this collaborative session. Include: key discussion points, decisions made, action items, and any unresolved disagreements. Be concise.',
          },
          {
            role: 'user',
            content: `Session: "${session.name}"\nParticipants: ${session.participants.map(p => p.displayName).join(', ')}\n\n${conversationText}`,
          },
        ],
        undefined,
        { temperature: 0.3, maxTokens: 500 },
      );

      return response.content;
    } catch (err) {
      log.error(`Session summary failed: ${err}`);
      return 'Summary generation failed.';
    }
  }

  formatDecisionPrompt(
    topic: string,
    positions: { userId: string; displayName: string; position: string }[],
    mode: 'consensus' | 'majority' | 'owner_decides',
  ): string {
    const positionsText = positions
      .map(p => `- **${p.displayName}**: ${p.position}`)
      .join('\n');

    const modeInstruction = {
      consensus: 'All members must agree. Help find common ground.',
      majority: 'A majority vote will decide. Present the options clearly for voting.',
      owner_decides: 'The session owner will make the final call. Present all perspectives fairly.',
    }[mode];

    return `A decision is needed on: **${topic}**\n\nCurrent positions:\n${positionsText}\n\n${modeInstruction}`;
  }

  toEngineMessages(session: SharedSession, currentUserId: string): ChatMessage[] {
    return session.messages.map(m => {
      if (m.role === 'assistant') {
        return { role: 'assistant' as const, content: m.content };
      }
      const prefix = m.userId === currentUserId ? '' : `[${m.displayName}]: `;
      return { role: 'user' as const, content: `${prefix}${m.content}` };
    });
  }
}

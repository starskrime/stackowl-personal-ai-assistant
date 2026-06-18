export type ParticipantRole = "owner" | "member" | "observer";

export interface Participant {
  userId: string;
  displayName: string;
  role: ParticipantRole;
  joinedAt: string;
  lastActiveAt: string;
  channelId: string;
  preferences?: Record<string, unknown>;
  expertise?: string[];
}

export interface SharedSession {
  id: string;
  name: string;
  owlName: string;
  participants: Participant[];
  messages: CollabMessage[];
  metadata: {
    createdAt: string;
    lastActivity: string;
    topic?: string;
    decision?: string;
  };
  settings: SessionSettings;
}

export interface CollabMessage {
  id: string;
  userId: string;
  displayName: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  replyTo?: string;
  reactions?: Record<string, string[]>;
}

export interface SessionSettings {
  maxParticipants: number;
  allowObservers: boolean;
  roundRobin: boolean;
  decisionMode: "consensus" | "majority" | "owner_decides";
  autoSummarize: boolean;
}

export interface CollabConfig {
  maxActiveSessions: number;
  sessionTimeoutMinutes: number;
  maxMessagesPerSession: number;
}

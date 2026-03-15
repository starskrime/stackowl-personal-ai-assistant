export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool' | string;
  content: string;
}

export interface TimelineSnapshot {
  id: string;
  sessionId: string;
  messageIndex: number;
  messages: ChatMessage[];
  metadata: {
    owlName: string;
    snapshotAt: string;
    description?: string;
  };
}

export interface SessionFork {
  id: string;
  parentSessionId: string;
  parentSnapshotId: string;
  forkIndex: number;
  newSessionId: string;
  forkReason?: string;
  createdAt: string;
}

export interface TimelineView {
  sessionId: string;
  snapshots: TimelineSnapshot[];
  forks: SessionFork[];
  totalMessages: number;
  created: string;
  lastActivity: string;
}

export interface ReplayOptions {
  speed: 'instant' | 'normal' | 'slow';
  fromIndex?: number;
  toIndex?: number;
  filter?: 'all' | 'user_only' | 'assistant_only';
}

export interface ReplayMessage {
  index: number;
  role: string;
  content: string;
  timestamp?: string;
  isForked?: boolean;
}

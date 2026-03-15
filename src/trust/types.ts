export type ActionCategory =
  | 'file_read' | 'file_write' | 'file_delete'
  | 'shell_safe' | 'shell_dangerous'
  | 'git_read' | 'git_write' | 'git_push'
  | 'web_fetch' | 'web_scrape'
  | 'system_info' | 'system_modify'
  | 'skill_invoke' | 'skill_synthesize'
  | 'send_message' | 'send_file';

export type TrustLevel = 'supervised' | 'prompted' | 'trusted' | 'autonomous';

export interface TrustScore {
  category: ActionCategory;
  level: TrustLevel;
  approvalCount: number;
  denialCount: number;
  totalExecutions: number;
  successCount: number;
  failureCount: number;
  lastApproved: string | null;
  lastDenied: string | null;
  confidence: number;
}

export interface TrustThresholds {
  promptedAfter: number;
  trustedAfter: number;
  autonomousAfter: number;
  denialPenalty: number;
  decayDays: number;
}

export interface TrustDecision {
  category: ActionCategory;
  level: TrustLevel;
  allowed: boolean;
  reason: string;
  confidence: number;
}

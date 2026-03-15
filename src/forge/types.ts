export type DemoStepType = 'command' | 'file_read' | 'file_write' | 'web_fetch' | 'api_call' | 'user_input' | 'decision';

export interface DemoStep {
  order: number;
  type: DemoStepType;
  action: string;
  input?: string;
  output?: string;
  duration_ms: number;
  timestamp: number;
}

export interface DemoRecording {
  id: string;
  name: string;
  description: string;
  steps: DemoStep[];
  startedAt: number;
  completedAt: number;
  context: {
    cwd: string;
    env?: Record<string, string>;
  };
  generatedSkill?: string;
}

export interface ForgeConfig {
  maxSteps: number;
  maxStepOutputChars: number;
  autoGenerateSkill: boolean;
}

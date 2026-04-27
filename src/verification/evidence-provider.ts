export type EvidenceType = 'file_creation' | 'file_modification' | 'command_output' | 'tool_result' | 'screenshot' | 'text';

export interface EvidenceArtifact {
  type: EvidenceType;
  path?: string;
  content?: string;
  timestamp: string;
  description?: string;
}

export interface Evidence {
  taskId: string;
  artifacts: EvidenceArtifact[];
  gatheredAt: string;
  isComplete: boolean;
}

export interface EvidenceProviderConfig {
  maxArtifacts?: number;
  includeTimestamps?: boolean;
}

const DEFAULT_CONFIG: EvidenceProviderConfig = {
  maxArtifacts: 20,
  includeTimestamps: true,
};

export class EvidenceProvider {
  private config: EvidenceProviderConfig;
  private evidenceCache: Map<string, Evidence> = new Map();
  private toolResults: Map<string, EvidenceArtifact[]> = new Map();

  constructor(config: Partial<EvidenceProviderConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  recordToolResult(taskId: string, toolName: string, result: string): void {
    const artifact: EvidenceArtifact = {
      type: 'tool_result',
      content: result,
      timestamp: new Date().toISOString(),
      description: `Result from tool: ${toolName}`,
    };

    const existing = this.toolResults.get(taskId) ?? [];
    existing.push(artifact);
    this.toolResults.set(taskId, existing);
  }

  recordFileCreation(taskId: string, filePath: string, description?: string): void {
    const artifact: EvidenceArtifact = {
      type: 'file_creation',
      path: filePath,
      timestamp: new Date().toISOString(),
      description: description ?? `File created: ${filePath}`,
    };

    const existing = this.toolResults.get(taskId) ?? [];
    existing.push(artifact);
    this.toolResults.set(taskId, existing);
  }

  recordFileModification(taskId: string, filePath: string, description?: string): void {
    const artifact: EvidenceArtifact = {
      type: 'file_modification',
      path: filePath,
      timestamp: new Date().toISOString(),
      description: description ?? `File modified: ${filePath}`,
    };

    const existing = this.toolResults.get(taskId) ?? [];
    existing.push(artifact);
    this.toolResults.set(taskId, existing);
  }

  recordCommandOutput(taskId: string, command: string, output: string): void {
    const artifact: EvidenceArtifact = {
      type: 'command_output',
      content: output,
      timestamp: new Date().toISOString(),
      description: `Command executed: ${command}`,
    };

    const existing = this.toolResults.get(taskId) ?? [];
    existing.push(artifact);
    this.toolResults.set(taskId, existing);
  }

  gatherEvidence(taskId: string): Evidence {
    this.logBehavioral('gathered', taskId);

    const artifacts = this.toolResults.get(taskId) ?? [];
    const trimmedArtifacts = artifacts.slice(-(this.config.maxArtifacts ?? 20));

    const evidence: Evidence = {
      taskId,
      artifacts: trimmedArtifacts,
      gatheredAt: new Date().toISOString(),
      isComplete: artifacts.length > 0,
    };

    this.evidenceCache.set(taskId, evidence);

    if (!evidence.isComplete) {
      this.logBehavioral('insufficient', taskId);
    }

    return evidence;
  }

  formatEvidence(evidence: Evidence): string {
    if (!evidence.isComplete || evidence.artifacts.length === 0) {
      return 'No evidence available for this task.';
    }

    const lines: string[] = ['## Evidence of Completion\n'];

    for (const artifact of evidence.artifacts) {
      lines.push(this.formatArtifact(artifact));
      lines.push('');
    }

    return lines.join('\n');
  }

  private formatArtifact(artifact: EvidenceArtifact): string {
    const parts: string[] = [];

    switch (artifact.type) {
      case 'file_creation':
        parts.push(`**File Created:** ${artifact.path ?? 'unknown'}`);
        break;
      case 'file_modification':
        parts.push(`**File Modified:** ${artifact.path ?? 'unknown'}`);
        break;
      case 'command_output':
        parts.push(`**Command Output:**\n\`\`\`\n${artifact.content?.slice(0, 500) ?? ''}${artifact.content && artifact.content.length > 500 ? '\n...' : ''}\n\`\`\``);
        break;
      case 'tool_result':
        parts.push(`**Tool Result:** ${artifact.content?.slice(0, 300) ?? ''}${artifact.content && artifact.content.length > 300 ? '...' : ''}`);
        break;
      case 'screenshot':
        parts.push(`**Screenshot:** ${artifact.path ?? 'available'}`);
        break;
      default:
        parts.push(`**Evidence:** ${artifact.content ?? artifact.path ?? 'unknown'}`);
    }

    if (this.config.includeTimestamps && artifact.timestamp) {
      const time = artifact.timestamp.replace('T', ' ').slice(0, 19);
      parts.push(`_Recorded at: ${time}_`);
    }

    if (artifact.description) {
      parts.push(artifact.description);
    }

    return parts.join('\n');
  }

  getEvidence(taskId: string): Evidence | undefined {
    return this.evidenceCache.get(taskId);
  }

  clearEvidence(taskId: string): void {
    this.toolResults.delete(taskId);
    this.evidenceCache.delete(taskId);
  }

  private logBehavioral(event: 'gathered' | 'insufficient', taskId: string): void {
    const timestamp = new Date().toISOString();
    switch (event) {
      case 'gathered':
        console.log(
          `${timestamp} INFO [EvidenceProvider] behavioral.evidence.gathered taskId=${taskId}`,
        );
        break;
      case 'insufficient':
        console.log(
          `${timestamp} INFO [EvidenceProvider] behavioral.evidence.insufficient taskId=${taskId}`,
        );
        break;
    }
  }
}

export const evidenceProvider = new EvidenceProvider();
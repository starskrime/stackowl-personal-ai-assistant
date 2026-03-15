import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import matter from 'gray-matter';
import { Logger } from '../logger.js';
import type { ModelProvider } from '../providers/base.js';
import type { DemoRecording } from './types.js';

const logger = new Logger('FORGE');

export class ForgeSynthesizer {
  constructor(private provider: ModelProvider) {}

  async synthesize(recording: DemoRecording): Promise<string> {
    const prompt = `You are a skill synthesis engine. Given a recorded demonstration of steps a user performed,
generate a reusable SKILL.md file that can reproduce this workflow.

The SKILL.md format:
---
name: snake_case_name
description: One-line description
openclaw:
  emoji: "\uD83D\uDD27"
  version: "1.0"
  tags: [relevant, tags]
  source: forge
---

## Instructions

[Step-by-step instructions that use available tools to reproduce this workflow]
[Generalize hardcoded values into parameters where appropriate]
[Include error handling for each step]

Recording:
${JSON.stringify(recording.steps, null, 2)}

Context: Working directory was ${recording.context.cwd}
User's description: ${recording.description}

Generate the SKILL.md now. Output ONLY the markdown content, nothing else.`;

    logger.info(`Synthesizing skill from recording "${recording.name}" (${recording.steps.length} steps)`);

    const response = await this.provider.chat(
      [
        { role: 'system', content: 'You generate SKILL.md files from recorded demonstrations.' },
        { role: 'user', content: prompt },
      ],
      undefined,
      { temperature: 0.3 },
    );

    return response.content;
  }

  async saveSkill(skillMd: string, skillsDir: string): Promise<string> {
    const parsed = matter(skillMd);
    const name = parsed.data.name as string | undefined;

    if (!name) {
      throw new Error('Generated SKILL.md is missing a "name" field in frontmatter');
    }

    const skillDirPath = join(skillsDir, name);
    mkdirSync(skillDirPath, { recursive: true });

    const filePath = join(skillDirPath, 'SKILL.md');
    writeFileSync(filePath, skillMd, 'utf-8');
    logger.info(`Saved generated skill to ${filePath}`);
    return filePath;
  }
}

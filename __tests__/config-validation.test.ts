import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { writeFile, mkdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadConfig } from "../src/config/loader.js";

describe("Config Validation", () => {
  let testDir: string;

  beforeEach(async () => {
    testDir = join(tmpdir(), `stackowl-config-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    await mkdir(testDir, { recursive: true });
  });

  afterEach(async () => {
    await rm(testDir, { recursive: true, force: true });
  });

  it('throws when intelligence tiers are empty', async () => {
    await writeFile(
      join(testDir, 'stackowl.config.json'),
      JSON.stringify({
        defaultProvider: 'ollama',
        defaultModel: 'llama3.2',
        providers: { ollama: { baseUrl: 'http://localhost:11434' } },
        intelligence: { defaults: {}, tiers: {} },
      }),
      'utf-8',
    );
    await expect(loadConfig(testDir)).rejects.toThrow('intelligence.tiers.mid is required');
  });

  it('throws when intelligence.tiers.mid is missing', async () => {
    await writeFile(
      join(testDir, 'stackowl.config.json'),
      JSON.stringify({
        defaultProvider: 'ollama',
        defaultModel: 'llama3.2',
        providers: { ollama: { baseUrl: 'http://localhost:11434' } },
        intelligence: {
          tiers: { high: { provider: 'anthropic', model: 'opus' }, low: { provider: 'anthropic', model: 'haiku' } },
          defaults: {},
        },
      }),
      'utf-8',
    );
    await expect(loadConfig(testDir)).rejects.toThrow('intelligence.tiers.mid is required');
  });

  it('loads valid intelligence block without error', async () => {
    await writeFile(
      join(testDir, 'stackowl.config.json'),
      JSON.stringify({
        defaultProvider: 'ollama',
        defaultModel: 'llama3.2',
        providers: {
          ollama: { baseUrl: 'http://localhost:11434' },
          anthropic: { apiKey: 'sk-test', defaultModel: 'claude-sonnet-4-6' },
        },
        intelligence: {
          tiers: {
            high: { provider: 'anthropic', model: 'claude-opus-4-7' },
            mid:  { provider: 'anthropic', model: 'claude-sonnet-4-6' },
            low:  { provider: 'anthropic', model: 'claude-haiku-4-5-20251001' },
          },
          defaults: { parliament: 'high', extraction: 'low' },
        },
      }),
      'utf-8',
    );
    const config = await loadConfig(testDir);
    expect(config.intelligence?.tiers.high.model).toBe('claude-opus-4-7');
    expect(config.intelligence?.tiers.mid.model).toBe('claude-sonnet-4-6');
  });

  it('throws when intelligence.tiers.high is missing model', async () => {
    await writeFile(
      join(testDir, 'stackowl.config.json'),
      JSON.stringify({
        defaultProvider: 'ollama',
        defaultModel: 'llama3.2',
        providers: { ollama: { baseUrl: 'http://localhost:11434' } },
        intelligence: {
          tiers: {
            high: { provider: 'anthropic', model: '' },
            mid:  { provider: 'anthropic', model: 'claude-sonnet-4-6' },
            low:  { provider: 'anthropic', model: 'claude-haiku-4-5-20251001' },
          },
          defaults: {},
        },
      }),
      'utf-8',
    );
    await expect(loadConfig(testDir)).rejects.toThrow('intelligence.tiers.high is missing');
  });
});

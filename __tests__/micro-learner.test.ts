import { describe, it, expect, beforeEach } from 'vitest';
import { MicroLearner } from '../src/learning/micro-learner.js';
import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

describe('MicroLearner', () => {
  let learner: MicroLearner;
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), 'microlearner-'));
    learner = new MicroLearner(tmpDir);
  });

  it('starts with empty profile', () => {
    const profile = learner.getProfile();
    expect(profile.totalMessages).toBe(0);
    expect(Object.keys(profile.topics)).toHaveLength(0);
    expect(Object.keys(profile.toolUsage)).toHaveLength(0);
  });

  it('detects topics from messages', () => {
    learner.processMessage('Can you send an email to my boss?');
    const profile = learner.getProfile();
    expect(profile.topics['email']).toBe(1);
    expect(profile.totalMessages).toBe(1);
  });

  it('accumulates topic counts', () => {
    learner.processMessage('Send an email');
    learner.processMessage('Check my email');
    learner.processMessage('Draft an email');
    const profile = learner.getProfile();
    expect(profile.topics['email']).toBe(3);
  });

  it('detects multiple topics in one message', () => {
    learner.processMessage('Take a screenshot of my calendar');
    const profile = learner.getProfile();
    expect(profile.topics['screenshot']).toBe(1);
    expect(profile.topics['calendar']).toBe(1);
  });

  it('tracks positive sentiment', () => {
    learner.processMessage('Thanks, that was perfect!');
    const profile = learner.getProfile();
    expect(profile.positiveSignals).toBe(1);
  });

  it('tracks negative sentiment', () => {
    learner.processMessage("That's not what I wanted, too verbose");
    const profile = learner.getProfile();
    expect(profile.negativeSignals).toBe(1);
  });

  it('detects command-style messages', () => {
    learner.processMessage('Send email to bob');
    learner.processMessage('Run the tests');
    learner.processMessage('Show me the logs');
    learner.processMessage('Get the weather');
    const profile = learner.getProfile();
    expect(profile.commandRate).toBe(1); // All 4 messages are commands
  });

  it('detects question-style messages', () => {
    learner.processMessage('What is the weather?');
    learner.processMessage('How does this work?');
    const profile = learner.getProfile();
    expect(profile.questionRate).toBe(1); // Both are questions
  });

  it('tracks tool usage', () => {
    learner.processMessage('send email', ['email', 'contacts']);
    const profile = learner.getProfile();
    expect(profile.toolUsage['email']).toBe(1);
    expect(profile.toolUsage['contacts']).toBe(1);
  });

  it('records tool usage separately', () => {
    learner.recordToolUse('screenshot');
    learner.recordToolUse('screenshot');
    learner.recordToolUse('screenshot');
    const profile = learner.getProfile();
    expect(profile.toolUsage['screenshot']).toBe(3);
  });

  it('tracks hourly activity', () => {
    learner.processMessage('hello');
    const profile = learner.getProfile();
    const currentHour = new Date().getHours();
    expect(profile.hourlyActivity[currentHour]).toBe(1);
  });

  it('builds capability clusters from repeated tool usage', () => {
    // Use email twice to trigger clustering
    learner.processMessage('send email', ['email']);
    learner.processMessage('send another email', ['email']);
    const profile = learner.getProfile();
    // email cluster should suggest related capabilities
    expect(profile.capabilityClusters['email']).toBeDefined();
    expect(profile.capabilityClusters['email'].length).toBeGreaterThan(0);
  });

  it('identifies anticipated needs based on usage patterns', () => {
    // Use email enough times to generate anticipations
    for (let i = 0; i < 5; i++) {
      learner.processMessage('send email', ['email']);
    }
    const needs = learner.getAnticipatedNeeds();
    expect(needs.length).toBeGreaterThan(0);
    // Should suggest related capabilities like contacts, calendar, etc.
    const capabilities = needs.map(n => n.capability);
    expect(capabilities.some(c => ['contacts', 'calendar', 'notification', 'template'].includes(c))).toBe(true);
  });

  it('returns empty context for too few messages', () => {
    learner.processMessage('hello');
    expect(learner.toContextString()).toBe('');
  });

  it('returns context string after enough messages', () => {
    for (let i = 0; i < 10; i++) {
      learner.processMessage('send email');
    }
    const ctx = learner.toContextString();
    expect(ctx).toContain('<user_profile>');
    expect(ctx).toContain('email');
  });

  it('saves and loads profile', async () => {
    learner.processMessage('send email', ['email']);
    learner.processMessage('take screenshot', ['screenshot']);
    await learner.save();

    const learner2 = new MicroLearner(tmpDir);
    await learner2.load();
    const profile = learner2.getProfile();
    expect(profile.totalMessages).toBe(2);
    expect(profile.topics['email']).toBe(1);
  });

  it('gets peak hours', () => {
    // All messages at current hour
    for (let i = 0; i < 30; i++) {
      learner.processMessage('test');
    }
    const peaks = learner.getPeakHours();
    expect(peaks).toContain(new Date().getHours());
  });
});

import { describe, it, expect } from 'vitest';

// Re-implement normalizeToolName here since it's not exported
// (mirrors the logic in src/evolution/synthesizer.ts)
const SERVICE_SPECIFIC_PATTERNS = [
  /(?:_?via_?\w+)$/i,
  /(?:_?from_?\w+)$/i,
  /(?:_?using_?\w+)$/i,
  /(?:_?with_?\w+)$/i,
  /(?:_?on_?\w+)$/i,
  /(?:_?through_?\w+)$/i,
];

function normalizeToolName(rawName: string): string {
  let name = rawName.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
  for (const pattern of SERVICE_SPECIFIC_PATTERNS) {
    name = name.replace(pattern, '');
  }
  name = name.replace(/_+/g, '_').replace(/^_|_$/g, '');
  if (name.length < 3) return rawName.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
  return name;
}

describe('Tool Name Normalization', () => {
  it('should strip service-specific "via" suffixes', () => {
    expect(normalizeToolName('send_email_via_agentmail')).toBe('send_email');
    expect(normalizeToolName('post_message_via_slack')).toBe('post_message');
  });

  it('should strip "from" suffixes', () => {
    expect(normalizeToolName('fetch_weather_from_openweather')).toBe('fetch_weather');
    expect(normalizeToolName('download_from_gdrive')).toBe('download');
  });

  it('should strip "using" suffixes', () => {
    expect(normalizeToolName('scrape_page_using_puppeteer')).toBe('scrape_page');
  });

  it('should strip "with" suffixes', () => {
    expect(normalizeToolName('automate_browser_with_selenium')).toBe('automate_browser');
  });

  it('should strip "on" suffixes', () => {
    expect(normalizeToolName('send_message_on_telegram')).toBe('send_message');
  });

  it('should strip "through" suffixes', () => {
    expect(normalizeToolName('route_request_through_proxy')).toBe('route_request');
  });

  it('should keep already-generic names unchanged', () => {
    expect(normalizeToolName('email_send')).toBe('email_send');
    expect(normalizeToolName('screenshot_capture')).toBe('screenshot_capture');
    expect(normalizeToolName('clipboard_read')).toBe('clipboard_read');
    expect(normalizeToolName('weather_fetch')).toBe('weather_fetch');
  });

  it('should handle non-alphanumeric characters', () => {
    expect(normalizeToolName('send-email-via-gmail')).toBe('send_email');
  });

  it('should collapse multiple underscores', () => {
    expect(normalizeToolName('send__email___via_gmail')).toBe('send_email');
  });

  it('should preserve short names that are too short after stripping', () => {
    // "ab" is too short — keep original
    expect(normalizeToolName('ab')).toBe('ab');
  });

  it('should handle uppercase input', () => {
    expect(normalizeToolName('SEND_EMAIL_VIA_SENDGRID')).toBe('send_email');
  });
});

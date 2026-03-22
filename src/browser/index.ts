/**
 * StackOwl — Browser Module
 *
 * Persistent browser environment with stealth and smart fetching.
 */

export { BrowserPool } from './pool.js';
export type { BrowserPoolConfig } from './pool.js';
export { webFetch, initSmartFetch, hasBrowserPool } from './smart-fetch.js';
export type { SmartFetchOptions, FetchResult } from './smart-fetch.js';
export { findChrome } from './chrome.js';

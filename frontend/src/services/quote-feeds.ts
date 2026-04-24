import type { QuoteFeedStatus } from './types';
import { QUOTE_FEEDS } from './fixtures/quote-feeds';

export interface QuoteFeedService {
  snapshot(): QuoteFeedStatus[];
  subscribe(cb: (feeds: QuoteFeedStatus[]) => void): () => void;
}

export class MockQuoteFeedService implements QuoteFeedService {
  private feeds: QuoteFeedStatus[] = QUOTE_FEEDS;
  private listeners = new Set<(f: QuoteFeedStatus[]) => void>();

  snapshot(): QuoteFeedStatus[] {
    return this.feeds;
  }

  subscribe(cb: (f: QuoteFeedStatus[]) => void): () => void {
    this.listeners.add(cb);
    return () => { this.listeners.delete(cb); };
  }

  // Reserved for Phase 4+ — real broker feed-entitlement updates will push via notify().
  // Public so ts/lint don't flag it as dead; callers inside this module pass the current feeds.
  notify(): void {
    for (const cb of this.listeners) cb(this.feeds);
  }
}

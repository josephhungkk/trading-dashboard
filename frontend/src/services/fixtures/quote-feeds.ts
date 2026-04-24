import type { QuoteFeedStatus } from '../types';

export const QUOTE_FEEDS: QuoteFeedStatus[] = [
  { assetClass: 'stock',   exchange: 'NYSE',   feedType: 'realtime' },
  { assetClass: 'stock',   exchange: 'NASDAQ', feedType: 'realtime' },
  { assetClass: 'stock',   exchange: 'AMEX',   feedType: 'realtime' },
  { assetClass: 'stock',   exchange: 'NYSE',   feedType: 'delayed', level: 2 },
  { assetClass: 'options',                      feedType: 'delayed' },
  { assetClass: 'futures', exchange: 'CME',    feedType: 'realtime' },
  { assetClass: 'futures', exchange: 'CFE',    feedType: 'realtime' },
  { assetClass: 'forex',                        feedType: 'realtime' },
  { assetClass: 'crypto',                       feedType: 'realtime' },
];

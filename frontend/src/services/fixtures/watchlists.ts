import type { Watchlist, WatchlistColumnKey } from '../types';
import { STRESS_SYMBOLS } from './symbols';

const DEFAULT_COLUMNS: WatchlistColumnKey[] = [
  'symbol', 'description', 'last', 'change', 'changePct',
];

const EXTENDED_COLUMNS: WatchlistColumnKey[] = [
  'symbol', 'description', 'last', 'change', 'changePct',
  'bid', 'ask', 'volume', 'exchange',
];

export const WATCHLISTS: Watchlist[] = [
  {
    id: 'us-large-cap',
    name: 'US Large Cap',
    assetClass: 'stock',
    symbolIds: ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'JPM', 'BAC', 'BRK.B'],
    columnConfig: EXTENDED_COLUMNS,
  },
  {
    id: 'fx-majors',
    name: 'FX Majors',
    assetClass: 'forex',
    symbolIds: ['EURUSD', 'USDJPY', 'GBPUSD', 'USDHKD', 'AUDUSD'],
    columnConfig: DEFAULT_COLUMNS,
  },
  {
    id: 'crypto-majors',
    name: 'Crypto Majors',
    assetClass: 'crypto',
    symbolIds: ['BTC-USD', 'ETH-USD', 'SOL-USD'],
    columnConfig: DEFAULT_COLUMNS,
  },
  {
    id: 'cjk-basket',
    name: 'CJK Basket',
    assetClass: 'mixed',
    symbolIds: ['0700', '9988', '3690', '7203', '6758', '7974', '005930', '000660', '2330'],
    columnConfig: EXTENDED_COLUMNS,
  },
  {
    id: 'stress-500',
    name: 'Stress Test — 500 Symbols',
    assetClass: 'stock',
    symbolIds: STRESS_SYMBOLS.map((s) => s.symbol),
    columnConfig: DEFAULT_COLUMNS,
  },
];

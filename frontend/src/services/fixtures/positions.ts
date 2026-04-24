import type { Position } from '../types';

const ASOF = '2026-04-24T10:00:00Z';

// 30 positions: 5 per account × 6 accounts.
// Mix of winners (pnlUnrealized > 0), losers (< 0), near-flat (|pnl| < 100),
// plus mixed pnlRealized (0 and non-zero). Assets span US stocks, HK stocks,
// JP stocks, FX, crypto and ETFs so watchlist asset-class filters have data.
export const POSITIONS: Position[] = [
  // ibkr-live-1 — US + ETF, USD
  { accountId: 'ibkr-live-1', symbol: 'AAPL', qty: 200, avgCost: 180.50, marketValue: 38_200.00, pnlUnrealized: 2_100.00, pnlRealized: 450.00, currency: 'USD', asOf: ASOF },
  { accountId: 'ibkr-live-1', symbol: 'MSFT', qty: 100, avgCost: 410.00, marketValue: 42_800.00, pnlUnrealized: 1_800.00, pnlRealized: 0,       currency: 'USD', asOf: ASOF },
  { accountId: 'ibkr-live-1', symbol: 'NVDA', qty: 50,  avgCost: 920.00, marketValue: 44_000.00, pnlUnrealized: -2_000.00, pnlRealized: 1_250.00, currency: 'USD', asOf: ASOF },
  { accountId: 'ibkr-live-1', symbol: 'SPY',  qty: 30,  avgCost: 530.00, marketValue: 15_930.00, pnlUnrealized: 30.00,    pnlRealized: 0,       currency: 'USD', asOf: ASOF },
  { accountId: 'ibkr-live-1', symbol: 'TSLA', qty: 40,  avgCost: 260.00, marketValue: 9_800.00,  pnlUnrealized: -600.00,  pnlRealized: -320.00, currency: 'USD', asOf: ASOF },

  // ibkr-paper-1 — US + crypto, USD
  { accountId: 'ibkr-paper-1', symbol: 'GOOGL',   qty: 60,  avgCost: 165.00, marketValue: 10_200.00, pnlUnrealized: 300.00,   pnlRealized: 0,     currency: 'USD', asOf: ASOF },
  { accountId: 'ibkr-paper-1', symbol: 'AMZN',    qty: 30,  avgCost: 185.00, marketValue: 5_400.00,  pnlUnrealized: -150.00,  pnlRealized: 0,     currency: 'USD', asOf: ASOF },
  { accountId: 'ibkr-paper-1', symbol: 'BTC-USD', qty: 0.5, avgCost: 65000,  marketValue: 34_500.00, pnlUnrealized: 2_000.00, pnlRealized: 0,     currency: 'USD', asOf: ASOF },
  { accountId: 'ibkr-paper-1', symbol: 'ETH-USD', qty: 4,   avgCost: 3_100,  marketValue: 12_200.00, pnlUnrealized: -200.00,  pnlRealized: 150.00, currency: 'USD', asOf: ASOF },
  { accountId: 'ibkr-paper-1', symbol: 'QQQ',     qty: 20,  avgCost: 460.00, marketValue: 9_220.00,  pnlUnrealized: 20.00,    pnlRealized: 0,     currency: 'USD', asOf: ASOF },

  // futu-live-1 — HK stocks + FX, HKD
  { accountId: 'futu-live-1', symbol: '0700',   qty: 500,     avgCost: 310.00, marketValue: 162_500.00, pnlUnrealized: 7_500.00,  pnlRealized: 1_200.00, currency: 'HKD', asOf: ASOF },
  { accountId: 'futu-live-1', symbol: '9988',   qty: 1000,    avgCost: 82.00,  marketValue: 78_000.00,  pnlUnrealized: -4_000.00, pnlRealized: 0,       currency: 'HKD', asOf: ASOF },
  { accountId: 'futu-live-1', symbol: '0005',   qty: 2000,    avgCost: 62.50,  marketValue: 125_100.00, pnlUnrealized: 100.00,    pnlRealized: 0,       currency: 'HKD', asOf: ASOF },
  { accountId: 'futu-live-1', symbol: '3690',   qty: 800,     avgCost: 115.00, marketValue: 88_800.00,  pnlUnrealized: -3_200.00, pnlRealized: -500.00, currency: 'HKD', asOf: ASOF },
  { accountId: 'futu-live-1', symbol: 'USDHKD', qty: 100_000, avgCost: 7.82,   marketValue: 783_500.00, pnlUnrealized: 1_500.00,  pnlRealized: 0,       currency: 'HKD', asOf: ASOF },

  // futu-paper-1 — HK + JP, HKD/JPY
  { accountId: 'futu-paper-1', symbol: '1299', qty: 600,  avgCost: 70.00,  marketValue: 43_200.00, pnlUnrealized: 1_200.00,  pnlRealized: 0, currency: 'HKD', asOf: ASOF },
  { accountId: 'futu-paper-1', symbol: '0388', qty: 300,  avgCost: 240.00, marketValue: 70_500.00, pnlUnrealized: -1_500.00, pnlRealized: 0, currency: 'HKD', asOf: ASOF },
  { accountId: 'futu-paper-1', symbol: '7203', qty: 1000, avgCost: 2_800,  marketValue: 2_830_000, pnlUnrealized: 30_000,    pnlRealized: 0, currency: 'JPY', asOf: ASOF },
  { accountId: 'futu-paper-1', symbol: '6758', qty: 500,  avgCost: 14_000, marketValue: 6_980_000, pnlUnrealized: -20_000,   pnlRealized: 0, currency: 'JPY', asOf: ASOF },
  { accountId: 'futu-paper-1', symbol: '7974', qty: 200,  avgCost: 9_500,  marketValue: 1_900_050, pnlUnrealized: 50.00,     pnlRealized: 0, currency: 'JPY', asOf: ASOF },

  // schwab-live-1 — US stocks + ETF + FX, USD
  { accountId: 'schwab-live-1', symbol: 'JPM',    qty: 100,    avgCost: 190.00, marketValue: 19_800.00, pnlUnrealized: 800.00,  pnlRealized: 0,      currency: 'USD', asOf: ASOF },
  { accountId: 'schwab-live-1', symbol: 'BAC',    qty: 500,    avgCost: 36.00,  marketValue: 17_500.00, pnlUnrealized: -500.00, pnlRealized: 220.00, currency: 'USD', asOf: ASOF },
  { accountId: 'schwab-live-1', symbol: 'BRK.B',  qty: 50,     avgCost: 420.00, marketValue: 21_050.00, pnlUnrealized: 50.00,   pnlRealized: 0,      currency: 'USD', asOf: ASOF },
  { accountId: 'schwab-live-1', symbol: 'VT',     qty: 100,    avgCost: 115.00, marketValue: 11_900.00, pnlUnrealized: 400.00,  pnlRealized: 0,      currency: 'USD', asOf: ASOF },
  { accountId: 'schwab-live-1', symbol: 'EURUSD', qty: 50_000, avgCost: 1.08,   marketValue: 54_250.00, pnlUnrealized: 250.00,  pnlRealized: -80.00, currency: 'USD', asOf: ASOF },

  // schwab-paper-1 — US consumer + tech, USD
  { accountId: 'schwab-paper-1', symbol: 'KO',   qty: 200, avgCost: 60.00,  marketValue: 12_400.00, pnlUnrealized: 400.00,  pnlRealized: 0,      currency: 'USD', asOf: ASOF },
  { accountId: 'schwab-paper-1', symbol: 'PG',   qty: 80,  avgCost: 155.00, marketValue: 12_000.00, pnlUnrealized: -400.00, pnlRealized: 0,      currency: 'USD', asOf: ASOF },
  { accountId: 'schwab-paper-1', symbol: 'WMT',  qty: 100, avgCost: 60.00,  marketValue: 6_080.00,  pnlUnrealized: 80.00,   pnlRealized: 0,      currency: 'USD', asOf: ASOF },
  { accountId: 'schwab-paper-1', symbol: 'DIS',  qty: 50,  avgCost: 110.00, marketValue: 5_420.00,  pnlUnrealized: -80.00,  pnlRealized: 120.00, currency: 'USD', asOf: ASOF },
  { accountId: 'schwab-paper-1', symbol: 'NFLX', qty: 20,  avgCost: 600.00, marketValue: 12_800.00, pnlUnrealized: 800.00,  pnlRealized: 0,      currency: 'USD', asOf: ASOF },
];

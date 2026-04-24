import type { Order } from '../types';

const T0 = '2026-04-24T09:00:00Z';
const T1 = '2026-04-24T09:15:00Z';
const T2 = '2026-04-24T09:30:00Z';
const T3 = '2026-04-24T09:45:00Z';

// 20 orders across 6 accounts covering all 5 statuses:
// open, filled, partial, cancelled, rejected.
export const ORDERS: Order[] = [
  // open (4)
  { id: 'ord-001', accountId: 'ibkr-live-1',    symbol: 'AAPL',    side: 'buy',  qty: 100, filledQty: 0,   limitPx: 195.00, stopPx: null,  orderType: 'limit', status: 'open', createdAt: T0, updatedAt: T0 },
  { id: 'ord-002', accountId: 'futu-live-1',    symbol: '0700',    side: 'buy',  qty: 200, filledQty: 0,   limitPx: 315.00, stopPx: null,  orderType: 'limit', status: 'open', createdAt: T0, updatedAt: T0 },
  { id: 'ord-003', accountId: 'schwab-live-1',  symbol: 'JPM',     side: 'sell', qty: 50,  filledQty: 0,   limitPx: 205.00, stopPx: null,  orderType: 'limit', status: 'open', createdAt: T1, updatedAt: T1 },
  { id: 'ord-004', accountId: 'ibkr-paper-1',   symbol: 'BTC-USD', side: 'buy',  qty: 0.1, filledQty: 0,   limitPx: 64_000, stopPx: null,  orderType: 'limit', status: 'open', createdAt: T1, updatedAt: T1 },

  // filled (6)
  { id: 'ord-005', accountId: 'ibkr-live-1',    symbol: 'MSFT', side: 'buy',  qty: 50,  filledQty: 50,  limitPx: null,   stopPx: null, orderType: 'market', status: 'filled', createdAt: T0, updatedAt: T0 },
  { id: 'ord-006', accountId: 'ibkr-live-1',    symbol: 'NVDA', side: 'sell', qty: 10,  filledQty: 10,  limitPx: 890.00, stopPx: null, orderType: 'limit',  status: 'filled', createdAt: T1, updatedAt: T2 },
  { id: 'ord-007', accountId: 'futu-live-1',    symbol: '9988', side: 'buy',  qty: 500, filledQty: 500, limitPx: null,   stopPx: null, orderType: 'market', status: 'filled', createdAt: T2, updatedAt: T2 },
  { id: 'ord-008', accountId: 'schwab-live-1',  symbol: 'BAC',  side: 'buy',  qty: 200, filledQty: 200, limitPx: 35.50,  stopPx: null, orderType: 'limit',  status: 'filled', createdAt: T0, updatedAt: T1 },
  { id: 'ord-009', accountId: 'schwab-paper-1', symbol: 'KO',   side: 'buy',  qty: 50,  filledQty: 50,  limitPx: null,   stopPx: null, orderType: 'market', status: 'filled', createdAt: T2, updatedAt: T2 },
  { id: 'ord-010', accountId: 'futu-paper-1',   symbol: '7203', side: 'buy',  qty: 100, filledQty: 100, limitPx: null,   stopPx: null, orderType: 'market', status: 'filled', createdAt: T3, updatedAt: T3 },

  // partial (3)
  { id: 'ord-011', accountId: 'ibkr-live-1',   symbol: 'TSLA', side: 'buy',  qty: 100,  filledQty: 40,  limitPx: 245.00, stopPx: null, orderType: 'limit', status: 'partial', createdAt: T1, updatedAt: T2 },
  { id: 'ord-012', accountId: 'futu-live-1',   symbol: '0005', side: 'sell', qty: 1000, filledQty: 600, limitPx: 63.00,  stopPx: null, orderType: 'limit', status: 'partial', createdAt: T2, updatedAt: T3 },
  { id: 'ord-013', accountId: 'schwab-live-1', symbol: 'VT',   side: 'buy',  qty: 50,   filledQty: 20,  limitPx: 118.00, stopPx: null, orderType: 'limit', status: 'partial', createdAt: T2, updatedAt: T3 },

  // cancelled (4)
  { id: 'ord-014', accountId: 'ibkr-paper-1',  symbol: 'GOOGL',  side: 'sell', qty: 30,     filledQty: 0, limitPx: 175.00, stopPx: null,   orderType: 'limit',      status: 'cancelled', createdAt: T0, updatedAt: T1 },
  { id: 'ord-015', accountId: 'futu-paper-1',  symbol: '1299',   side: 'buy',  qty: 300,    filledQty: 0, limitPx: 69.00,  stopPx: null,   orderType: 'limit',      status: 'cancelled', createdAt: T1, updatedAt: T2 },
  { id: 'ord-016', accountId: 'schwab-live-1', symbol: 'EURUSD', side: 'buy',  qty: 10_000, filledQty: 0, limitPx: null,   stopPx: 1.07,   orderType: 'stop',       status: 'cancelled', createdAt: T2, updatedAt: T3 },
  { id: 'ord-017', accountId: 'ibkr-live-1',   symbol: 'SPY',    side: 'sell', qty: 10,     filledQty: 0, limitPx: 540.00, stopPx: 525.00, orderType: 'stop_limit', status: 'cancelled', createdAt: T1, updatedAt: T3 },

  // rejected (3)
  { id: 'ord-018', accountId: 'ibkr-paper-1',   symbol: 'AMZN', side: 'sell', qty: 500, filledQty: 0, limitPx: 200.00, stopPx: null,   orderType: 'limit',      status: 'rejected', createdAt: T0, updatedAt: T0 },
  { id: 'ord-019', accountId: 'futu-live-1',    symbol: '3690', side: 'buy',  qty: 50,  filledQty: 0, limitPx: null,   stopPx: null,   orderType: 'market',     status: 'rejected', createdAt: T1, updatedAt: T1 },
  { id: 'ord-020', accountId: 'schwab-paper-1', symbol: 'NFLX', side: 'buy',  qty: 10,  filledQty: 0, limitPx: 610.00, stopPx: 595.00, orderType: 'stop_limit', status: 'rejected', createdAt: T2, updatedAt: T2 },
];

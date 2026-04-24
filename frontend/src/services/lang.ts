export type Exchange =
  | 'NYSE' | 'NASDAQ' | 'AMEX' | 'ARCA' | 'CBOE' | 'CME'
  | 'SEHK' | 'TSE' | 'KRX' | 'TWSE' | 'SSE' | 'SZSE'
  | 'LSE' | 'EURONEXT' | 'XETRA'
  | 'FX' | 'CRYPTO'
  | (string & {});

const MAP: Record<string, string> = {
  NYSE: 'en', NASDAQ: 'en', AMEX: 'en', ARCA: 'en', CBOE: 'en', CME: 'en',
  SEHK: 'zh-HK', TSE: 'ja', KRX: 'ko', TWSE: 'zh-TW',
  SSE: 'zh-CN', SZSE: 'zh-CN',
  LSE: 'en', EURONEXT: 'en', XETRA: 'en',
  FX: 'en', CRYPTO: 'en',
};

export function langForMarket(exchange: Exchange): string {
  return MAP[exchange] ?? 'en';
}

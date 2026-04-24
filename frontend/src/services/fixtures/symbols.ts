import type { Symbol } from '../types';

export const SYMBOLS: Symbol[] = [
  // US large cap (10)
  { symbol: 'AAPL',   exchange: 'NASDAQ', description: 'Apple Inc.',                  assetClass: 'stock', langTag: 'en' },
  { symbol: 'MSFT',   exchange: 'NASDAQ', description: 'Microsoft Corp.',             assetClass: 'stock', langTag: 'en' },
  { symbol: 'GOOGL',  exchange: 'NASDAQ', description: 'Alphabet Inc. Class A',       assetClass: 'stock', langTag: 'en' },
  { symbol: 'AMZN',   exchange: 'NASDAQ', description: 'Amazon.com Inc.',             assetClass: 'stock', langTag: 'en' },
  { symbol: 'NVDA',   exchange: 'NASDAQ', description: 'NVIDIA Corp.',                assetClass: 'stock', langTag: 'en' },
  { symbol: 'TSLA',   exchange: 'NASDAQ', description: 'Tesla Inc.',                  assetClass: 'stock', langTag: 'en' },
  { symbol: 'META',   exchange: 'NASDAQ', description: 'Meta Platforms Inc.',         assetClass: 'stock', langTag: 'en' },
  { symbol: 'JPM',    exchange: 'NYSE',   description: 'JPMorgan Chase & Co.',        assetClass: 'stock', langTag: 'en' },
  { symbol: 'BAC',    exchange: 'NYSE',   description: 'Bank of America Corp.',       assetClass: 'stock', langTag: 'en' },
  { symbol: 'BRK.B',  exchange: 'NYSE',   description: 'Berkshire Hathaway Class B',  assetClass: 'stock', langTag: 'en' },

  // HK (6)
  { symbol: '0700', exchange: 'SEHK', description: '腾訊控股',      assetClass: 'stock', langTag: 'zh-HK' },
  { symbol: '9988', exchange: 'SEHK', description: '阿里巴巴-W',    assetClass: 'stock', langTag: 'zh-HK' },
  { symbol: '3690', exchange: 'SEHK', description: '美團-W',        assetClass: 'stock', langTag: 'zh-HK' },
  { symbol: '1299', exchange: 'SEHK', description: '友邦保險',      assetClass: 'stock', langTag: 'zh-HK' },
  { symbol: '0005', exchange: 'SEHK', description: '匯豐控股',      assetClass: 'stock', langTag: 'zh-HK' },
  { symbol: '0388', exchange: 'SEHK', description: '香港交易所',    assetClass: 'stock', langTag: 'zh-HK' },

  // JP (4)
  { symbol: '7203', exchange: 'TSE', description: 'トヨタ自動車',        assetClass: 'stock', langTag: 'ja' },
  { symbol: '6758', exchange: 'TSE', description: 'ソニーグループ',      assetClass: 'stock', langTag: 'ja' },
  { symbol: '7974', exchange: 'TSE', description: '任天堂',              assetClass: 'stock', langTag: 'ja' },
  { symbol: '6501', exchange: 'TSE', description: '日立製作所',          assetClass: 'stock', langTag: 'ja' },

  // KR (3)
  { symbol: '005930', exchange: 'KRX', description: '삼성전자',   assetClass: 'stock', langTag: 'ko' },
  { symbol: '000660', exchange: 'KRX', description: 'SK하이닉스', assetClass: 'stock', langTag: 'ko' },
  { symbol: '035420', exchange: 'KRX', description: 'NAVER',      assetClass: 'stock', langTag: 'ko' },

  // TW (2)
  { symbol: '2330', exchange: 'TWSE', description: '台積電', assetClass: 'stock', langTag: 'zh-TW' },
  { symbol: '2454', exchange: 'TWSE', description: '聯發科', assetClass: 'stock', langTag: 'zh-TW' },

  // FX (5)
  { symbol: 'EURUSD', exchange: 'FX', description: 'Euro / US Dollar',              assetClass: 'forex', langTag: 'en' },
  { symbol: 'USDJPY', exchange: 'FX', description: 'US Dollar / Japanese Yen',      assetClass: 'forex', langTag: 'en' },
  { symbol: 'GBPUSD', exchange: 'FX', description: 'British Pound / US Dollar',     assetClass: 'forex', langTag: 'en' },
  { symbol: 'USDHKD', exchange: 'FX', description: 'US Dollar / Hong Kong Dollar',  assetClass: 'forex', langTag: 'en' },
  { symbol: 'AUDUSD', exchange: 'FX', description: 'Australian Dollar / US Dollar', assetClass: 'forex', langTag: 'en' },

  // Crypto (3)
  { symbol: 'BTC-USD', exchange: 'CRYPTO', description: 'Bitcoin / US Dollar',  assetClass: 'crypto', langTag: 'en' },
  { symbol: 'ETH-USD', exchange: 'CRYPTO', description: 'Ethereum / US Dollar', assetClass: 'crypto', langTag: 'en' },
  { symbol: 'SOL-USD', exchange: 'CRYPTO', description: 'Solana / US Dollar',   assetClass: 'crypto', langTag: 'en' },

  // ETFs (3)
  { symbol: 'SPY', exchange: 'NYSE',   description: 'SPDR S&P 500 ETF Trust',         assetClass: 'etf', langTag: 'en' },
  { symbol: 'QQQ', exchange: 'NASDAQ', description: 'Invesco QQQ Trust',              assetClass: 'etf', langTag: 'en' },
  { symbol: 'VT',  exchange: 'NYSE',   description: 'Vanguard Total World Stock ETF', assetClass: 'etf', langTag: 'en' },

  // US stocks (14 more)
  { symbol: 'JNJ',  exchange: 'NYSE',   description: 'Johnson & Johnson',      assetClass: 'stock', langTag: 'en' },
  { symbol: 'PG',   exchange: 'NYSE',   description: 'Procter & Gamble Co.',   assetClass: 'stock', langTag: 'en' },
  { symbol: 'KO',   exchange: 'NYSE',   description: 'Coca-Cola Co.',          assetClass: 'stock', langTag: 'en' },
  { symbol: 'PEP',  exchange: 'NASDAQ', description: 'PepsiCo Inc.',           assetClass: 'stock', langTag: 'en' },
  { symbol: 'WMT',  exchange: 'NYSE',   description: 'Walmart Inc.',           assetClass: 'stock', langTag: 'en' },
  { symbol: 'TGT',  exchange: 'NYSE',   description: 'Target Corp.',           assetClass: 'stock', langTag: 'en' },
  { symbol: 'HD',   exchange: 'NYSE',   description: 'Home Depot Inc.',        assetClass: 'stock', langTag: 'en' },
  { symbol: 'LOW',  exchange: 'NYSE',   description: "Lowe's Companies Inc.",  assetClass: 'stock', langTag: 'en' },
  { symbol: 'DIS',  exchange: 'NYSE',   description: 'Walt Disney Co.',        assetClass: 'stock', langTag: 'en' },
  { symbol: 'NFLX', exchange: 'NASDAQ', description: 'Netflix Inc.',           assetClass: 'stock', langTag: 'en' },
  { symbol: 'CRM',  exchange: 'NYSE',   description: 'Salesforce Inc.',        assetClass: 'stock', langTag: 'en' },
  { symbol: 'ADBE', exchange: 'NASDAQ', description: 'Adobe Inc.',             assetClass: 'stock', langTag: 'en' },
  { symbol: 'ORCL', exchange: 'NYSE',   description: 'Oracle Corp.',           assetClass: 'stock', langTag: 'en' },
  { symbol: 'CSCO', exchange: 'NASDAQ', description: 'Cisco Systems Inc.',     assetClass: 'stock', langTag: 'en' },
];

export const STRESS_SYMBOLS: Symbol[] = (() => {
  const list: Symbol[] = [];
  for (let i = 1; i <= 500; i++) {
    const code = String(i).padStart(3, '0');
    list.push({
      symbol: `SYM${code}`,
      exchange: 'NYSE',
      description: `Stress test symbol ${code}`,
      assetClass: 'stock',
      langTag: 'en',
    });
  }
  return list;
})();

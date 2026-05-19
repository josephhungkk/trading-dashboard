import { useState } from 'react';
import type { BacktestSubmitConfig } from '../../../services/backtests/types';
import { uploadBars } from '../../../services/backtests/api';

interface Props {
  botId: string;
  onSubmit: (config: BacktestSubmitConfig) => void;
}

export function BacktestConfigForm({ botId, onSubmit }: Props) {
  const [canonicalId, setCanonicalId] = useState('');
  const [timeframe, setTimeframe] = useState('1d');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [barsSource, setBarsSource] = useState<'db' | 'backfill' | 'csv'>('db');
  const [slippageMode, setSlippageMode] = useState<'bps' | 'atr'>('bps');
  const [slippageBps, setSlippageBps] = useState('5');
  const [slippageAtr, setSlippageAtr] = useState('0.1');
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadDone, setUploadDone] = useState(false);

  const showCorporateWarning =
    canonicalId &&
    startDate &&
    endDate &&
    new Date(endDate).getTime() - new Date(startDate).getTime() > 180 * 24 * 60 * 60 * 1000;

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError(null);
    setUploadDone(false);
    try {
      await uploadBars(botId, file, canonicalId, timeframe);
      setUploadDone(true);
    } catch (err) {
      setUploadError(String(err));
      setUploadDone(false);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const bps = parseFloat(slippageBps);
    const atr = parseFloat(slippageAtr);
    if (slippageMode === 'bps' && isNaN(bps)) return;
    if (slippageMode === 'atr' && isNaN(atr)) return;
    onSubmit({
      canonical_id: canonicalId,
      timeframe,
      start_date: startDate,
      end_date: endDate,
      slippage_bps: slippageMode === 'bps' ? bps : null,
      slippage_atr_pct: slippageMode === 'atr' ? atr : null,
      bars_source: barsSource,
    });
  }

  const submitDisabled = barsSource === 'csv' && !uploadDone;

  return (
    <form onSubmit={handleSubmit} aria-label="Backtest configuration">
      <label htmlFor="canonical_id">Instrument</label>
      <input
        id="canonical_id"
        value={canonicalId}
        onChange={(e) => setCanonicalId(e.target.value)}
        required
      />

      <label htmlFor="timeframe">Timeframe</label>
      <select id="timeframe" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
        {['1m', '5m', '15m', '1h', '1d'].map((tf) => (
          <option key={tf}>{tf}</option>
        ))}
      </select>

      <label htmlFor="start_date">Start date</label>
      <input
        id="start_date"
        type="date"
        value={startDate}
        onChange={(e) => setStartDate(e.target.value)}
        required
      />

      <label htmlFor="end_date">End date</label>
      <input
        id="end_date"
        type="date"
        value={endDate}
        onChange={(e) => setEndDate(e.target.value)}
        required
      />

      {showCorporateWarning && (
        <p role="alert" style={{ color: 'orange' }}>
          This range may span splits or dividends. Results will be misleading unless you upload
          split-adjusted bars.
        </p>
      )}

      <fieldset>
        <legend>Bars source</legend>
        {(['db', 'backfill', 'csv'] as const).map((src) => (
          <label key={src}>
            <input
              type="radio"
              name="bars_source"
              value={src}
              checked={barsSource === src}
              onChange={() => {
                setBarsSource(src);
                setUploadDone(false);
              }}
            />
            {src}
          </label>
        ))}
      </fieldset>

      {barsSource === 'csv' && (
        <div>
          <label htmlFor="csv_upload">Upload OHLCV CSV</label>
          <input id="csv_upload" type="file" accept=".csv" onChange={handleUpload} />
          {uploadError && (
            <p role="alert" style={{ color: 'red' }}>
              {uploadError}
            </p>
          )}
          {uploadDone && <p>Upload successful</p>}
        </div>
      )}

      <fieldset>
        <legend>Slippage</legend>
        <label>
          <input
            type="radio"
            name="slip_mode"
            checked={slippageMode === 'bps'}
            onChange={() => setSlippageMode('bps')}
          />
          Fixed bps
          <input
            type="number"
            value={slippageBps}
            onChange={(e) => setSlippageBps(e.target.value)}
            disabled={slippageMode !== 'bps'}
            min="0"
            step="0.1"
          />
        </label>
        <label>
          <input
            type="radio"
            name="slip_mode"
            checked={slippageMode === 'atr'}
            onChange={() => setSlippageMode('atr')}
          />
          % of ATR
          <input
            type="number"
            value={slippageAtr}
            onChange={(e) => setSlippageAtr(e.target.value)}
            disabled={slippageMode !== 'atr'}
            min="0"
            step="0.01"
          />
        </label>
      </fieldset>

      <button type="submit" disabled={submitDisabled}>
        Run Backtest
      </button>
    </form>
  );
}

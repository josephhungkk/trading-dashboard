import * as React from 'react';
import type { FxPair, FxQuote } from '@/services/forex/types';
import { requestQuote, acceptQuote, cancelQuote } from '@/services/forex/api';
import { FxQuoteDisplay } from './FxQuoteDisplay';

interface Props {
  accountId: string;
  pair: FxPair;
  onSuccess?: (orderId: string) => void;
}

export function FxTicketSection({ accountId, pair, onSuccess }: Props) {
  const [notional, setNotional] = React.useState('');
  const [notionalCurrency, setNotionalCurrency] = React.useState<'base' | 'quote'>('base');
  const [activeQuote, setActiveQuote] = React.useState<FxQuote | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [successMsg, setSuccessMsg] = React.useState<string | null>(null);

  const handleGetQuote = async () => {
    setLoading(true);
    setError(null);
    try {
      const q = await requestQuote({
        pair: pair.canonical_id.replace('forex:', '').toUpperCase(),
        notional,
        notional_currency: notionalCurrency,
        account_id: accountId,
      });
      setActiveQuote(q);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to get quote');
    } finally {
      setLoading(false);
    }
  };

  const handleAccept = async (side: 'BUY' | 'SELL', qty: string) => {
    if (!activeQuote) return;
    setLoading(true);
    setError(null);
    try {
      const result = await acceptQuote(activeQuote.broker_quote_id, {
        account_id: accountId,
        side,
        qty,
      });
      setSuccessMsg(`Order placed: ${result.order_id} @ ${result.fill_price}`);
      setActiveQuote(null);
      onSuccess?.(result.order_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to accept quote');
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!activeQuote) return;
    await cancelQuote(activeQuote.broker_quote_id, accountId);
    setActiveQuote(null);
  };

  return (
    <div className='space-y-3 p-3'>
      <div className='flex items-center gap-2'>
        <span className='font-semibold'>{pair.base_currency}/{pair.quote_currency}</span>
        <span className='text-xs text-fg-muted'>pip {pair.pip_size}</span>
      </div>
      {successMsg && (
        <p className='rounded bg-green-50 px-3 py-2 text-xs text-green-700'>{successMsg}</p>
      )}
      {error && (
        <p className='rounded bg-red-50 px-3 py-2 text-xs text-red-700'>{error}</p>
      )}
      {activeQuote ? (
        <FxQuoteDisplay
          quote={activeQuote}
          onAccept={handleAccept}
          onCancel={handleCancel}
        />
      ) : (
        <div className='flex gap-2'>
          <input
            type='number'
            placeholder='Notional'
            value={notional}
            onChange={(e) => setNotional(e.target.value)}
            className='flex-1 rounded border px-2 py-1 text-sm'
          />
          <select
            value={notionalCurrency}
            onChange={(e) => setNotionalCurrency(e.target.value as 'base' | 'quote')}
            className='rounded border px-2 py-1 text-sm'
          >
            <option value='base'>{pair.base_currency}</option>
            <option value='quote'>{pair.quote_currency}</option>
          </select>
          <button
            type='button'
            disabled={!notional || loading}
            onClick={handleGetQuote}
            className='rounded bg-blue-600 px-3 py-1.5 text-xs text-white disabled:opacity-50'
          >
            {loading ? 'Loading...' : 'Get Quote'}
          </button>
        </div>
      )}
    </div>
  );
}

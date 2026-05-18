import * as React from 'react';
import type { FxQuote } from '@/services/forex/types';

interface Props {
  quote: FxQuote;
  onAccept: (side: 'BUY' | 'SELL', qty: string) => void;
  onCancel: () => void;
}

export function FxQuoteDisplay({ quote, onAccept, onCancel }: Props) {
  const [qty, setQty] = React.useState('');
  const expiresAt = React.useMemo(() => new Date(quote.expires_at).getTime(), [quote.expires_at]);
  const [ttl, setTtl] = React.useState(() =>
    Math.max(0, Math.round((expiresAt - Date.now()) / 1000)),
  );

  React.useEffect(() => {
    const id = window.setInterval(() => {
      setTtl(Math.max(0, Math.round((expiresAt - Date.now()) / 1000)));
    }, 1000);
    return () => window.clearInterval(id);
  }, [expiresAt]);

  const expired = ttl <= 0 || quote.status === 'expired';
  const expiring = !expired && ttl < 5;

  return (
    <div className='rounded border border-border p-3 text-sm'>
      <div className='mb-2 flex items-center justify-between'>
        <span className='font-mono text-base'>
          Bid <strong>{quote.bid}</strong> / Ask <strong>{quote.ask}</strong>
        </span>
        {expiring && (
          <span className='rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800'>Expiring</span>
        )}
        {expired && (
          <span className='rounded bg-red-100 px-2 py-0.5 text-xs text-red-800'>Expired</span>
        )}
        {!expired && !expiring && (
          <span className='text-xs text-fg-muted'>{ttl}s</span>
        )}
      </div>
      <input
        type='number'
        placeholder='Quantity'
        value={qty}
        onChange={(e) => setQty(e.target.value)}
        disabled={expired}
        className='mb-2 w-full rounded border px-2 py-1 text-sm'
      />
      {expired ? (
        <p className='text-xs text-red-600'>Quote expired — refresh</p>
      ) : (
        <div className='flex gap-2'>
          <button
            type='button'
            disabled={!qty}
            onClick={() => onAccept('BUY', qty)}
            className='flex-1 rounded bg-green-600 px-3 py-1.5 text-xs text-white disabled:opacity-50'
          >
            Buy at {quote.ask}
          </button>
          <button
            type='button'
            disabled={!qty}
            onClick={() => onAccept('SELL', qty)}
            className='flex-1 rounded bg-red-600 px-3 py-1.5 text-xs text-white disabled:opacity-50'
          >
            Sell at {quote.bid}
          </button>
        </div>
      )}
      <button
        type='button'
        onClick={onCancel}
        className='mt-2 w-full rounded border px-3 py-1 text-xs text-fg-muted'
      >
        Cancel
      </button>
    </div>
  );
}

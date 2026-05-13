import * as React from 'react';

import { Badge } from '@/components/primitives/Badge';
import { useTradeContext } from '@/services/ai/useTradeContext';

export interface TradeTicketAiSectionProps {
  symbol: string;
  side: 'BUY' | 'SELL';
  qty: number;
}

export function TradeTicketAiSection({
  symbol,
  side,
  qty,
}: TradeTicketAiSectionProps): React.JSX.Element | null {
  const { context, error, loading } = useTradeContext({ symbol, side, qty });

  if (symbol.trim() === '') return null;

  return (
    <details
      open
      className="rounded-md border border-border p-3"
      data-testid="ai-context-section"
    >
      <summary className="cursor-pointer text-sm font-medium" aria-label="Section: AI context">
        AI context
      </summary>
      <div className="mt-3 space-y-3">
        {loading ? (
          <p
            aria-label="loading"
            className="text-sm text-muted-foreground"
          >
            Loading AI context...
          </p>
        ) : null}

        {!loading && context !== null ? (
          <>
            <p className="text-sm text-fg-muted">{context.summary}</p>
            {context.recent_signals.length > 0 ? (
              <ul className="list-disc space-y-1 pl-5 text-sm text-fg-muted">
                {context.recent_signals.map((signal) => (
                  <li key={signal}>{signal}</li>
                ))}
              </ul>
            ) : null}
            {context.risk_flags.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {context.risk_flags.map((flag) => (
                  <Badge key={flag} variant="warn">
                    {flag}
                  </Badge>
                ))}
              </div>
            ) : null}
          </>
        ) : null}

        {!loading && context === null && error !== null ? (
          <p className="text-xs text-muted-foreground">
            AI context unavailable
          </p>
        ) : null}
      </div>
    </details>
  );
}

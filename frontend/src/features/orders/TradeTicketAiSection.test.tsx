import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useTradeContext } from '@/services/ai/useTradeContext';
import type { UseTradeContextReturn } from '@/services/ai/useTradeContext';

import { TradeTicketAiSection } from './TradeTicketAiSection';

vi.mock('@/services/ai/useTradeContext', () => ({
  useTradeContext: vi.fn(),
}));

const useTradeContextMock = vi.mocked(useTradeContext);

function mockTradeContext(returnValue: UseTradeContextReturn): void {
  useTradeContextMock.mockReturnValue(returnValue);
}

describe('TradeTicketAiSection', () => {
  it('renders summary + signals + risk flags when context resolves', () => {
    mockTradeContext({
      context: {
        summary: 'AAPL momentum is constructive into the open.',
        recent_signals: [
          'Price reclaimed the 20-day moving average.',
          'Options flow skewed bullish yesterday.',
        ],
        risk_flags: ['earnings week', 'wide spread'],
      },
      loading: false,
      error: null,
    });

    render(<TradeTicketAiSection symbol="AAPL" side="BUY" qty={10} />);

    expect(screen.getByText('AAPL momentum is constructive into the open.')).toBeInTheDocument();
    expect(screen.getByText('Price reclaimed the 20-day moving average.')).toBeInTheDocument();
    expect(screen.getByText('Options flow skewed bullish yesterday.')).toBeInTheDocument();
    expect(screen.getByText('earnings week')).toHaveClass('bg-warn/15');
    expect(screen.getByText('wide spread')).toHaveClass('bg-warn/15');
  });

  it('renders unavailable message when useTradeContext errors', () => {
    mockTradeContext({
      context: null,
      loading: false,
      error: 'unavailable',
    });

    render(<TradeTicketAiSection symbol="AAPL" side="SELL" qty={5} />);

    expect(screen.getByText('AI context unavailable')).toBeInTheDocument();
    expect(screen.queryByLabelText('loading')).not.toBeInTheDocument();
  });
});

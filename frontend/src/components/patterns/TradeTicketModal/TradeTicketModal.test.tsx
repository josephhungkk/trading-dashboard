import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TradeTicketModal } from './TradeTicketModal';

// ---------------------------------------------------------------------------
// Fetch mock helpers
// ---------------------------------------------------------------------------

interface FetchLike {
  ok: boolean;
  status: number;
  headers: { get: () => null };
  json: () => Promise<unknown>;
  text: () => Promise<string>;
}

function makeFetchResponse(body: unknown, status = 200): FetchLike {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

const defaultProps = {
  accountId: 'acct-1',
  conid: '265598',
  onClose: vi.fn(),
};

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.spyOn(global, 'fetch').mockResolvedValue(
    makeFetchResponse({ nonce: 'nonce-1', cap_status: 'ok', warnings: [] }) as unknown as Response,
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Existing smoke tests
// ---------------------------------------------------------------------------

describe('TradeTicketModal — existing behavior', () => {
  it('renders dialog with accessible role', () => {
    render(<TradeTicketModal {...defaultProps} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('cancel button calls onClose', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<TradeTicketModal {...defaultProps} onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Place Order button present for mode=place', () => {
    render(<TradeTicketModal {...defaultProps} />);
    expect(screen.getByTestId('trade-ticket-submit')).toHaveTextContent('Place Order');
  });
});

// ---------------------------------------------------------------------------
// New tests — mode prop (6 required)
// ---------------------------------------------------------------------------

describe('TradeTicketModal — mode prop (6 new tests)', () => {
  /**
   * Test 1: mode=modify pre-fills from order, disables conid/side/order_type
   */
  it('mode=modify pre-fills from order, disables conid/side/order_type', () => {
    const order = {
      conid: '265598',
      side: 'BUY' as const,
      order_type: 'LIMIT' as const,
      qty: 5,
      limit_price: 182.5,
    };
    render(
      <TradeTicketModal
        {...defaultProps}
        mode="modify"
        orderId="order-abc"
        initialOrder={order}
      />,
    );

    // conid input should be disabled
    expect(screen.getByTestId('trade-ticket-conid')).toBeDisabled();

    // order_type select should be disabled
    expect(screen.getByLabelText('Order type')).toBeDisabled();

    // BUY / SELL side buttons should be disabled
    expect(screen.getByRole('button', { name: 'BUY' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'SELL' })).toBeDisabled();

    // qty and limit_price should be pre-filled
    expect(screen.getByLabelText('Qty')).toHaveValue('5');
    // limit price field appears because order_type=LIMIT
    expect(screen.getByLabelText('Limit price')).toHaveValue('182.5');
  });

  /**
   * Test 2: mode=modify submits to PUT /api/orders/{id}
   */
  it('mode=modify submits to PUT /api/orders/{id}', async () => {
    const fetchMock = vi.mocked(fetch);
    // Override with a resolved mock for the submit call
    fetchMock.mockResolvedValue(
      makeFetchResponse({ id: 'order-123' }) as unknown as Response,
    );

    const order = {
      conid: '265598',
      side: 'BUY' as const,
      order_type: 'MARKET' as const,
      qty: 10,
    };
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <TradeTicketModal
        {...defaultProps}
        mode="modify"
        orderId="order-123"
        initialOrder={order}
        onClose={onClose}
      />,
    );

    // qty is pre-filled (10); the submit button is enabled for modify mode
    const submitBtn = screen.getByTestId('trade-ticket-submit');
    expect(submitBtn).toHaveTextContent('Modify Order');

    await user.click(submitBtn);

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        (call) => (call[1] as RequestInit | undefined)?.method === 'PUT',
      );
      expect(putCall).toBeDefined();
      expect(putCall?.[0]).toBe('/api/orders/order-123');
    });
  });

  /**
   * Test 3: mode=bracket shows stop_price and target_price inputs
   */
  it('mode=bracket shows stop_price and target_price inputs', () => {
    render(<TradeTicketModal {...defaultProps} mode="bracket" />);

    expect(screen.getByTestId('trade-ticket-bracket-stop')).toBeInTheDocument();
    expect(screen.getByTestId('trade-ticket-bracket-target')).toBeInTheDocument();
    expect(screen.getByLabelText('Bracket stop price')).toBeInTheDocument();
    expect(screen.getByLabelText('Bracket target price')).toBeInTheDocument();
  });

  /**
   * Test 4: mode=bracket submits to POST /api/orders/bracket
   */
  it('mode=bracket submits to POST /api/orders/bracket', async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(
      makeFetchResponse({ id: 'bracket-order-1' }) as unknown as Response,
    );

    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<TradeTicketModal {...defaultProps} mode="bracket" onClose={onClose} />);

    // Fill qty
    await user.clear(screen.getByLabelText('Qty'));
    await user.type(screen.getByLabelText('Qty'), '1');

    // Set LIMIT order type to get entry price field
    fireEvent.change(screen.getByLabelText('Order type'), { target: { value: 'LIMIT' } });

    // Fill limit (entry) price
    await user.clear(screen.getByLabelText('Limit price'));
    await user.type(screen.getByLabelText('Limit price'), '100');

    // BUY: stop < 100, target > 100
    await user.clear(screen.getByLabelText('Bracket stop price'));
    await user.type(screen.getByLabelText('Bracket stop price'), '95');
    await user.clear(screen.getByLabelText('Bracket target price'));
    await user.type(screen.getByLabelText('Bracket target price'), '110');

    // Clear tracked calls so far (debounced previews) and set up submit response
    fetchMock.mockClear();
    fetchMock.mockResolvedValue(
      makeFetchResponse({ id: 'bracket-order-1' }) as unknown as Response,
    );

    await user.click(screen.getByTestId('trade-ticket-submit'));

    await waitFor(() => {
      const bracketCall = fetchMock.mock.calls.find(
        (call) => call[0] === '/api/orders/bracket',
      );
      expect(bracketCall).toBeDefined();
      expect((bracketCall?.[1] as RequestInit | undefined)?.method).toBe('POST');
    });
  });

  /**
   * Test 5: mode=bracket rejects BUY with stop_price >= entry_price
   */
  it('mode=bracket rejects BUY with stop_price >= entry_price', async () => {
    const user = userEvent.setup();
    render(<TradeTicketModal {...defaultProps} mode="bracket" />);

    // BUY is default side
    await user.clear(screen.getByLabelText('Qty'));
    await user.type(screen.getByLabelText('Qty'), '1');

    // Set LIMIT to get the entry price field
    fireEvent.change(screen.getByLabelText('Order type'), { target: { value: 'LIMIT' } });
    await user.clear(screen.getByLabelText('Limit price'));
    await user.type(screen.getByLabelText('Limit price'), '50');

    // Invalid: BUY stop >= entry (50)
    await user.clear(screen.getByLabelText('Bracket stop price'));
    await user.type(screen.getByLabelText('Bracket stop price'), '55');

    await user.click(screen.getByTestId('trade-ticket-submit'));

    expect(await screen.findByText('bracket_invalid_prices')).toBeInTheDocument();
  });

  /**
   * Test 6: preview re-fires on every keystroke (debounced 300ms)
   *
   * Uses fake timers to verify the debounce: 3 rapid changes → only 1 fetch
   * after the timer fires.
   */
  it('preview re-fires on every keystroke (debounced 300ms)', async () => {
    vi.useFakeTimers();

    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(
      makeFetchResponse({ nonce: 'n', cap_status: 'ok', warnings: [] }) as unknown as Response,
    );

    const { unmount } = render(<TradeTicketModal {...defaultProps} />);
    const qtyInput = screen.getByLabelText('Qty');

    // Fire 3 rapid change events — each arms the debounce timer, cancelling the previous
    act(() => { fireEvent.change(qtyInput, { target: { value: '1' } }); });
    act(() => { fireEvent.change(qtyInput, { target: { value: '12' } }); });
    act(() => { fireEvent.change(qtyInput, { target: { value: '123' } }); });

    // None of the debounce timers have fired yet — no preview calls
    const callsBefore = fetchMock.mock.calls.filter(
      (c) => typeof c[0] === 'string' && (c[0] as string).includes('/api/orders/preview'),
    ).length;
    expect(callsBefore).toBe(0);

    // Advance past the 300 ms debounce window
    await act(async () => { vi.advanceTimersByTime(350); });

    const callsAfter = fetchMock.mock.calls.filter(
      (c) => typeof c[0] === 'string' && (c[0] as string).includes('/api/orders/preview'),
    ).length;

    // Exactly 1 preview fetch, not 3
    expect(callsAfter).toBe(1);

    unmount();
    vi.useRealTimers();
  });
});

import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithQuery } from '@/test-utils/render-with-query';
import { TradeTicketModal } from './TradeTicketModal';
import { tradeTicketStore } from './use-trade-ticket';
import { useOrdersStore } from '@/stores/global/orders';
import type { DecimalString, OrderResponse, PreviewRequest, PreviewResponse } from '@/services/types';
import { previewOrder, placeOrder } from '@/services/orders';
import type { BrokerCapabilitiesResponse } from '@/services/capabilities/types';

vi.mock('@/services/orders', async () => {
  const actual = await vi.importActual<typeof import('@/services/orders')>('@/services/orders');
  return {
    ...actual,
    previewOrder: vi.fn(),
    placeOrder: vi.fn(),
  };
});

const previewMock = vi.mocked(previewOrder);
const placeMock = vi.mocked(placeOrder);

function decimal(value: string): DecimalString {
  return value as DecimalString;
}

function makePreview(overrides: Partial<PreviewResponse> = {}): PreviewResponse {
  return {
    nonce: 'nonce-1',
    notional: decimal('100'),
    notional_currency: 'USD',
    notional_filled_today: decimal('0'),
    daily_notional_cap: decimal('10000'),
    max_notional_per_order: decimal('5000'),
    cap_status: 'ok',
    daily_cap_status: 'ok',
    position_sanity: {
      current_qty: decimal('0'),
      new_qty_after_fill: decimal('1'),
      sanity_multiplier: decimal('1'),
      status: 'ok',
      requires_extra_attestation: false,
    },
    contract_summary: { conid: 265598, description: 'AAPL' },
    warnings: [],
    ...overrides,
  };
}

function makeOrder(): OrderResponse {
  return {
    id: 'ord-1',
    account_id: 'acct-1',
    broker_order_id: 'broker-1',
    symbol: 'AAPL',
    side: 'BUY',
    order_type: 'MARKET',
    tif: 'DAY',
    qty: decimal('1'),
    limit_price: null,
    stop_price: null,
    status: 'submitted',
    filled_qty: decimal('0'),
    avg_fill_price: null,
    notional: decimal('100'),
    created_at: '2026-04-27T00:00:00Z',
    updated_at: '2026-04-27T00:00:00Z',
    last_event_at: '2026-04-27T00:00:00Z',
    submission_state: 'submitted',
    events: [],
  };
}

function openTicket(conid = '265598', symbol = 'AAPL'): void {
  act(() => {
    tradeTicketStore.getState().open({ accountId: 'acct-1', conid, symbol });
  });
}

function renderOpen(brokerId?: string): void {
  openTicket();
  renderWithQuery(<TradeTicketModal {...(brokerId !== undefined ? { brokerId } : {})} />);
}

async function fillMarket(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const qty = screen.getByLabelText('Qty');
  await user.clear(qty);
  await user.type(qty, '1');
}

async function previewForm(user: ReturnType<typeof userEvent.setup>, response = makePreview()): Promise<void> {
  previewMock.mockResolvedValueOnce(response);
  await fillMarket(user);
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Confirm' });
}

function maintenanceError(seconds: string): Error & { headers: Headers } {
  const error = new Error('maintenance') as Error & { headers: Headers };
  error.headers = new Headers({ 'Retry-After': seconds });
  return error;
}

function schwabCapabilities(): BrokerCapabilitiesResponse {
  const supportedOrderTypes = ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT'];
  const supportedTifs = ['DAY', 'GTC', 'IOC', 'FOK'];
  return {
    broker_id: 'schwab',
    order_types: [
      ...supportedOrderTypes.map((code, index) => ({ code, label: code, description: code, sort_order: index })),
      { code: 'TRAIL', label: 'TRAIL', description: 'TRAIL', sort_order: supportedOrderTypes.length },
    ],
    time_in_force: supportedTifs.map((code, index) => ({
      code,
      label: code,
      description: code,
      requires_expiry: false,
      sort_order: index,
    })),
    combos: [
      ...supportedOrderTypes.flatMap((order_type) => (
        supportedTifs.map((time_in_force) => ({ broker_id: 'schwab', asset_class: 'STOCK', order_type, time_in_force, supported: true, notes: '' }))
      )),
      { broker_id: 'schwab', asset_class: 'STOCK', order_type: 'TRAIL', time_in_force: 'DAY', supported: false, notes: 'Not supported for this broker' },
      { broker_id: 'schwab', asset_class: 'STOCK', order_type: 'TRAIL', time_in_force: 'GTC', supported: false, notes: 'Not supported for this broker' },
      { broker_id: 'schwab', asset_class: 'STOCK', order_type: 'TRAIL', time_in_force: 'IOC', supported: false, notes: 'Not supported for this broker' },
      { broker_id: 'schwab', asset_class: 'STOCK', order_type: 'TRAIL', time_in_force: 'FOK', supported: false, notes: 'Not supported for this broker' },
    ],
  };
}

function mockCapabilitiesFetch(response: Promise<Response>): void {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url;
    if (url === '/api/brokers/schwab/capabilities') return response;
    return Promise.reject(new Error(`unexpected fetch ${url}`));
  });
}

function capabilitiesResponse(body: BrokerCapabilitiesResponse, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

describe('TradeTicketModal', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    previewMock.mockReset();
    placeMock.mockReset();
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('11111111-1111-4111-8111-111111111111');
    tradeTicketStore.getState().close();
    useOrdersStore.getState().clear();
  });

  afterEach(() => {
    tradeTicketStore.getState().close();
    vi.restoreAllMocks();
  });

  it('form_validation_market_blocks_limit_price_input', () => {
    renderOpen();
    expect(screen.queryByLabelText('Limit price')).not.toBeInTheDocument();
  });

  it('form_validation_limit_requires_limit_price', async () => {
    const user = userEvent.setup();
    renderOpen();
    fireEvent.change(screen.getByLabelText('Order type'), { target: { value: 'LIMIT' } });
    await fillMarket(user);
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
    expect(previewMock).not.toHaveBeenCalled();
  });

  it('preview_button_calls_orderService_preview', async () => {
    const user = userEvent.setup();
    renderOpen();
    await previewForm(user);
    expect(previewMock).toHaveBeenCalledWith({
      account_id: 'acct-1',
      conid: '265598',
      side: 'BUY',
      order_type: 'MARKET',
      tif: 'DAY',
      qty: decimal('1'),
      limit_price: null,
      stop_price: null,
    } satisfies PreviewRequest);
  });

  it('cap_exceeded_disables_confirm', async () => {
    const user = userEvent.setup();
    renderOpen();
    await previewForm(user, makePreview({ cap_status: 'exceeded' }));
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
  });

  it('position_sanity_extreme_requires_extra_attestation', async () => {
    const user = userEvent.setup();
    renderOpen();
    await previewForm(user, makePreview({
      position_sanity: {
        current_qty: decimal('0'),
        new_qty_after_fill: decimal('100000'),
        sanity_multiplier: decimal('100'),
        status: 'extreme',
        requires_extra_attestation: true,
      },
    }));
    const confirm = screen.getByRole('button', { name: 'Confirm' });
    expect(confirm).toBeDisabled();
    await user.click(screen.getByLabelText('I understand this is an extreme position size'));
    expect(confirm).toBeEnabled();
  });

  it('confirm_button_uses_modal_client_order_id', async () => {
    const user = userEvent.setup();
    renderOpen();
    await previewForm(user);
    placeMock.mockResolvedValueOnce({ order: makeOrder(), submissionState: 'submitted' });
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(placeMock).toHaveBeenCalledWith(expect.objectContaining({ account_id: 'acct-1' }), 'nonce-1', '11111111-1111-4111-8111-111111111111');
  });

  it('idempotency_on_double_click', async () => {
    const user = userEvent.setup();
    renderOpen();
    await previewForm(user);
    placeMock.mockImplementation(() => new Promise(() => { /* pending */ }));
    await user.dblClick(screen.getByRole('button', { name: 'Confirm' }));
    expect(placeMock).toHaveBeenCalledTimes(1);
  });

  it('503_maintenance_shows_retry_after_countdown', async () => {
    const user = userEvent.setup();
    renderOpen();
    await previewForm(user);
    placeMock.mockRejectedValueOnce(maintenanceError('7'));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText('Broker maintenance - retrying in 7s')).toBeInTheDocument();
  });

  it('mobile_breakpoint_full_screen', () => {
    window.innerWidth = 500;
    renderOpen();
    expect(screen.getByRole('dialog')).toHaveClass('fixed', 'inset-0');
  });

  it('confirm_retry_after_network_error_uses_same_client_order_id', async () => {
    const user = userEvent.setup();
    renderOpen();
    await previewForm(user);
    placeMock
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({ order: makeOrder(), submissionState: 'submitted' });
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await screen.findByText('Trading suspended by kill-switch');
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(placeMock).toHaveBeenCalledTimes(2);
    expect(placeMock.mock.calls[0]?.[2]).toBe('11111111-1111-4111-8111-111111111111');
    expect(placeMock.mock.calls[1]?.[2]).toBe('11111111-1111-4111-8111-111111111111');
  });

  it('escape_closes_modal_returns_focus_to_trigger', async () => {
    const user = userEvent.setup();
    function Harness(): React.JSX.Element {
      return (
        <>
          <button type="button" onClick={() => tradeTicketStore.getState().open({ accountId: 'acct-1', conid: '265598', symbol: 'AAPL' })}>Trade</button>
          <TradeTicketModal />
        </>
      );
    }
    renderWithQuery(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Trade' });
    await user.click(trigger);
    await screen.findByRole('dialog');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('focus_trap_prevents_tab_out', async () => {
    const user = userEvent.setup();
    renderWithQuery(
      <>
        <button type="button">Outside</button>
        <TradeTicketModal />
      </>,
    );
    openTicket();
    const close = await screen.findByRole('button', { name: 'Close trade ticket' });
    close.focus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'BUY' })).toHaveFocus();
    await user.tab({ shift: true });
    expect(close).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Outside' })).not.toHaveFocus();
  });

  it('aria_modal_true_on_dialog_container', () => {
    renderOpen();
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
  });

  it('first_focusable_element_focused_on_open', async () => {
    renderOpen();
    await waitFor(() => expect(screen.getByRole('button', { name: 'BUY' })).toHaveFocus());
  });

  it('schwab_market_limit_supported_and_trail_disabled_with_tooltip', async () => {
    mockCapabilitiesFetch(Promise.resolve(capabilitiesResponse(schwabCapabilities())));
    renderOpen('schwab');

    const market = await screen.findByRole('option', { name: 'MARKET' });
    const limit = screen.getByRole('option', { name: 'LIMIT' });
    const trail = Array.from(screen.getAllByRole('option')).find((option) => (
      option instanceof HTMLOptionElement && option.value === 'TRAIL'
    ));
    if (trail === undefined) throw new Error('TRAIL option missing');

    expect(market).toBeEnabled();
    expect(limit).toBeEnabled();
    expect(trail).toBeDisabled();
    expect(trail).toHaveAttribute('title', 'Not supported for this broker');
  });

  it('capability_loading_disables_order_and_tif_dropdowns', async () => {
    mockCapabilitiesFetch(new Promise<Response>(() => { /* pending */ }));
    renderOpen('schwab');

    await screen.findAllByText('Loading capabilities...');

    expect(screen.getByLabelText('Order type')).toBeDisabled();
    expect(screen.getByLabelText('TIF')).toBeDisabled();
  });

  it('capability_error_shows_warning_and_disables_preview', async () => {
    // MED-4: capability error → warning banner shown + Preview button disabled.
    mockCapabilitiesFetch(Promise.resolve(capabilitiesResponse(schwabCapabilities(), 500)));
    renderOpen('schwab');

    expect(await screen.findByText('Unable to load order capabilities. Preview is disabled until data is available.')).toBeInTheDocument();
    // Phase 9.6: capability hook flushes the disabled flag on a separate
    // microtask from the warning banner; wrap in waitFor so the disabled
    // assertion doesn't race the React update and trip the act() warning.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
    });
  });
});

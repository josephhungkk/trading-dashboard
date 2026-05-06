import type { Meta, StoryObj } from '@storybook/react-vite';
import * as React from 'react';
import { TradeTicketModal } from './TradeTicketModal';
import { tradeTicketStore } from './use-trade-ticket';
import type { BrokerCapabilitiesResponse } from '@/services/capabilities/types';
import type { DecimalString, PreviewResponse } from '@/services/types';

function decimal(value: string): DecimalString {
  return value as DecimalString;
}

function preview(overrides: Partial<PreviewResponse> = {}): PreviewResponse {
  return {
    nonce: 'story-nonce',
    notional: decimal('1250'),
    notional_currency: 'USD',
    notional_filled_today: decimal('2500'),
    daily_notional_cap: decimal('10000'),
    max_notional_per_order: decimal('5000'),
    cap_status: 'ok',
    daily_cap_status: 'ok',
    position_sanity: {
      current_qty: decimal('0'),
      new_qty_after_fill: decimal('10'),
      sanity_multiplier: decimal('1'),
      status: 'ok',
      requires_extra_attestation: false,
    },
    contract_summary: { conid: 265598, description: 'AAPL' },
    warnings: [],
    ...overrides,
  };
}

function StoryHarness({
  orderType = 'MARKET',
  previewResponse = null,
  banner = null,
  brokerId = null,
  capabilityMode = 'ready',
}: {
  orderType?: 'MARKET' | 'LIMIT' | 'STOP' | 'STOP_LIMIT';
  previewResponse?: PreviewResponse | null;
  banner?: { kind: 'maintenance'; seconds: number } | { kind: 'kill-switch' } | null;
  brokerId?: string | null;
  capabilityMode?: 'ready' | 'loading' | 'error';
}): React.JSX.Element {
  React.useEffect(() => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = storyFetch(capabilityMode);
    return () => {
      globalThis.fetch = originalFetch;
    };
  }, [capabilityMode]);

  React.useEffect(() => {
    tradeTicketStore.getState().open({ accountId: 'acct-1', conid: '265598', symbol: 'AAPL' });
    if (previewResponse !== null) tradeTicketStore.getState().setPreview(previewResponse);
    return () => tradeTicketStore.getState().close();
  }, [previewResponse]);

  React.useEffect(() => {
    window.setTimeout(() => {
      const select = document.querySelector('select');
      if (select instanceof HTMLSelectElement) {
        select.value = orderType;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }, 0);
  }, [orderType]);

  return <TradeTicketModal storyBanner={banner} {...(brokerId !== null ? { brokerId } : {})} />;
}

function storyFetch(capabilityMode: 'ready' | 'loading' | 'error'): typeof fetch {
  return async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url;
    if (url === '/api/brokers/schwab/capabilities') {
      if (capabilityMode === 'loading') return new Promise<Response>(() => { /* pending */ });
      if (capabilityMode === 'error') return jsonResponse({ error: 'capabilities unavailable' }, 500);
      return jsonResponse(schwabCapabilities(), 200);
    }
    if (url === '/api/orders/preview') return jsonResponse(preview(), 200);
    return jsonResponse({}, 404);
  };
}

function jsonResponse(body: unknown, status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    json: () => Promise.resolve(body),
  } as Response;
}

function schwabCapabilities(): BrokerCapabilitiesResponse {
  const supportedOrderTypes = ['MARKET', 'LIMIT', 'STOP', 'STOP_LIMIT'];
  const unsupportedOrderTypes = ['TRAIL', 'TRAIL_LIMIT', 'MOC', 'MOO', 'LOC', 'LOO'];
  const supportedTifs = ['DAY', 'GTC', 'IOC', 'FOK'];
  return {
    broker_id: 'schwab',
    order_types: [
      ...supportedOrderTypes.map((code, index) => ({ code, label: code, description: code, sort_order: index })),
      ...unsupportedOrderTypes.map((code, index) => ({
        code,
        label: code,
        description: code,
        sort_order: supportedOrderTypes.length + index,
      })),
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
        supportedTifs.map((time_in_force) => ({ broker_id: 'schwab', order_type, time_in_force, supported: true, notes: '' }))
      )),
      ...unsupportedOrderTypes.flatMap((order_type) => (
        supportedTifs.map((time_in_force) => ({
          broker_id: 'schwab',
          order_type,
          time_in_force,
          supported: false,
          notes: 'Not supported for this broker',
        }))
      )),
    ],
  };
}

const meta = {
  title: 'Features/TradeTicketModal',
  component: TradeTicketModal,
  tags: ['autodocs'],
} satisfies Meta<typeof TradeTicketModal>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  render: () => <StoryHarness />,
};

export const LimitOrderValid: Story = {
  render: () => <StoryHarness orderType="LIMIT" />,
};

export const MarketOrderValid: Story = {
  render: () => <StoryHarness orderType="MARKET" />,
};

export const StopOrderValid: Story = {
  render: () => <StoryHarness orderType="STOP" />,
};

export const CapNearWarning: Story = {
  render: () => <StoryHarness previewResponse={preview({ cap_status: 'near', warnings: ['Approaching per-order cap'] })} />,
};

export const CapExceeded: Story = {
  render: () => <StoryHarness previewResponse={preview({ cap_status: 'exceeded', warnings: ['Per-order cap exceeded'] })} />,
};

export const MaintenanceBlocked: Story = {
  render: () => <StoryHarness previewResponse={preview()} banner={{ kind: 'maintenance', seconds: 30 }} />,
};

export const KillSwitchBlocked: Story = {
  render: () => <StoryHarness previewResponse={preview()} banner={{ kind: 'kill-switch' }} />,
};

export const SchwabAccountReady: Story = {
  render: () => <StoryHarness brokerId="schwab" capabilityMode="ready" />,
};

export const CapabilityLoading: Story = {
  render: () => <StoryHarness brokerId="schwab" capabilityMode="loading" />,
};

export const CapabilityError: Story = {
  render: () => <StoryHarness brokerId="schwab" capabilityMode="error" />,
};

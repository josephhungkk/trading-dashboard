import type { Meta, StoryObj } from '@storybook/react-vite';
import * as React from 'react';
import { TradeTicketModal } from './TradeTicketModal';
import { tradeTicketStore } from './use-trade-ticket';
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
}: {
  orderType?: 'MARKET' | 'LIMIT' | 'STOP';
  previewResponse?: PreviewResponse | null;
  banner?: { kind: 'maintenance'; seconds: number } | { kind: 'kill-switch' } | null;
}): React.JSX.Element {
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

  return <TradeTicketModal storyBanner={banner} />;
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

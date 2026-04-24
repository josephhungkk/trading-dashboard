import type { Meta, StoryObj } from '@storybook/react-vite';
import { useEffect } from 'react';
import { ConnectedDropdown } from './ConnectedDropdown';
import { useConnectedStore } from '@/stores/global/connected';
import type { ConnectedStatus } from '@/services/types';

function Seed({ statuses, children }: { statuses: ConnectedStatus[]; children: React.ReactNode }): React.JSX.Element {
  useEffect(() => {
    useConnectedStore.setState({ statuses });
  }, [statuses]);
  return <>{children}</>;
}

const allLive: ConnectedStatus[] = [
  { assetClass: 'stock',  source: 'IBKR TWS',    state: 'live',    latencyMs: 120 },
  { assetClass: 'forex',  source: 'IBKR TWS',    state: 'live',    latencyMs: 80 },
  { assetClass: 'crypto', source: 'Coinbase WS', state: 'live',    latencyMs: 200 },
];

const someDelayed: ConnectedStatus[] = [
  { assetClass: 'stock',  source: 'IBKR TWS',     state: 'live',    latencyMs: 120 },
  { assetClass: 'stock',  source: 'Schwab Stream',state: 'delayed', latencyMs: 15_000 },
  { assetClass: 'forex',  source: 'IBKR TWS',     state: 'live',    latencyMs: 80 },
];

const someDown: ConnectedStatus[] = [
  { assetClass: 'stock',   source: 'IBKR TWS',   state: 'live',    latencyMs: 120 },
  { assetClass: 'futures', source: 'IBKR TWS',   state: 'down',    latencyMs: null },
];

const meta = {
  title: 'Patterns/ConnectedDropdown',
  component: ConnectedDropdown,
  tags: ['autodocs'],
} satisfies Meta<typeof ConnectedDropdown>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AllLive: Story = { render: () => <Seed statuses={allLive}><ConnectedDropdown /></Seed> };
export const SomeDelayed: Story = { render: () => <Seed statuses={someDelayed}><ConnectedDropdown /></Seed> };
export const SomeDown: Story = { render: () => <Seed statuses={someDown}><ConnectedDropdown /></Seed> };

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

const allGreen: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-1',  alias: 'IBKR Live Gateway 1',  backendOk: true, gatewayOk: true, latencyMs: 120 },
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-2',  alias: 'IBKR Live Gateway 2',  backendOk: true, gatewayOk: true, latencyMs: 130 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true, gatewayOk: true, latencyMs: 140 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-2', alias: 'IBKR Paper Gateway 2', backendOk: true, gatewayOk: true, latencyMs: 160 },
  { broker: 'futu',   gatewayId: 'futu-od-1',    alias: 'Futu OpenD',  backendOk: true, gatewayOk: true, latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API',  backendOk: true, gatewayOk: true, latencyMs: 200 },
];

const mixedYellow: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-1',  alias: 'IBKR Live Gateway 1',  backendOk: true, gatewayOk: true,  latencyMs: 120 },
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-2',  alias: 'IBKR Live Gateway 2',  backendOk: true, gatewayOk: false, latencyMs: 240 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true, gatewayOk: true,  latencyMs: 140 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-2', alias: 'IBKR Paper Gateway 2', backendOk: true, gatewayOk: true,  latencyMs: 160 },
  { broker: 'futu',   gatewayId: 'futu-od-1',    alias: 'Futu OpenD',  backendOk: true, gatewayOk: true, latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API',  backendOk: true, gatewayOk: true, latencyMs: 200 },
];

const schwabDown: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-1',  alias: 'IBKR Live Gateway 1',  backendOk: true,  gatewayOk: true,  latencyMs: 120 },
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-2',  alias: 'IBKR Live Gateway 2',  backendOk: true,  gatewayOk: true,  latencyMs: 130 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true,  gatewayOk: true,  latencyMs: 140 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-2', alias: 'IBKR Paper Gateway 2', backendOk: true,  gatewayOk: true,  latencyMs: 160 },
  { broker: 'futu',   gatewayId: 'futu-od-1',    alias: 'Futu OpenD',  backendOk: true,  gatewayOk: true,  latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API',  backendOk: false, gatewayOk: false, latencyMs: null },
];

const meta = {
  title: 'Patterns/ConnectedDropdown',
  component: ConnectedDropdown,
  tags: ['autodocs'],
} satisfies Meta<typeof ConnectedDropdown>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AllGreen: Story = { render: () => <Seed statuses={allGreen}><ConnectedDropdown /></Seed> };
export const MixedYellow: Story = { render: () => <Seed statuses={mixedYellow}><ConnectedDropdown /></Seed> };
export const SchwabDown: Story = { render: () => <Seed statuses={schwabDown}><ConnectedDropdown /></Seed> };

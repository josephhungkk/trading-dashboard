import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConnectedDropdown } from './ConnectedDropdown';
import { useConnectedStore } from '@/stores/global/connected';
import { useFleetHealthStore } from '@/stores/global/fleet-health';
import type { ConnectedStatus } from '@/services/types';

function stubRadixPointer(): void {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  if (typeof proto['hasPointerCapture'] !== 'function') proto['hasPointerCapture'] = () => false;
  if (typeof proto['releasePointerCapture'] !== 'function') proto['releasePointerCapture'] = () => { /* jsdom stub */ };
  if (typeof proto['setPointerCapture'] !== 'function') proto['setPointerCapture'] = () => { /* jsdom stub */ };
  if (typeof proto['scrollIntoView'] !== 'function') proto['scrollIntoView'] = () => { /* jsdom stub */ };
}

// Full SEED analog: 4 IBKR gateways (2 live + 2 paper) + Futu + Schwab all green.
const allGreen: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-1',  alias: 'IBKR Live Gateway 1',  backendOk: true, gatewayOk: true, latencyMs: 120 },
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-2',  alias: 'IBKR Live Gateway 2',  backendOk: true, gatewayOk: true, latencyMs: 130 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true, gatewayOk: true, latencyMs: 140 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-2', alias: 'IBKR Paper Gateway 2', backendOk: true, gatewayOk: true, latencyMs: 160 },
  { broker: 'futu',   gatewayId: 'futu-od-1',    alias: 'Futu OpenD',  backendOk: true, gatewayOk: true, latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API',  backendOk: true, gatewayOk: true, latencyMs: 200 },
];

// One IBKR live gateway has gatewayOk=false → IBKR Live aggregate row is yellow (backendOk XOR gatewayOk).
const mixedYellow: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-1',  alias: 'IBKR Live Gateway 1',  backendOk: true, gatewayOk: true,  latencyMs: 120 },
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-2',  alias: 'IBKR Live Gateway 2',  backendOk: true, gatewayOk: false, latencyMs: 240 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true, gatewayOk: true,  latencyMs: 140 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-2', alias: 'IBKR Paper Gateway 2', backendOk: true, gatewayOk: true,  latencyMs: 160 },
  { broker: 'futu',   gatewayId: 'futu-od-1',    alias: 'Futu OpenD',  backendOk: true, gatewayOk: true, latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API',  backendOk: true, gatewayOk: true, latencyMs: 200 },
];

// Schwab both flags false → red row → red worst-state on trigger.
const schwabDown: ConnectedStatus[] = [
  { broker: 'ibkr', mode: 'live',  gatewayId: 'ibkr-live-gw-1',  alias: 'IBKR Live Gateway 1',  backendOk: true,  gatewayOk: true,  latencyMs: 120 },
  { broker: 'ibkr', mode: 'paper', gatewayId: 'ibkr-paper-gw-1', alias: 'IBKR Paper Gateway 1', backendOk: true,  gatewayOk: true,  latencyMs: 140 },
  { broker: 'futu',   gatewayId: 'futu-od-1',    alias: 'Futu OpenD',  backendOk: true,  gatewayOk: true,  latencyMs: 80 },
  { broker: 'schwab', gatewayId: 'schwab-api-1', alias: 'Schwab API',  backendOk: false, gatewayOk: false, latencyMs: null },
];

describe('ConnectedDropdown', () => {
  beforeEach(() => {
    stubRadixPointer();
    useFleetHealthStore.setState({ degraded_sidecars: [] });
  });

  it('renders a trigger labeled connection health', () => {
    useConnectedStore.setState({ statuses: allGreen });
    render(<ConnectedDropdown />);
    expect(screen.getByRole('button', { name: /connection health/i })).toBeInTheDocument();
  });

  it('opens menu on click and lists 4 aggregate rows (IBKR Live, IBKR Paper, Futu, Schwab)', async () => {
    const user = userEvent.setup();
    useConnectedStore.setState({ statuses: allGreen });
    render(<ConnectedDropdown />);
    await user.click(screen.getByRole('button', { name: /connection health/i }));
    const items = screen.getAllByRole('menuitem');
    expect(items).toHaveLength(4);
    expect(screen.getByText(/Interactive Brokers Live/i)).toBeInTheDocument();
    expect(screen.getByText(/Interactive Brokers Paper/i)).toBeInTheDocument();
    expect(screen.getByText(/^Futu Securities$/)).toBeInTheDocument();
    expect(screen.getByText(/^Charles Schwab$/)).toBeInTheDocument();
  });

  it('renders all green rows when every gateway is ok', async () => {
    const user = userEvent.setup();
    useConnectedStore.setState({ statuses: allGreen });
    render(<ConnectedDropdown />);
    await user.click(screen.getByRole('button', { name: /connection health/i }));
    const greenBadges = screen.getAllByText('green');
    // 4 aggregate rows × 1 badge each = 4 green row badges.
    expect(greenBadges).toHaveLength(4);
  });

  it('renders a yellow row when a gateway has backendOk XOR gatewayOk', async () => {
    const user = userEvent.setup();
    useConnectedStore.setState({ statuses: mixedYellow });
    render(<ConnectedDropdown />);
    await user.click(screen.getByRole('button', { name: /connection health/i }));
    expect(screen.getAllByText('yellow').length).toBeGreaterThanOrEqual(1);
  });

  it('renders a red row when a group has both flags false', async () => {
    const user = userEvent.setup();
    useConnectedStore.setState({ statuses: schwabDown });
    render(<ConnectedDropdown />);
    await user.click(screen.getByRole('button', { name: /connection health/i }));
    expect(screen.getAllByText('red').length).toBeGreaterThanOrEqual(1);
  });

  it('hides the fleet-degraded pill when degraded_sidecars is empty', () => {
    useConnectedStore.setState({ statuses: allGreen });
    useFleetHealthStore.setState({ degraded_sidecars: [] });
    render(<ConnectedDropdown />);
    expect(screen.queryByTestId('fleet-degraded-pill')).toBeNull();
  });

  it('shows the fleet-degraded pill with singular label when one sidecar is degraded', () => {
    useConnectedStore.setState({ statuses: allGreen });
    useFleetHealthStore.setState({ degraded_sidecars: ['isa-live'] });
    render(<ConnectedDropdown />);
    const pill = screen.getByTestId('fleet-degraded-pill');
    expect(pill).toBeInTheDocument();
    expect(pill).toHaveTextContent(/^1 broker degraded$/);
  });

  it('shows the fleet-degraded pill with plural label when multiple sidecars are degraded', () => {
    useConnectedStore.setState({ statuses: allGreen });
    useFleetHealthStore.setState({ degraded_sidecars: ['isa-live', 'normal-paper'] });
    render(<ConnectedDropdown />);
    const pill = screen.getByTestId('fleet-degraded-pill');
    expect(pill).toHaveTextContent(/^2 brokers degraded$/);
  });
});

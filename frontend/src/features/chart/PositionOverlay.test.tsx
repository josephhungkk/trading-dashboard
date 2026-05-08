import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import type { Chart, OverlayCreate, OverlayFilter } from 'klinecharts';
import { PositionOverlay } from './PositionOverlay';
import { useChartStore } from './stores/chartStore';
import { getScopedStores } from '@/stores/registry';
import { useModeStore } from '@/stores/global/mode';
import type { Position } from '@/services/types';

const mockCreateOverlay = vi.fn((overlay: OverlayCreate): string => {
  void overlay;
  return 'overlay-1';
});
const mockRemoveOverlay = vi.fn((filter: OverlayFilter): boolean => {
  void filter;
  return true;
});

const mockChart = {
  createOverlay: mockCreateOverlay,
  removeOverlay: mockRemoveOverlay,
} as unknown as Chart;

describe('PositionOverlay', () => {
  beforeEach(() => {
    mockCreateOverlay.mockClear();
    mockRemoveOverlay.mockClear();
    useModeStore.setState({ mode: 'paper', pendingMode: null, status: 'idle' });
    getScopedStores('paper').usePositions.setState({ positions: [] });
    useChartStore.setState({ pending_modify_id: new Map() });
  });

  afterEach(() => {
    getScopedStores('paper').usePositions.setState({ positions: [] });
  });

  it('renders nothing visibly', () => {
    const { container } = render(
      <PositionOverlay canonicalId="AAPL.US" chartRef={{ current: mockChart }} onModifyRequest={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('calls chart.createOverlay for each position with bracket', () => {
    getScopedStores('paper').usePositions.setState({
      positions: [
        positionWithBracket('AAPL.US', 'BUY', 'aapl-sl-1', 'aapl-tp-1'),
        positionWithBracket('AAPL.US', 'SELL', 'aapl-sl-2', 'aapl-tp-2'),
      ],
    });

    render(<PositionOverlay canonicalId="AAPL.US" chartRef={{ current: mockChart }} onModifyRequest={vi.fn()} />);

    expect(mockCreateOverlay).toHaveBeenCalledTimes(2);
    expect(mockCreateOverlay.mock.calls[0]?.[0]).toMatchObject({
      name: 'longPosition',
      points: [{ value: 184 }, { value: 180 }, { value: 190 }],
    });
    expect(mockCreateOverlay.mock.calls[1]?.[0]).toMatchObject({ name: 'shortPosition' });
  });

  it('removes overlays on unmount', () => {
    getScopedStores('paper').usePositions.setState({
      positions: [positionWithBracket('AAPL.US', 'BUY', 'aapl-sl-1', 'aapl-tp-1')],
    });

    const { unmount } = render(
      <PositionOverlay canonicalId="AAPL.US" chartRef={{ current: mockChart }} onModifyRequest={vi.fn()} />,
    );
    unmount();

    expect(mockRemoveOverlay).toHaveBeenCalledWith({ id: 'overlay-1' });
  });

  it('skips positions without brackets', () => {
    getScopedStores('paper').usePositions.setState({
      positions: [basePosition('AAPL.US')],
    });

    render(<PositionOverlay canonicalId="AAPL.US" chartRef={{ current: mockChart }} onModifyRequest={vi.fn()} />);

    expect(mockCreateOverlay).not.toHaveBeenCalled();
  });

  it('re-creates overlays when positions change', () => {
    mockCreateOverlay
      .mockReturnValueOnce('overlay-1')
      .mockReturnValueOnce('overlay-2');
    getScopedStores('paper').usePositions.setState({
      positions: [positionWithBracket('AAPL.US', 'BUY', 'aapl-sl-1', 'aapl-tp-1')],
    });

    render(<PositionOverlay canonicalId="AAPL.US" chartRef={{ current: mockChart }} onModifyRequest={vi.fn()} />);

    act(() => {
      getScopedStores('paper').usePositions.setState({
        positions: [positionWithBracket('AAPL.US', 'BUY', 'aapl-sl-2', 'aapl-tp-2')],
      });
    });

    expect(mockRemoveOverlay).toHaveBeenCalledWith({ id: 'overlay-1' });
    expect(mockCreateOverlay).toHaveBeenCalledTimes(2);
    expect(mockCreateOverlay.mock.calls[1]?.[0]).toMatchObject({
      extendData: { slLegId: 'aapl-sl-2', tpLegId: 'aapl-tp-2' },
    });
  });
});

function basePosition(symbol: string): Position {
  return {
    accountId: 'paper-1',
    symbol,
    qty: 10,
    avgCost: 184,
    marketValue: 1840,
    pnlUnrealized: 0,
    pnlRealized: 0,
    currency: 'USD',
    asOf: '2026-05-08T00:00:00Z',
  };
}

function positionWithBracket(
  symbol: string,
  side: 'BUY' | 'SELL',
  stopLossId: string,
  takeProfitId: string,
): Position {
  const position = {
    ...basePosition(symbol),
    qty: side === 'BUY' ? 10 : -10,
    side,
    avgPrice: 184,
    canonical_id: symbol,
    bracket: {
      id: `${symbol}-${side}`,
      stopLossId,
      takeProfitId,
      stopLossPrice: 180,
      takeProfitPrice: 190,
    },
  };
  return position as unknown as Position;
}

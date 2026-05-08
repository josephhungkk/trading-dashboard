import { describe, it, expect, beforeEach } from 'vitest';
import { useChartStore } from './chartStore';

function resetStore(): void {
  useChartStore.setState({
    timeframe: '1m',
    indicators: [],
    drawings: [],
    chartType: 'candle',
    activeDrawingTool: null,
    pending_modify_id: new Map(),
  });
}

describe('useChartStore', () => {
  beforeEach(() => {
    resetStore();
  });

  it('has correct initial state', () => {
    const state = useChartStore.getState();
    expect(state.timeframe).toBe('1m');
    expect(state.indicators).toEqual([]);
    expect(state.chartType).toBe('candle');
  });

  it('setTimeframe updates timeframe', () => {
    useChartStore.getState().setTimeframe('5m');
    expect(useChartStore.getState().timeframe).toBe('5m');
  });

  it('addIndicator appends without duplicates', () => {
    useChartStore.getState().addIndicator('MA');
    useChartStore.getState().addIndicator('RSI');
    useChartStore.getState().addIndicator('MA'); // duplicate — ignored

    expect(useChartStore.getState().indicators).toEqual(['MA', 'RSI']);
  });

  it('removeIndicator removes by name', () => {
    useChartStore.getState().setIndicators(['MA', 'RSI', 'BOLL']);
    useChartStore.getState().removeIndicator('RSI');

    expect(useChartStore.getState().indicators).toEqual(['MA', 'BOLL']);
  });

  it('setChartType updates chartType', () => {
    useChartStore.getState().setChartType('area');
    expect(useChartStore.getState().chartType).toBe('area');
  });

  it('activeDrawingTool starts null', () => {
    expect(useChartStore.getState().activeDrawingTool).toBeNull();
  });

  it('setActiveDrawingTool sets a tool name', () => {
    useChartStore.getState().setActiveDrawingTool('priceLine');
    expect(useChartStore.getState().activeDrawingTool).toBe('priceLine');
  });

  it('setActiveDrawingTool(null) clears the active tool', () => {
    useChartStore.getState().setActiveDrawingTool('rect');
    useChartStore.getState().setActiveDrawingTool(null);
    expect(useChartStore.getState().activeDrawingTool).toBeNull();
  });

  it('setActiveDrawingTool replaces a previous selection', () => {
    useChartStore.getState().setActiveDrawingTool('segment');
    useChartStore.getState().setActiveDrawingTool('circle');
    expect(useChartStore.getState().activeDrawingTool).toBe('circle');
  });
});

// MED-2: pending_modify_id shape no longer includes nonce — held in closure only.
describe('setPendingModify', () => {
  beforeEach(() => {
    useChartStore.setState({ pending_modify_id: new Map() });
  });

  it('sets entry without nonce field', () => {
    const entry = { targetPrice: 185.5, startedAt: 1000 };
    useChartStore.getState().setPendingModify('leg-1', entry);
    const map = useChartStore.getState().pending_modify_id;
    expect(map.has('leg-1')).toBe(true);
    expect(map.get('leg-1')).toEqual({ targetPrice: 185.5, startedAt: 1000 });
    // nonce must NOT be present in the stored entry
    expect('nonce' in (map.get('leg-1') ?? {})).toBe(false);
  });

  it('clears entry when null passed', () => {
    useChartStore.getState().setPendingModify('leg-1', { targetPrice: 185.5, startedAt: 1000 });
    useChartStore.getState().setPendingModify('leg-1', null);
    expect(useChartStore.getState().pending_modify_id.has('leg-1')).toBe(false);
  });

  it('tracks multiple legs independently', () => {
    useChartStore.getState().setPendingModify('leg-a', { targetPrice: 100, startedAt: 1 });
    useChartStore.getState().setPendingModify('leg-b', { targetPrice: 200, startedAt: 2 });
    const map = useChartStore.getState().pending_modify_id;
    expect(map.size).toBe(2);
    expect(map.get('leg-a')?.targetPrice).toBe(100);
    expect(map.get('leg-b')?.targetPrice).toBe(200);
  });

  it('clearing one leg does not affect others', () => {
    useChartStore.getState().setPendingModify('leg-a', { targetPrice: 100, startedAt: 1 });
    useChartStore.getState().setPendingModify('leg-b', { targetPrice: 200, startedAt: 2 });
    useChartStore.getState().setPendingModify('leg-a', null);
    const map = useChartStore.getState().pending_modify_id;
    expect(map.has('leg-a')).toBe(false);
    expect(map.has('leg-b')).toBe(true);
  });
});

import { describe, it, expect, beforeEach } from 'vitest';
import { useChartStore } from './chartStore';

function resetStore(): void {
  useChartStore.setState({
    timeframe: '1m',
    indicators: [],
    drawings: [],
    chartType: 'candle',
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
});

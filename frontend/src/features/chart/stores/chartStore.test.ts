import { describe, it, expect, beforeEach } from 'vitest';
import { useChartStore } from './chartStore';

function resetStore(): void {
  useChartStore.setState({
    timeframe: '1m',
    indicators: [],
    drawings: [],
    chartType: 'candle',
    activeDrawingTool: null,
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

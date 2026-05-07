import { create } from 'zustand';

export interface ChartState {
  timeframe: string;
  indicators: string[]; // names like 'MA', 'RSI'
  drawings: unknown[]; // klinecharts overlay objects (Task 38)
  chartType: 'candle' | 'area' | 'bar';
  setTimeframe: (tf: string) => void;
  setIndicators: (inds: string[]) => void;
  addIndicator: (name: string) => void;
  removeIndicator: (name: string) => void;
  setChartType: (t: 'candle' | 'area' | 'bar') => void;
}

export const useChartStore = create<ChartState>((set) => ({
  timeframe: '1m',
  indicators: [],
  drawings: [],
  chartType: 'candle',
  setTimeframe: (tf) => set({ timeframe: tf }),
  setIndicators: (inds) => set({ indicators: inds }),
  addIndicator: (name) =>
    set((s) => ({
      indicators: s.indicators.includes(name) ? s.indicators : [...s.indicators, name],
    })),
  removeIndicator: (name) =>
    set((s) => ({
      indicators: s.indicators.filter((i) => i !== name),
    })),
  setChartType: (t) => set({ chartType: t }),
}));

import { create } from 'zustand';

export interface ChartState {
  timeframe: string;
  indicators: string[]; // names like 'MA', 'RSI'
  drawings: unknown[]; // klinecharts overlay objects (Task 38)
  chartType: 'candle' | 'area' | 'bar';
  activeDrawingTool: string | null;
  pending_modify_id: Map<string, { nonce: string; targetPrice: number; startedAt: number }>;
  setTimeframe: (tf: string) => void;
  setIndicators: (inds: string[]) => void;
  addIndicator: (name: string) => void;
  removeIndicator: (name: string) => void;
  setChartType: (t: 'candle' | 'area' | 'bar') => void;
  setActiveDrawingTool: (tool: string | null) => void;
  setPendingModify: (
    legId: string,
    entry: { nonce: string; targetPrice: number; startedAt: number } | null,
  ) => void;
}

export const useChartStore = create<ChartState>((set) => ({
  timeframe: '1m',
  indicators: [],
  drawings: [],
  chartType: 'candle',
  activeDrawingTool: null,
  pending_modify_id: new Map(),
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
  setActiveDrawingTool: (tool) => set({ activeDrawingTool: tool }),
  setPendingModify: (legId, entry) => set((s) => {
    const next = new Map(s.pending_modify_id);
    if (entry === null) next.delete(legId);
    else next.set(legId, entry);
    return { pending_modify_id: next };
  }),
}));

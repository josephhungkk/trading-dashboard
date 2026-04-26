import { create } from 'zustand';

interface FleetHealthState {
  degraded_sidecars: string[];
  setDegraded: (labels: string[]) => void;
}

export const useFleetHealthStore = create<FleetHealthState>((set) => ({
  degraded_sidecars: [],
  setDegraded: (labels) => set({ degraded_sidecars: labels }),
}));

export interface FleetHealth {
  ok: boolean;
  count: number;
  labels: string[];
}

export function useFleetHealth(): FleetHealth {
  const labels = useFleetHealthStore((s) => s.degraded_sidecars);
  return { ok: labels.length === 0, count: labels.length, labels };
}

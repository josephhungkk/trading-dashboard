import { create } from 'zustand';

export interface FleetMaintenance {
  active: boolean;
  window: 'weekend' | 'daily' | null;
  until: Date | null;
}

interface FleetMaintenanceState {
  maintenance: FleetMaintenance;
  setMaintenance: (maintenance: FleetMaintenance) => void;
}

export const useFleetMaintenance = create<FleetMaintenanceState>((set) => ({
  maintenance: {
    active: false,
    window: null,
    until: null,
  },
  setMaintenance: (maintenance) => set({ maintenance }),
}));

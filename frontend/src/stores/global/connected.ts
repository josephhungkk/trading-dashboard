import { create } from 'zustand';
import { getServices } from '@/services/registry';
import type { ConnectedStatus } from '@/services/types';

interface ConnectedState {
  statuses: ConnectedStatus[];
  init(): void;
}

export const useConnectedStore = create<ConnectedState>((set) => ({
  statuses: [],
  init() {
    const svc = getServices().connected;
    svc.subscribe(statuses => set({ statuses }));
    set({ statuses: svc.snapshot() });
  },
}));

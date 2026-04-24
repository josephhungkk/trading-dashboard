import { create } from 'zustand';
import { getServices } from '@/services/registry';
import type { ConnectedStatus } from '@/services/types';

export const useConnectedStore = create<{ statuses: ConnectedStatus[] }>((set) => {
  const svc = getServices().connected;
  svc.subscribe(statuses => set({ statuses }));
  return { statuses: svc.snapshot() };
});

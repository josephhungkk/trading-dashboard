import { create } from 'zustand';
import type { Mode } from '@/services/types';

export type ModeStatus = 'idle' | 'switching';

interface ModeState {
  mode: Mode;
  pendingMode: Mode | null;
  status: ModeStatus;
  requestModeSwitch(target: Mode): void;
  confirmModeSwitch(): void;
  cancelModeSwitch(): void;
  setMode(next: Mode): void;
  setStatus(status: ModeStatus): void;
}

export const useModeStore = create<ModeState>((set, get) => ({
  mode: 'paper',
  pendingMode: null,
  status: 'idle',
  requestModeSwitch(target) {
    if (get().mode === target) return;
    if (target === 'live') set({ pendingMode: 'live' });
    else                   set({ mode: 'paper' });
  },
  confirmModeSwitch() {
    const p = get().pendingMode;
    if (p) set({ mode: p, pendingMode: null });
  },
  cancelModeSwitch() { set({ pendingMode: null }); },
  setMode(next) { set({ mode: next }); },
  setStatus(status) { set({ status }); },
}));

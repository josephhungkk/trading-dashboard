import { create } from 'zustand';
import { getServices } from '@/services/registry';
import type { Command } from '@/services/types';

interface CommandsState {
  open: boolean;
  commands: Command[];
  setOpen(open: boolean): void;
  register(cmd: Command): () => void;
  init(): void;
}

export const useCommandsStore = create<CommandsState>((set) => ({
  open: false,
  commands: [],
  setOpen(open) { set({ open }); },
  register(cmd) {
    const registry = getServices().commands;
    const unregister = registry.register(cmd);
    set({ commands: registry.list() });
    return unregister;
  },
  init() {
    const registry = getServices().commands;
    registry.subscribe(list => set({ commands: list }));
    set({ commands: registry.list() });
  },
}));

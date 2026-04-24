import { create } from 'zustand';
import { getServices } from '@/services/registry';
import type { Command } from '@/services/types';

interface CommandsState {
  open: boolean;
  commands: Command[];
  setOpen(open: boolean): void;
  register(cmd: Command): () => void;
}

export const useCommandsStore = create<CommandsState>((set) => {
  const registry = getServices().commands;
  registry.subscribe(list => set({ commands: list }));
  return {
    open: false,
    commands: registry.list(),
    setOpen(open) { set({ open }); },
    register(cmd) { return registry.register(cmd); },
  };
});

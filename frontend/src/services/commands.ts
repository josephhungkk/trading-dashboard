import type { Command } from './types';

export interface CommandRegistry {
  register(cmd: Command): () => void;
  list(): Command[];
  subscribe(cb: (cmds: Command[]) => void): () => void;
}

export class InMemoryCommandRegistry implements CommandRegistry {
  private commands = new Map<string, Command>();
  private listeners = new Set<(cmds: Command[]) => void>();

  register(cmd: Command): () => void {
    this.commands.set(cmd.id, cmd);
    this.notify();
    return () => { this.commands.delete(cmd.id); this.notify(); };
  }

  list(): Command[] { return Array.from(this.commands.values()); }

  subscribe(cb: (cmds: Command[]) => void): () => void {
    this.listeners.add(cb);
    cb(this.list());
    return () => { this.listeners.delete(cb); };
  }

  private notify() {
    const snap = this.list();
    for (const cb of this.listeners) cb(snap);
  }
}

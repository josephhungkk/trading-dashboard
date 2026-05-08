import { create } from 'zustand';

export interface ToastItem {
  id: string;
  title?: string;
  description?: string;
  tone?: 'neutral' | 'success' | 'error';
  durationMs?: number;
}

interface ToastState {
  items: ToastItem[];
  push(item: Omit<ToastItem, 'id'>): string;
  dismiss(id: string): void;
}

export const useToastStore = create<ToastState>((set) => ({
  items: [],
  push(item) {
    const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    set((s) => ({ items: [...s.items, { ...item, id }] }));
    if (item.durationMs !== 0) {
      setTimeout(() => useToastStore.getState().dismiss(id), item.durationMs ?? 3000);
    }
    return id;
  },
  dismiss(id) {
    set((s) => ({ items: s.items.filter((t) => t.id !== id) }));
  },
}));

export function useToast(): {
  toast: (item: Omit<ToastItem, 'id'>) => string;
  dismiss: (id: string) => void;
} {
  return {
    toast: (item) => useToastStore.getState().push(item),
    dismiss: (id) => useToastStore.getState().dismiss(id),
  };
}

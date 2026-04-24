import { create } from 'zustand';
export const useThemeStore = create(() => ({ theme: 'dark' as const }));

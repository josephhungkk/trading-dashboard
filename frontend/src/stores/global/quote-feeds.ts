import { create } from 'zustand';
import { getServices } from '@/services/registry';
import type { QuoteFeedStatus } from '@/services/types';

export const useQuoteFeedStore = create<{ feeds: QuoteFeedStatus[] }>((set) => {
  const svc = getServices().quoteFeeds;
  svc.subscribe(feeds => set({ feeds }));
  return { feeds: svc.snapshot() };
});

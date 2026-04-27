import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';
import type { PreviewResponse } from '@/services/types';

export interface TradeTicketState {
  isOpen: boolean;
  accountId: string | null;
  defaultConid: string | null;
  defaultSymbol: string | null;
  clientOrderId: string | null;
  preview: PreviewResponse | null;
  inFlight: boolean;
  open: (args: { accountId: string; conid?: string | undefined; symbol?: string | undefined }) => void;
  close: () => void;
  setPreview: (p: PreviewResponse | null) => void;
  setInFlight: (v: boolean) => void;
}

const initialState = {
  isOpen: false,
  accountId: null,
  defaultConid: null,
  defaultSymbol: null,
  clientOrderId: null,
  preview: null,
  inFlight: false,
} satisfies Pick<
  TradeTicketState,
  'isOpen' | 'accountId' | 'defaultConid' | 'defaultSymbol' | 'clientOrderId' | 'preview' | 'inFlight'
>;

function makeClientOrderId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `client-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const tradeTicketStore = createStore<TradeTicketState>((set) => ({
  ...initialState,
  open: ({ accountId, conid, symbol }) => {
    set({
      isOpen: true,
      accountId,
      defaultConid: conid ?? null,
      defaultSymbol: symbol ?? null,
      clientOrderId: makeClientOrderId(),
      preview: null,
      inFlight: false,
    });
  },
  close: () => set(initialState),
  setPreview: (preview) => set({ preview }),
  setInFlight: (inFlight) => set({ inFlight }),
}));

export function useTradeTicketStore<T>(selector: (state: TradeTicketState) => T): T {
  return useStore(tradeTicketStore, selector);
}

type TradeTicketCompatPatch = Partial<Omit<TradeTicketState, 'defaultConid' | 'defaultSymbol'>> & {
  defaultConid?: string | null | undefined;
  defaultSymbol?: string | null | undefined;
};

function useTradeTicketHook(): TradeTicketState {
  return useTradeTicketStore((state) => state);
}

export const useTradeTicket = Object.assign(useTradeTicketHook, {
  getState: tradeTicketStore.getState,
  setState: (patch: TradeTicketCompatPatch) => {
    tradeTicketStore.setState({
      ...patch,
      defaultConid: patch.defaultConid ?? null,
      defaultSymbol: patch.defaultSymbol ?? null,
    });
  },
});

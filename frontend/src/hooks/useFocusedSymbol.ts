import { useEffect } from 'react';
import { getServices } from '@/services/registry';

export function useFocusedSymbol(symbol: string | null): void {
  useEffect(() => {
    const { quotes } = getServices();
    quotes.setFocus(symbol);
    return () => {
      quotes.setFocus(null);
    };
  }, [symbol]);
}

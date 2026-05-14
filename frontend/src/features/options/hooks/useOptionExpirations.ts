import { useQuery } from '@tanstack/react-query';
import { fetchExpirations } from '@/services/options/api';

export function useOptionExpirations(symbol: string, currency = 'USD') {
  return useQuery({
    queryKey: ['options', 'expirations', symbol, currency],
    queryFn: () => fetchExpirations(symbol, currency),
    enabled: symbol.trim().length > 0,
    staleTime: 60_000,
  });
}

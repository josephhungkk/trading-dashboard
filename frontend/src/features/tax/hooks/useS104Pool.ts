import { useQuery } from "@tanstack/react-query";

export interface PoolEntry {
  instrument_id: number;
  symbol: string;
  qty: string;
  total_cost_gbp: string;
  pool_avg_cost_gbp: string;
  last_updated_at: string;
}

export interface S104PoolData {
  positions: PoolEntry[];
  total_count: number;
}

export function useS104Pool() {
  return useQuery<S104PoolData>({
    queryKey: ["cgt", "pool"],
    queryFn: async () => {
      const resp = await fetch("/api/cgt/pool");
      if (!resp.ok) throw new Error("Failed to fetch S104 pool");
      return resp.json() as Promise<S104PoolData>;
    },
    refetchInterval: 60_000,
  });
}

import { useQuery } from "@tanstack/react-query";

export interface CgtSummary {
  tax_year: number;
  net_gain_gbp: string;
  net_loss_gbp: string;
  annual_exempt_amount_gbp: string;
  used_allowance_gbp: string;
  remaining_allowance_gbp: string;
  income_total_gbp: string;
  disposal_count: number;
}

export function useCgtSummary(taxYear?: number) {
  const params = taxYear ? `?tax_year=${taxYear}` : "";
  return useQuery<CgtSummary>({
    queryKey: ["cgt", "summary", taxYear],
    queryFn: async () => {
      const resp = await fetch(`/api/cgt/summary${params}`);
      if (!resp.ok) throw new Error("Failed to fetch CGT summary");
      return resp.json() as Promise<CgtSummary>;
    },
    refetchInterval: 30_000,
  });
}

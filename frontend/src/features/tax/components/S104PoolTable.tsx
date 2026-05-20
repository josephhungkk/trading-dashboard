import * as React from "react";
import { useS104Pool } from "../hooks/useS104Pool";

export function S104PoolTable(): React.JSX.Element {
  const { data, isLoading } = useS104Pool();

  if (isLoading) {
    return <div className="animate-pulse h-40 bg-muted rounded" />;
  }
  if (!data || data.positions.length === 0) {
    return <p className="text-muted-foreground text-sm">No pool positions.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-fg-muted">
            <th className="py-2 text-left font-medium">Symbol</th>
            <th className="py-2 text-right font-medium">Qty</th>
            <th className="py-2 text-right font-medium">Avg Cost (£)</th>
            <th className="py-2 text-right font-medium">Total Cost (£)</th>
          </tr>
        </thead>
        <tbody>
          {data.positions.map((p) => (
            <tr key={p.instrument_id} className="border-b border-border/50 hover:bg-muted/20">
              <td className="py-2 font-mono">{p.symbol}</td>
              <td className="py-2 text-right">
                {parseFloat(p.qty).toLocaleString()}
              </td>
              <td className="py-2 text-right">
                {parseFloat(p.pool_avg_cost_gbp).toFixed(4)}
              </td>
              <td className="py-2 text-right">
                {parseFloat(p.total_cost_gbp).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

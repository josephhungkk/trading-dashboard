import * as React from "react";
import { useCgtSummary } from "../hooks/useCgtSummary";

interface Props {
  taxYear: number;
}

export function AllowanceGauge({ taxYear }: Props): React.JSX.Element {
  const { data, isLoading } = useCgtSummary(taxYear);

  if (isLoading || !data) {
    return <div className="animate-pulse h-24 bg-muted rounded-lg" />;
  }

  const used = parseFloat(data.used_allowance_gbp);
  const total = parseFloat(data.annual_exempt_amount_gbp);
  const pct = Math.min((used / total) * 100, 100);
  const netGain = parseFloat(data.net_gain_gbp);
  const netLoss = parseFloat(data.net_loss_gbp);
  const isOver = netGain + netLoss > total;

  return (
    <div className="p-4 border rounded-lg space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">
          CGT Allowance {data.tax_year}/{String(data.tax_year + 1).slice(-2)}
        </span>
        <span className={isOver ? "text-red-600" : "text-muted-foreground"}>
          £{used.toLocaleString()} / £{total.toLocaleString()}
        </span>
      </div>
      <div className="h-3 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${isOver ? "bg-red-600" : "bg-green-600"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground">
        <div>Gain: £{netGain.toLocaleString()}</div>
        <div>Loss: £{Math.abs(netLoss).toLocaleString()}</div>
        <div>Remaining: £{parseFloat(data.remaining_allowance_gbp).toLocaleString()}</div>
      </div>
    </div>
  );
}

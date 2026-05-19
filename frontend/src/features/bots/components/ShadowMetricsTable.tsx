import type { ShadowVsLive } from '@/services/shadow_promoter/types';

interface Props {
  shadow: ShadowVsLive;
}

function deltaClass(value: string): string {
  if (value.startsWith('+')) return 'text-green-700';
  if (value.startsWith('-')) return 'text-red-700';
  return 'text-muted-foreground';
}

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function ShadowMetricsTable({ shadow }: Props) {
  const rows = [
    {
      metric: 'Sharpe',
      shadow: shadow.shadow_metrics.sharpe.toFixed(2),
      live: shadow.live_metrics.sharpe.toFixed(2),
      delta: shadow.delta.sharpe,
    },
    {
      metric: 'MAR',
      shadow: shadow.shadow_metrics.mar.toFixed(2),
      live: shadow.live_metrics.mar.toFixed(2),
      delta: '--',
    },
    {
      metric: 'Max DD',
      shadow: `${shadow.shadow_metrics.max_dd.toFixed(2)}%`,
      live: `${shadow.live_metrics.max_dd.toFixed(2)}%`,
      delta: shadow.delta.max_dd,
    },
    {
      metric: 'Win Rate',
      shadow: percent(shadow.shadow_metrics.win_rate),
      live: percent(shadow.live_metrics.win_rate),
      delta: '--',
    },
    {
      metric: 'Avg Trade P&L',
      shadow: shadow.shadow_metrics.avg_trade_pnl,
      live: shadow.live_metrics.avg_trade_pnl,
      delta: '--',
    },
    {
      metric: 'Total Trades',
      shadow: String(shadow.shadow_metrics.total_trades),
      live: String(shadow.live_metrics.total_trades),
      delta: '--',
    },
  ];

  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b text-xs uppercase text-muted-foreground">
          <th className="py-2 font-medium">Metric</th>
          <th className="py-2 font-medium">Shadow</th>
          <th className="py-2 font-medium">Live</th>
          <th className="py-2 font-medium">Delta</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.metric} className="border-b last:border-b-0">
            <td className="py-2">{row.metric}</td>
            <td className="py-2">{row.shadow}</td>
            <td className="py-2">{row.live}</td>
            <td className={`py-2 ${deltaClass(row.delta)}`}>{row.delta}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getBot, upsertRiskCaps } from '../../../services/bots/api';
import type { RiskCaps } from '../../../services/bots/types';

interface Props {
  botId: string;
}

function toNum(v: string): number | null {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

function toArr(v: string): string[] | null {
  const trimmed = v.trim();
  if (trimmed === '') return null;
  return trimmed.split(',').map((s) => s.trim());
}

export function RiskCapsForm({ botId }: Props): React.JSX.Element {
  const qc = useQueryClient();
  const { data: bot } = useQuery({
    queryKey: ['bot', botId],
    queryFn: () => getBot(botId),
  });

  const [caps, setCaps] = React.useState<RiskCaps>({
    max_position_size: null,
    max_daily_loss: null,
    max_open_orders: null,
    max_order_size: null,
    allowed_asset_classes: null,
  });
  const [saved, setSaved] = React.useState(false);

  const mut = useMutation({
    mutationFn: () => upsertRiskCaps(botId, caps),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['bot', botId] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  if (bot == null) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const field = (
    label: string,
    key: keyof RiskCaps,
    placeholder: string,
    isArr = false,
  ) => {
    const current = caps[key];
    const displayVal = isArr
      ? (current as string[] | null)?.join(', ') ?? ''
      : (current as number | null)?.toString() ?? '';

    return (
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-muted-foreground">{label}</span>
        <input
          type="text"
          placeholder={placeholder}
          value={displayVal}
          onChange={(e) => {
            const v = e.target.value;
            setCaps((prev) => ({
              ...prev,
              [key]: isArr ? toArr(v) : toNum(v),
            }));
          }}
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        />
      </label>
    );
  };

  return (
    <div className="space-y-3">
      {field('Max position size', 'max_position_size', 'e.g. 10000')}
      {field('Max daily loss', 'max_daily_loss', 'e.g. 500')}
      {field('Max open orders', 'max_open_orders', 'e.g. 5')}
      {field('Max order size', 'max_order_size', 'e.g. 2000')}
      {field('Allowed asset classes (comma-sep)', 'allowed_asset_classes', 'e.g. STOCK,ETF', true)}
      <button
        onClick={() => mut.mutate()}
        disabled={mut.isPending}
        className="btn-primary"
      >
        {mut.isPending ? 'Saving…' : saved ? 'Saved ✓' : 'Save caps'}
      </button>
      {mut.isError && (
        <p className="text-xs text-destructive">
          {(mut.error as Error).message}
        </p>
      )}
    </div>
  );
}

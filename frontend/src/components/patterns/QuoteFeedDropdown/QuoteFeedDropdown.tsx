import * as React from 'react';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/primitives/DropdownMenu';
import { Button } from '@/components/primitives/Button';
import { Badge } from '@/components/primitives/Badge';
// eslint-disable-next-line boundaries/element-types -- global quote-feed store is an ambient source
import { useQuoteFeedStore } from '@/stores/global/quote-feeds';
// eslint-disable-next-line boundaries/element-types -- type-only import for tone mapping
import type { QuoteFeedStatus, AssetClass } from '@/services/types';

type Tone = 'realtime' | 'delayed' | 'none';
const TONE_VARIANT: Record<Tone, 'up' | 'warn' | 'down'> = {
  realtime: 'up',
  delayed: 'warn',
  none: 'down',
};
const TONE_RANK: Record<Tone, number> = { realtime: 0, delayed: 1, none: 2 };

function worstOf(feeds: QuoteFeedStatus[]): Tone {
  return feeds.reduce<Tone>(
    (acc, f) => (TONE_RANK[f.feedType] > TONE_RANK[acc] ? f.feedType : acc),
    'realtime',
  );
}

const TRIGGER_LABEL: Record<Tone, string> = {
  realtime: 'Realtime',
  delayed: 'Delayed',
  none: 'Offline',
};

interface Group {
  assetClass: AssetClass;
  rows: QuoteFeedStatus[];
  worst: Tone;
}

function groupByAssetClass(feeds: QuoteFeedStatus[]): Group[] {
  const byClass = new Map<AssetClass, QuoteFeedStatus[]>();
  for (const f of feeds) {
    const list = byClass.get(f.assetClass) ?? [];
    list.push(f);
    byClass.set(f.assetClass, list);
  }
  const out: Group[] = [];
  for (const [assetClass, rows] of byClass) {
    out.push({ assetClass, rows, worst: worstOf(rows) });
  }
  return out;
}

function rowLabel(r: QuoteFeedStatus): string {
  let label = r.exchange ?? '';
  if (r.level === 2) label = label ? `${label} (L2)` : 'Level II';
  return label;
}

export function QuoteFeedDropdown(): React.JSX.Element {
  const feeds = useQuoteFeedStore(s => s.feeds);
  const groups = groupByAssetClass(feeds);
  const worst = groups.reduce<Tone>(
    (acc, g) => (TONE_RANK[g.worst] > TONE_RANK[acc] ? g.worst : acc),
    'realtime',
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" aria-label="quote feed status">
          <Badge variant={TONE_VARIANT[worst]}>{TRIGGER_LABEL[worst]}</Badge>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        {groups.map((g, i) => (
          <React.Fragment key={g.assetClass}>
            {i > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="capitalize">{g.assetClass}</DropdownMenuLabel>
            {g.rows.map(r => (
              <DropdownMenuItem
                key={`${r.assetClass}-${r.exchange ?? ''}-${r.level ?? 1}`}
                className="flex items-center justify-between gap-3"
              >
                <span className="flex-1 truncate">{rowLabel(r) || g.assetClass}</span>
                <Badge variant={TONE_VARIANT[r.feedType]}>{TRIGGER_LABEL[r.feedType]}</Badge>
              </DropdownMenuItem>
            ))}
          </React.Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

import * as React from 'react';
import { ChevronLeft, ChevronRight, ChevronUp, ChevronDown } from 'lucide-react';
import {
  Dialog, DialogContent, DialogTitle, DialogDescription, DialogFooter, DialogClose,
} from '@/components/primitives/Dialog';
import { Button } from '@/components/primitives/Button';
import { Icon } from '@/components/primitives/Icon';
import { cn } from '@/lib/utils';
// eslint-disable-next-line boundaries/element-types -- type-only import for catalog key type
import type { WatchlistColumnKey } from '@/services/types';

export const ALL_COLUMNS: { key: WatchlistColumnKey; label: string }[] = [
  { key: 'symbol',           label: 'Symbol' },
  { key: 'description',      label: 'Description' },
  { key: 'last',             label: 'Last' },
  { key: 'change',           label: 'Change' },
  { key: 'changePct',        label: 'Change %' },
  { key: 'bid',              label: 'Bid' },
  { key: 'ask',              label: 'Ask' },
  { key: 'spread',           label: 'Spread' },
  { key: 'spreadPct',        label: 'Spread %' },
  { key: 'volume',           label: 'Volume' },
  { key: 'avgVol30d',        label: 'Avg Vol 30d' },
  { key: 'dayHigh',          label: 'Day High' },
  { key: 'dayLow',           label: 'Day Low' },
  { key: 'open',             label: 'Open' },
  { key: 'prevClose',        label: 'Prev Close' },
  { key: 'fiftyTwoWkHigh',   label: '52W High' },
  { key: 'fiftyTwoWkLow',    label: '52W Low' },
  { key: 'marketCap',        label: 'Market Cap' },
  { key: 'pe',               label: 'P/E' },
  { key: 'eps',              label: 'EPS' },
  { key: 'divYield',         label: 'Div Yield' },
  { key: 'beta',             label: 'Beta' },
  { key: 'sector',           label: 'Sector' },
  { key: 'industry',         label: 'Industry' },
  { key: 'exchange',         label: 'Exchange' },
  { key: 'assetClass',       label: 'Asset Class' },
  { key: 'nextEarningsDate', label: 'Next Earnings' },
  { key: 'ivRank',           label: 'IV Rank' },
  { key: 'optionsOI',        label: 'Options OI' },
  { key: 'newsCount24h',     label: 'News 24h' },
];

const LABEL_BY_KEY: Record<WatchlistColumnKey, string> = Object.fromEntries(
  ALL_COLUMNS.map(c => [c.key, c.label]),
) as Record<WatchlistColumnKey, string>;

export interface ColumnCustomizerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selected: WatchlistColumnKey[];
  onApply: (next: WatchlistColumnKey[]) => void;
}

export function ColumnCustomizerDialog(props: ColumnCustomizerDialogProps): React.JSX.Element {
  // Remount the inner body each time the dialog transitions open so working state
  // resets from `selected` without a setState-in-effect violation.
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      {props.open && <DialogBody key={`open-${String(props.open)}`} {...props} />}
    </Dialog>
  );
}

function DialogBody({
  onOpenChange, selected, onApply,
}: ColumnCustomizerDialogProps): React.JSX.Element {
  const [working, setWorking] = React.useState<WatchlistColumnKey[]>(selected);
  const [availableActive, setAvailableActive] = React.useState<WatchlistColumnKey | null>(null);
  const [selectedActive, setSelectedActive] = React.useState<WatchlistColumnKey | null>(null);

  const available = ALL_COLUMNS.filter(c => !working.includes(c.key));

  function add(): void {
    if (availableActive && !working.includes(availableActive)) {
      setWorking([...working, availableActive]);
      setAvailableActive(null);
    }
  }

  function remove(): void {
    if (selectedActive) {
      setWorking(working.filter(k => k !== selectedActive));
      setSelectedActive(null);
    }
  }

  function move(delta: -1 | 1): void {
    if (!selectedActive) return;
    const idx = working.indexOf(selectedActive);
    const target = idx + delta;
    if (idx < 0 || target < 0 || target >= working.length) return;
    const next = [...working];
    const [item] = next.splice(idx, 1);
    if (item === undefined) return;
    next.splice(target, 0, item);
    setWorking(next);
  }

  function apply(): void {
    onApply(working);
    onOpenChange(false);
  }

  return (
    <DialogContent className="max-w-2xl">
      <DialogTitle>Customize columns</DialogTitle>
      <DialogDescription>
        Move columns between Available and Selected. Reorder Selected with the up / down arrows.
      </DialogDescription>

      <div className="grid grid-cols-[1fr_auto_1fr] gap-4">
        <ColumnList
          title="Available"
          items={available.map(c => c.key)}
          activeKey={availableActive}
          onSelect={setAvailableActive}
        />
        <div className="flex flex-col items-center justify-center gap-2">
          <Button
            type="button"
            variant="outline"
            aria-label="add column"
            disabled={!availableActive}
            onClick={add}
          >
            <Icon as={ChevronRight} size="sm" />
          </Button>
          <Button
            type="button"
            variant="outline"
            aria-label="remove column"
            disabled={!selectedActive}
            onClick={remove}
          >
            <Icon as={ChevronLeft} size="sm" />
          </Button>
          <Button
            type="button"
            variant="outline"
            aria-label="move up"
            disabled={!selectedActive || working.indexOf(selectedActive) <= 0}
            onClick={() => move(-1)}
          >
            <Icon as={ChevronUp} size="sm" />
          </Button>
          <Button
            type="button"
            variant="outline"
            aria-label="move down"
            disabled={!selectedActive || working.indexOf(selectedActive) >= working.length - 1}
            onClick={() => move(1)}
          >
            <Icon as={ChevronDown} size="sm" />
          </Button>
        </div>
        <ColumnList
          title="Selected"
          items={working}
          activeKey={selectedActive}
          onSelect={setSelectedActive}
        />
      </div>

      <DialogFooter>
        <DialogClose asChild>
          <Button variant="outline">Cancel</Button>
        </DialogClose>
        <Button onClick={apply}>Apply</Button>
      </DialogFooter>
    </DialogContent>
  );
}

interface ColumnListProps {
  title: string;
  items: WatchlistColumnKey[];
  activeKey: WatchlistColumnKey | null;
  onSelect: (key: WatchlistColumnKey) => void;
}

function ColumnList({ title, items, activeKey, onSelect }: ColumnListProps): React.JSX.Element {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase text-fg-muted">{title}</div>
      <ul
        role="listbox"
        aria-label={title}
        className="h-64 overflow-y-auto rounded-md border border-border bg-panel p-1"
      >
        {items.length === 0 && (
          <li className="px-2 py-1 text-sm text-fg-subtle">—</li>
        )}
        {items.map(key => (
          <li
            key={key}
            role="option"
            aria-selected={activeKey === key}
            tabIndex={0}
            onClick={() => onSelect(key)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(key); } }}
            className={cn(
              'cursor-pointer rounded-sm px-2 py-1 text-sm text-fg',
              'hover:bg-elevated',
              activeKey === key && 'bg-accent-active text-primary-fg',
            )}
          >
            {LABEL_BY_KEY[key]}
          </li>
        ))}
      </ul>
    </div>
  );
}

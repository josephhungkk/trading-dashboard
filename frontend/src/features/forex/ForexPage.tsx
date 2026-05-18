import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { listPairs } from '@/services/forex/api';
import type { FxPair } from '@/services/forex/types';
import { FxTicketSection } from './FxTicketSection';

type Tab = 'pairs' | 'rate' | 'quote' | 'positions';

function PairBrowser({
  pairs,
  selected,
  onSelect,
}: {
  readonly pairs: FxPair[];
  readonly selected: FxPair | null;
  readonly onSelect: (pair: FxPair) => void;
}): React.JSX.Element {
  const [search, setSearch] = React.useState('');
  const filtered = pairs.filter(
    (p) =>
      p.canonical_id.toLowerCase().includes(search.toLowerCase()) ||
      p.base_currency.toLowerCase().includes(search.toLowerCase()) ||
      p.quote_currency.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="flex flex-col h-full">
      <div className="p-3 border-b border-border">
        <input
          type="text"
          placeholder="Search pairs…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">No pairs found.</p>
        ) : (
          filtered.map((pair) => (
            <button
              key={pair.canonical_id}
              type="button"
              onClick={() => onSelect(pair)}
              className={[
                'w-full text-left px-4 py-3 border-b border-border last:border-0',
                'hover:bg-accent transition-colors',
                selected?.canonical_id === pair.canonical_id ? 'bg-accent' : '',
              ].join(' ')}
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">
                  {pair.base_currency}/{pair.quote_currency}
                </span>
                <span className="text-xs text-muted-foreground">pip {pair.pip_size}</span>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

export function ForexPage(): React.JSX.Element {
  const [activeTab, setActiveTab] = React.useState<Tab>('pairs');
  const [selectedPair, setSelectedPair] = React.useState<FxPair | null>(null);

  const pairsQuery = useQuery({
    queryKey: ['forex', 'pairs'],
    queryFn: listPairs,
    staleTime: 60_000,
  });

  const accountsQuery = useQuery({
    queryKey: ['accounts'],
    queryFn: () => fetch('/api/accounts').then((r) => r.json()),
    staleTime: 30_000,
  });

  const pairs: FxPair[] = React.useMemo(() => pairsQuery.data ?? [], [pairsQuery.data]);
  const accountId: string | null =
    (accountsQuery.data?.accounts?.find((a: { mode: string; id: string }) => a.mode === 'paper')
      ?.id as string) ?? null;

  const activePair = selectedPair ?? pairs[0] ?? null;

  const tabs: { id: Tab; label: string }[] = [
    { id: 'pairs', label: 'Pairs' },
    { id: 'rate', label: 'Rate' },
    { id: 'quote', label: 'Quote' },
    { id: 'positions', label: 'Positions' },
  ];

  return (
    <div className="h-full flex flex-col">
      {/* Mobile tab bar */}
      <div className="md:hidden flex border-b border-border">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={[
              'flex-1 py-3 text-sm font-medium transition-colors',
              activeTab === tab.id
                ? 'border-b-2 border-primary text-primary'
                : 'text-muted-foreground hover:text-foreground',
            ].join(' ')}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Mobile panel */}
      <div className="md:hidden flex-1 overflow-hidden">
        {activeTab === 'pairs' && (
          <PairBrowser pairs={pairs} selected={activePair} onSelect={setSelectedPair} />
        )}
        {activeTab === 'rate' && (
          <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
            Rate chart coming soon
          </div>
        )}
        {activeTab === 'quote' && (
          <div className="p-4">
            {activePair && accountId ? (
              <FxTicketSection accountId={accountId} pair={activePair} />
            ) : (
              <p className="text-sm text-muted-foreground">Select a pair to get a quote.</p>
            )}
          </div>
        )}
        {activeTab === 'positions' && (
          <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
            Positions coming soon
          </div>
        )}
      </div>

      {/* Desktop 2×2 grid */}
      <div className="hidden md:grid md:grid-cols-2 md:grid-rows-2 flex-1 overflow-hidden divide-x divide-y divide-border">
        {/* Top-left: Pair browser */}
        <div className="overflow-hidden flex flex-col">
          <div className="px-4 py-2 border-b border-border">
            <h2 className="text-sm font-semibold">Pairs</h2>
          </div>
          <div className="flex-1 overflow-hidden">
            <PairBrowser pairs={pairs} selected={activePair} onSelect={setSelectedPair} />
          </div>
        </div>

        {/* Top-right: Rate chart placeholder */}
        <div className="overflow-hidden flex flex-col">
          <div className="px-4 py-2 border-b border-border">
            <h2 className="text-sm font-semibold">
              {activePair
                ? `${activePair.base_currency}/${activePair.quote_currency}`
                : 'Rate Chart'}
            </h2>
          </div>
          <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
            Rate chart coming soon
          </div>
        </div>

        {/* Bottom-left: RFQ panel */}
        <div className="overflow-y-auto p-4">
          <div className="mb-3">
            <h2 className="text-sm font-semibold">Quote</h2>
          </div>
          {activePair && accountId ? (
            <FxTicketSection accountId={accountId} pair={activePair} />
          ) : (
            <p className="text-sm text-muted-foreground">Select a pair to get a quote.</p>
          )}
        </div>

        {/* Bottom-right: Positions placeholder */}
        <div className="overflow-hidden flex flex-col">
          <div className="px-4 py-2 border-b border-border">
            <h2 className="text-sm font-semibold">Positions</h2>
          </div>
          <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
            Positions coming soon
          </div>
        </div>
      </div>
    </div>
  );
}

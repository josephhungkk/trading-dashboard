import * as React from 'react';
import { createDebouncedSearch } from '../../services/orders';
import type { ContractSummary } from '../../services/types';

export interface ContractSearchInputValue {
  conid: string;
  symbol: string;
}

interface SelectProps {
  onSelect: (contract: { conid: string; symbol: string }) => void;
  assetClass?: string;
  id?: string;
  disabled?: boolean;
}

interface LegacyProps {
  value: ContractSearchInputValue;
  onChange: (value: ContractSearchInputValue) => void;
  id?: string;
  disabled?: boolean;
}

type Props = SelectProps | LegacyProps;

type DisplayContract = ContractSummary & {
  symbol?: string;
  exchange?: string;
  asset_class?: string;
};

type SearchFn = (q: string, assetClass?: string) => Promise<ContractSummary[]>;

declare global {
  interface Window {
    __contractSearchInputSearchFactory?: () => SearchFn;
  }
}

function contractSymbol(contract: DisplayContract): string {
  return contract.symbol ?? contract.description;
}

// 5c v0.5.5: bump STK/STOCK contracts to the top so a search like "AAPL"
// surfaces the equity row above options/futures/currency variants. Stable
// ordering preserved within both partitions.
function rankContracts(contracts: DisplayContract[]): DisplayContract[] {
  const stk: DisplayContract[] = [];
  const rest: DisplayContract[] = [];
  for (const c of contracts) {
    const ac = (c.asset_class ?? '').toUpperCase();
    if (ac === 'STK' || ac === 'STOCK') stk.push(c);
    else rest.push(c);
  }
  return [...stk, ...rest];
}

function contractLabel(contract: DisplayContract): string {
  return [
    contractSymbol(contract),
    contract.exchange ?? '',
    contract.asset_class ?? '',
  ].filter(Boolean).join(' · ');
}

export function ContractSearchInput({
  ...props
}: Props): React.JSX.Element {
  const legacy = 'onChange' in props;
  const disabled = props.disabled ?? false;
  const assetClass = legacy ? undefined : props.assetClass;
  const listboxSeed = React.useId();
  const listboxId = `${listboxSeed}-contracts`;
  const rootRef = React.useRef<HTMLDivElement | null>(null);
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const controllerRef = React.useRef<AbortController | null>(null);
  const requestRef = React.useRef(0);
  const search = React.useMemo(
    () => window.__contractSearchInputSearchFactory?.() ?? createDebouncedSearch(),
    [],
  );
  const [query, setQuery] = React.useState(legacy ? props.value.symbol || props.value.conid : '');
  const [results, setResults] = React.useState<DisplayContract[]>([]);
  const [open, setOpen] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(false);
  const [fetched, setFetched] = React.useState(false);
  const [activeIndex, setActiveIndex] = React.useState(-1);

  const activeId = open && activeIndex >= 0
    ? `${listboxId}-option-${activeIndex}`
    : undefined;

  const clearTimer = React.useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const close = React.useCallback(() => {
    setOpen(false);
    setActiveIndex(-1);
  }, []);

  const selectContract = React.useCallback((contract: DisplayContract) => {
    const selected = { conid: String(contract.conid), symbol: contractSymbol(contract) };
    if (legacy) {
      props.onChange(selected);
    } else {
      props.onSelect(selected);
    }
    setQuery(selected.symbol);
    close();
  }, [close, legacy, props]);

  const selectContractFromKeyboard = React.useCallback((event: React.KeyboardEvent, contract: DisplayContract) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    selectContract(contract);
  }, [selectContract]);

  const runSearch = React.useCallback((nextQuery: string) => {
    clearTimer();
    controllerRef.current?.abort();
    setQuery(nextQuery);
    if (legacy) {
      props.onChange({ conid: nextQuery, symbol: nextQuery });
    }
    setActiveIndex(-1);
    setError(false);
    setFetched(false);

    if (nextQuery.trim() === '') {
      setResults([]);
      setLoading(false);
      close();
      return;
    }

    setOpen(true);
    setLoading(true);
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    const controller = new AbortController();
    controllerRef.current = controller;

    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      search(nextQuery, assetClass)
        .then((contracts) => {
          if (requestRef.current !== requestId || controller.signal.aborted) return;
          setResults(rankContracts(contracts as DisplayContract[]));
          setError(false);
          setFetched(true);
          setOpen(true);
        })
        .catch(() => {
          if (requestRef.current !== requestId || controller.signal.aborted) return;
          setResults([]);
          setError(true);
          setFetched(true);
          setOpen(true);
        })
        .finally(() => {
          if (requestRef.current !== requestId) return;
          setLoading(false);
        });
    }, 300);
  }, [assetClass, clearTimer, close, legacy, props, search]);

  React.useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      close();
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
    };
  }, [close]);

  React.useEffect(() => () => {
    clearTimer();
    controllerRef.current?.abort();
  }, [clearTimer]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (!open) setOpen(true);
      if (results.length > 0) {
        setActiveIndex((current) => Math.min(current + 1, results.length - 1));
      }
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (results.length > 0) {
        setActiveIndex((current) => Math.max(current - 1, 0));
      }
    } else if (event.key === 'Enter') {
      if (activeIndex >= 0) {
        const contract = results[activeIndex];
        if (contract !== undefined) {
          event.preventDefault();
          selectContract(contract);
        }
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  };

  return (
    <div ref={rootRef} className="relative w-full">
      <input
        role="combobox"
        id={props.id}
        type="text"
        value={query}
        disabled={disabled}
        aria-expanded={open}
        aria-activedescendant={activeId}
        aria-controls={listboxId}
        aria-autocomplete="list"
        autoComplete="off"
        onChange={(event) => { runSearch(event.target.value); }}
        onKeyDown={onKeyDown}
        className="w-full rounded-md border border-border bg-panel pb-2 pl-3 pr-3 pt-2 text-sm text-fg outline-none transition focus:border-accent disabled:cursor-not-allowed disabled:opacity-60"
      />
      {open ? (
        <ul
          id={listboxId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-50 mt-1 max-h-[16rem] overflow-auto rounded-md border border-border bg-panel py-1 text-sm text-fg shadow-lg"
        >
          {loading ? (
            <li className="pb-2 pl-3 pr-3 pt-2 text-muted">Searching...</li>
          ) : null}
          {!loading && error ? (
            <li className="pb-2 pl-3 pr-3 pt-2 text-danger">Search failed; retry</li>
          ) : null}
          {!loading && !error && fetched && results.length === 0 ? (
            <li className="pb-2 pl-3 pr-3 pt-2 text-muted">No matches</li>
          ) : null}
          {!loading && !error
            ? results.map((contract, index) => (
              <li
                id={`${listboxId}-option-${index}`}
                key={`${contract.conid}-${index}`}
                role="option"
                aria-selected={activeIndex === index}
                onMouseDown={(event) => { event.preventDefault(); }}
                onClick={() => { selectContract(contract); }}
                onKeyDown={(event) => { selectContractFromKeyboard(event, contract); }}
                className={[
                  'cursor-pointer pb-2 pl-3 pr-3 pt-2',
                  activeIndex === index ? 'bg-accent text-accent-foreground' : 'hover:bg-muted',
                ].join(' ')}
              >
                {contractLabel(contract)}
              </li>
            ))
            : null}
        </ul>
      ) : null}
    </div>
  );
}

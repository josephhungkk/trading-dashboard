// frontend/src/features/orders/AlgoSection.tsx
import * as React from 'react';
import { getAlgoCapabilities } from '@/services/algo/api';
import type { AlgoCapabilityEntry, AlgoOrderFields, AlgoStrategy } from '@/services/algo/types';
import { DISPLAY_ALGOS } from '@/services/algo/types';

interface Props {
  brokerId: string;
  assetClass: string;
  onAlgoChange: (fields: AlgoOrderFields | null) => void;
}

export function AlgoSection({ brokerId, assetClass, onAlgoChange }: Props): React.JSX.Element | null {
  const [open, setOpen] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [strategies, setStrategies] = React.useState<AlgoCapabilityEntry[]>([]);
  const [selectedStrategy, setSelectedStrategy] = React.useState<AlgoStrategy | null>(null);
  const [params, setParams] = React.useState<Record<string, string>>({});

  React.useEffect(() => {
    let cancelled = false;
    getAlgoCapabilities(brokerId, assetClass)
      .then((res) => {
        if (!cancelled) {
          setStrategies(Array.isArray(res.strategies) ? res.strategies : []);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setStrategies([]);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [brokerId, assetClass]);

  // Hidden when no strategies available.
  if (!loading && strategies.length === 0) return null;

  const selectedEntry = strategies.find((s) => s.strategy === selectedStrategy) ?? null;
  const isDisplayAlgo = selectedStrategy != null && DISPLAY_ALGOS.has(selectedStrategy);
  const isExecutionAlgo = selectedStrategy != null && !DISPLAY_ALGOS.has(selectedStrategy);

  function handleStrategyChange(strategy: AlgoStrategy | ''): void {
    if (strategy === '') {
      setSelectedStrategy(null);
      setParams({});
      onAlgoChange(null);
      return;
    }
    const s = strategy as AlgoStrategy;
    setSelectedStrategy(s);
    setParams({});
    // Notify parent immediately so order-type coercion fires on selection,
    // before any params are filled in.
    onAlgoChange({ algo_strategy: s, algo_params: {} });
  }

  function handleParamChange(name: string, value: string): void {
    const next = { ...params, [name]: value };
    setParams(next);
    if (selectedStrategy) {
      onAlgoChange({ algo_strategy: selectedStrategy, algo_params: next });
    }
  }

  if (loading) {
    return (
      <div
        className="animate-pulse h-8 rounded bg-neutral-200"
        aria-label="Loading algo capabilities"
      />
    );
  }

  return (
    <div className="border rounded p-2 text-sm">
      <button
        type="button"
        className="w-full text-left font-medium"
        onClick={() => setOpen((o) => !o)}
      >
        Algo Execution — {selectedStrategy ?? 'Off'}
      </button>

      {open && (
        <div className="mt-2 space-y-2">
          <label className="block">
            <span className="text-xs text-neutral-500">Strategy</span>
            <select
              className="block w-full mt-0.5"
              value={selectedStrategy ?? ''}
              onChange={(e) => handleStrategyChange(e.currentTarget.value as AlgoStrategy | '')}
            >
              <option value="">— Off —</option>
              {strategies.map((s) => (
                <option key={s.strategy} value={s.strategy}>{s.strategy}</option>
              ))}
            </select>
          </label>

          {isDisplayAlgo && (
            <p className="text-xs text-amber-600">Order type forced to LIMIT for this strategy.</p>
          )}
          {isExecutionAlgo && (
            <p className="text-xs text-blue-600">Order type forced to MARKET for this strategy.</p>
          )}

          {selectedEntry?.params.map((param) => (
            <label key={param.name} className="block">
              <span className="text-xs text-neutral-500">
                {param.name}{param.required ? ' *' : ''}
              </span>
              {param.type === 'enum' && param.values ? (
                <select
                  className="block w-full mt-0.5"
                  value={params[param.name] ?? ''}
                  onChange={(e) => handleParamChange(param.name, e.currentTarget.value)}
                >
                  <option value="">—</option>
                  {param.values.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              ) : param.type === 'boolean' ? (
                <input
                  type="checkbox"
                  checked={params[param.name] === 'true'}
                  onChange={(e) => handleParamChange(param.name, e.currentTarget.checked ? 'true' : 'false')}
                />
              ) : param.type === 'time' ? (
                <input
                  type="time"
                  className="block mt-0.5"
                  value={params[param.name] ?? ''}
                  onChange={(e) => handleParamChange(param.name, e.currentTarget.value)}
                />
              ) : (
                <input
                  type="text"
                  className="block w-full mt-0.5"
                  value={params[param.name] ?? ''}
                  onChange={(e) => handleParamChange(param.name, e.currentTarget.value)}
                  placeholder="0.00"
                />
              )}
            </label>
          ))}

          {isDisplayAlgo && selectedStrategy && (
            <p className="text-xs text-neutral-400">
              Display size must be &gt; 0 and less than order quantity.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

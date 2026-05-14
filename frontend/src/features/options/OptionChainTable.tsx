import * as React from 'react';
import type { OptionChainData, OptionChainRow } from './types';

interface Props {
  data: OptionChainData;
  spot: number | null;
  onSelectStrike?: (row: OptionChainRow, side: 'call' | 'put') => void;
}

function isAtm(strike: string, spot: number | null): boolean {
  if (spot === null) return false;
  const s = parseFloat(strike);
  return Math.abs(s - spot) <= spot * 0.002;
}

function isItmCall(strike: string, spot: number | null): boolean {
  if (spot === null) return false;
  return parseFloat(strike) < spot;
}

function isItmPut(strike: string, spot: number | null): boolean {
  if (spot === null) return false;
  return parseFloat(strike) > spot;
}

export function OptionChainTable({ data, spot, onSelectStrike }: Props) {
  const strikeMap = React.useMemo(() => {
    const m = new Map<string, { call?: OptionChainRow; put?: OptionChainRow }>();
    for (const row of data.calls) {
      m.set(row.strike, { ...m.get(row.strike), call: row });
    }
    for (const row of data.puts) {
      m.set(row.strike, { ...m.get(row.strike), put: row });
    }
    return m;
  }, [data.calls, data.puts]);

  const strikes = React.useMemo(
    () => [...strikeMap.keys()].sort((a, b) => parseFloat(a) - parseFloat(b)),
    [strikeMap],
  );

  return (
    <div className="overflow-x-auto">
      {/* Desktop butterfly table */}
      <table className="hidden md:table w-full border-collapse text-xs min-w-[36rem]">
        <thead>
          <tr className="text-muted-foreground border-b border-border">
            <th className="text-right p-1 text-green-400">Bid</th>
            <th className="text-right p-1 text-green-400">Ask</th>
            <th className="text-right p-1 text-green-400">IV</th>
            <th className="text-right p-1 text-green-400">Δ</th>
            <th className="text-right p-1 text-green-400">OI</th>
            <th className="text-center p-1 font-bold bg-white/5">Strike</th>
            <th className="text-left p-1 text-red-400">OI</th>
            <th className="text-left p-1 text-red-400">Δ</th>
            <th className="text-left p-1 text-red-400">IV</th>
            <th className="text-left p-1 text-red-400">Bid</th>
            <th className="text-left p-1 text-red-400">Ask</th>
          </tr>
        </thead>
        <tbody>
          {strikes.map((strike) => {
            const entry = strikeMap.get(strike) ?? {};
            const atm = isAtm(strike, spot);
            const itmCall = isItmCall(strike, spot);
            const itmPut = isItmPut(strike, spot);

            return (
              <tr
                key={strike}
                className={`cursor-pointer transition-colors ${
                  atm
                    ? 'bg-yellow-400/10 outline outline-1 outline-yellow-400/40 hover:bg-yellow-400/20'
                    : itmCall
                      ? 'bg-green-400/6 hover:bg-green-400/13'
                      : itmPut
                        ? 'bg-red-400/4 hover:bg-red-400/12'
                        : 'hover:bg-white/5'
                }`}
                data-testid={`chain-row-${strike}`}
              >
                {/* Call side */}
                <td
                  className="text-right p-1 cursor-pointer"
                  onClick={() => entry.call && onSelectStrike?.(entry.call, 'call')}
                >
                  {entry.call?.bid ?? '—'}
                </td>
                <td
                  className="text-right p-1"
                  onClick={() => entry.call && onSelectStrike?.(entry.call, 'call')}
                >
                  {entry.call?.ask ?? '—'}
                </td>
                <td className="text-right p-1 text-muted-foreground">
                  {entry.call ? `${(entry.call.iv * 100).toFixed(1)}%` : '—'}
                </td>
                <td className="text-right p-1">{entry.call?.delta.toFixed(2) ?? '—'}</td>
                <td className="text-right p-1 text-muted-foreground">
                  {entry.call ? (entry.call.open_interest / 1000).toFixed(1) + 'k' : '—'}
                </td>
                {/* Strike */}
                <td
                  className={`text-center p-1 font-semibold bg-white/4 ${atm ? 'text-yellow-400' : ''}`}
                  data-atm={atm ? 'true' : undefined}
                >
                  {strike}
                  {atm ? ' ★' : ''}
                </td>
                {/* Put side */}
                <td className="text-left p-1 text-muted-foreground">
                  {entry.put ? (entry.put.open_interest / 1000).toFixed(1) + 'k' : '—'}
                </td>
                <td className="text-left p-1">{entry.put?.delta.toFixed(2) ?? '—'}</td>
                <td className="text-left p-1 text-muted-foreground">
                  {entry.put ? `${(entry.put.iv * 100).toFixed(1)}%` : '—'}
                </td>
                <td
                  className="text-left p-1 cursor-pointer"
                  onClick={() => entry.put && onSelectStrike?.(entry.put, 'put')}
                >
                  {entry.put?.bid ?? '—'}
                </td>
                <td
                  className="text-left p-1"
                  onClick={() => entry.put && onSelectStrike?.(entry.put, 'put')}
                >
                  {entry.put?.ask ?? '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Mobile single-column list */}
      <div className="md:hidden divide-y divide-border">
        {strikes.map((strike) => {
          const entry = strikeMap.get(strike) ?? {};
          const atm = isAtm(strike, spot);
          return (
            <div
              key={strike}
              className={`p-2 ${atm ? 'bg-yellow-400/10' : ''}`}
              data-testid={`chain-row-mobile-${strike}`}
            >
              <div className="flex justify-between items-center">
                <span className={`font-semibold ${atm ? 'text-yellow-400' : ''}`}>
                  {strike}
                  {atm ? ' ★' : ''}
                </span>
                <div className="flex gap-3 text-xs text-muted-foreground">
                  {entry.call && (
                    <span>
                      C IV {(entry.call.iv * 100).toFixed(1)}% Δ{entry.call.delta.toFixed(2)}
                    </span>
                  )}
                  {entry.put && (
                    <span>
                      P IV {(entry.put.iv * 100).toFixed(1)}% Δ{entry.put.delta.toFixed(2)}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex gap-2 mt-1">
                {entry.call && (
                  <button
                    className="text-xs rounded border border-green-400/40 px-2 py-0.5"
                    onClick={() => { if (entry.call) onSelectStrike?.(entry.call, 'call'); }}
                  >
                    Call {entry.call.bid}/{entry.call.ask}
                  </button>
                )}
                {entry.put && (
                  <button
                    className="text-xs rounded border border-red-400/40 px-2 py-0.5"
                    onClick={() => { if (entry.put) onSelectStrike?.(entry.put, 'put'); }}
                  >
                    Put {entry.put.bid}/{entry.put.ask}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

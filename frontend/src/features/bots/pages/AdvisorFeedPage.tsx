import * as React from 'react';
import { useAdvisorFeedStream } from '../hooks/useAdvisorFeedStream';
import type { AdvisorVerdict, AdvisorWsFrame } from '../../../services/advisor/types';

type VerdictFilter = 'all' | AdvisorVerdict;

const FILTERS: VerdictFilter[] = ['all', 'approve', 'veto', 'fail_open'];

function createdAt(frame: AdvisorWsFrame): string {
  return frame.created_at ?? frame.ts ?? '';
}

function reasoningPreview(frame: AdvisorWsFrame): string {
  const text = frame.reasoning_preview ?? frame.reasoning ?? '';
  return text.length > 80 ? `${text.slice(0, 80)}...` : text;
}

export function AdvisorFeedPage(): React.JSX.Element {
  const { frames, isConnected } = useAdvisorFeedStream();
  const [filter, setFilter] = React.useState<VerdictFilter>('all');
  const visibleFrames = filter === 'all' ? frames : frames.filter((frame) => frame.verdict === filter);

  return (
    <main className="p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Advisor feed</h1>
          <p className="text-sm text-muted-foreground">Live advisor decisions across bots</p>
        </div>
        <span
          className={`rounded px-2 py-1 text-xs ${
            isConnected ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
          }`}
        >
          {isConnected ? 'Connected' : 'Disconnected'}
        </span>
      </div>

      <label className="mb-3 flex max-w-xs flex-col gap-1 text-sm" htmlFor="advisor-feed-filter">
        <span className="text-muted-foreground">Verdict filter</span>
        <select
          id="advisor-feed-filter"
          value={filter}
          onChange={(event) => setFilter(event.target.value as VerdictFilter)}
          className="rounded border border-border bg-background px-3 py-2 text-sm"
        >
          {FILTERS.map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
      </label>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="py-2 pr-3 font-medium">Bot</th>
              <th className="py-2 pr-3 font-medium">Canonical ID</th>
              <th className="py-2 pr-3 font-medium">Verdict</th>
              <th className="py-2 pr-3 font-medium">Reasoning</th>
              <th className="py-2 pr-3 font-medium">Created</th>
            </tr>
          </thead>
          <tbody>
            {visibleFrames.map((frame) => (
              <tr key={`${frame.bot_id}-${frame.decision_id}`} className="border-b">
                <td className="py-2 pr-3">{frame.bot_id}</td>
                <td className="py-2 pr-3">{frame.canonical_id}</td>
                <td className="py-2 pr-3">{frame.verdict}</td>
                <td className="py-2 pr-3">{reasoningPreview(frame)}</td>
                <td className="py-2 pr-3">{createdAt(frame)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}

import * as React from 'react';
import type { AdvisorDecision } from '../../../services/advisor/types';

interface Props {
  decision: AdvisorDecision | null;
  onClose: () => void;
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function AdvisorDecisionDrawer({ decision, onClose }: Props): React.JSX.Element | null {
  React.useEffect(() => {
    if (decision == null) return undefined;

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') onClose();
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [decision, onClose]);

  if (decision == null) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="advisor-decision-title"
        className="h-full w-full max-w-xl overflow-y-auto border-l border-border bg-background p-4 shadow-xl"
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id="advisor-decision-title" className="text-lg font-semibold">
              Advisor decision
            </h2>
            <p className="text-sm text-muted-foreground">{decision.canonical_id}</p>
          </div>
          <button type="button" onClick={onClose} className="btn-secondary text-xs">
            Close
          </button>
        </div>

        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-muted-foreground">Verdict</dt>
            <dd className="font-medium">{decision.verdict}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Confidence</dt>
            <dd>{decision.confidence == null ? 'N/A' : `${Math.round(decision.confidence * 100)}%`}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Latency</dt>
            <dd>{decision.latency_ms} ms</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Mode</dt>
            <dd>{decision.effective_mode}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Provider</dt>
            <dd>{decision.provider ?? 'N/A'}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Model</dt>
            <dd>{decision.model ?? 'N/A'}</dd>
          </div>
        </dl>

        <div className="mt-4 space-y-4">
          <section>
            <h3 className="mb-1 text-sm font-semibold">Reasoning</h3>
            <p className="whitespace-pre-wrap text-sm">{decision.reasoning}</p>
          </section>

          <section>
            <h3 className="mb-2 text-sm font-semibold">Advice tags</h3>
            <div className="flex flex-wrap gap-2">
              {decision.advice_tags.length === 0 ? (
                <span className="text-sm text-muted-foreground">None</span>
              ) : (
                decision.advice_tags.map((tag) => (
                  <span key={tag} className="rounded bg-muted px-2 py-1 text-xs">
                    {tag}
                  </span>
                ))
              )}
            </div>
          </section>

          <section>
            <h3 className="mb-1 text-sm font-semibold">Intent</h3>
            <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
              {formatJson(decision.intent)}
            </pre>
          </section>

          <section>
            <h3 className="mb-1 text-sm font-semibold">Context summary</h3>
            <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
              {formatJson(decision.context_summary)}
            </pre>
          </section>
        </div>
      </section>
    </div>
  );
}

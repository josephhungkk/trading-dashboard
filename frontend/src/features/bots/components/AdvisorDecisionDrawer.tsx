import * as React from 'react';
import { patchAdvisorDecisionOverride } from '@/services/advisor/api';
import { mintCsrfNonce } from '../../../services/admin/api';
import type { AdvisorDecision } from '../../../services/advisor/types';

interface Props {
  decision: AdvisorDecision | null;
  isAdmin: boolean;
  onClose: () => void;
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function AdvisorDecisionDrawer({ decision, isAdmin, onClose }: Props): React.JSX.Element | null {
  const sectionRef = React.useRef<HTMLDivElement>(null);
  const onCloseRef = React.useRef(onClose);
  React.useEffect(() => { onCloseRef.current = onClose; });

  React.useEffect(() => {
    if (decision == null) return undefined;

    sectionRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') onCloseRef.current();
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [decision]);

  if (decision == null) return null;

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- decorative backdrop; keyboard dismiss handled via Escape on the dialog
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/30"
      onClick={onClose}
    >
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- role=dialog is interactive; stopPropagation prevents backdrop close on inner click */}
      <div
        ref={sectionRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="advisor-decision-title"
        className="h-full w-full max-w-xl overflow-y-auto border-l border-border bg-background p-4 shadow-xl outline-none"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id="advisor-decision-title" className="text-lg font-semibold">
              Advisor decision
            </h2>
            <p className="text-sm text-muted-foreground">{decision.canonical_id}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close advisor decision"
            className="btn-secondary text-xs"
          >
            Close
          </button>
        </div>

        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-muted-foreground">Verdict</dt>
            <dd className="font-medium">{decision.verdict}</dd>
          </div>
          {decision.overridden_at != null ? (
            <div className="col-span-2 rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
              <p className="font-medium">Override recorded</p>
              <p>By {decision.overridden_by ?? 'unknown'} at {new Date(decision.overridden_at).toLocaleString()}</p>
              <p>Action: {decision.override_action ?? 'N/A'}</p>
              <p className="whitespace-pre-wrap">Reason: {decision.override_reason ?? 'N/A'}</p>
            </div>
          ) : isAdmin ? (
            <div className="col-span-2">
              <OverrideButton decision={decision} />
            </div>
          ) : null}
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
      </div>
    </div>
  );
}

function OverrideButton({ decision }: { decision: AdvisorDecision }): React.JSX.Element {
  const [submitting, setSubmitting] = React.useState(false);
  const [done, setDone] = React.useState(false);
  const [reason, setReason] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);

  async function handleOverride(): Promise<void> {
    const trimmed = reason.trim();
    if (trimmed === '') return;
    setSubmitting(true);
    setError(null);
    try {
      const nonce = await mintCsrfNonce();
      await patchAdvisorDecisionOverride(
        decision.bot_id,
        decision.id,
        { override_action: 'approve', override_reason: trimmed },
        nonce,
      );
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Override failed. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <p className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
        Override recorded for audit purposes. The original order was not re-submitted.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <textarea
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="Override reason required"
        maxLength={500}
        rows={2}
        className="w-full rounded border border-border bg-background px-3 py-2 text-sm"
      />
      {error != null && (
        <p role="alert" className="text-xs text-destructive">{error}</p>
      )}
      <button
        type="button"
        aria-label="Override (audit only)"
        onClick={() => void handleOverride()}
        disabled={submitting || reason.trim() === ''}
        className="btn-secondary text-xs"
      >
        {submitting ? 'Submitting…' : 'Override (audit only)'}
      </button>
    </div>
  );
}
